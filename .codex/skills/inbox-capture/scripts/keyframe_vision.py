#!/usr/bin/env python3
"""UI 演示视频的画面解析：关键帧提取 + 视觉模型分析（issue #1 批次 5，设计稿子设计四）。

职责：
1. 扫描收件箱中「作品类型=UI演示视频 且 画面解析 ∈ {未开始, 失败}」的笔记，ffmpeg 抽关键帧
   （场景切换 + 每 30 秒兜底，上限 12 帧，长边 1280px），送本地 Qwen3-VL（llama-server 8304
   专用实例）或 GLM API 生成「逐帧观察 / 推断 / 流程总结」，回写笔记「## 画面解析」小节与模态覆盖。
2. UI 演示视觉确认（决策 10）：口播视频/未知类型且关键词命中过的笔记抽 2 帧问「这是否软件界面/
   操作演示录屏」，是 → 作品类型升级为 UI演示视频（来源=视觉确认）；人工 > 视觉确认 > 启发式。
3. 降级链：local 失败 → glm（若配置）→ --allow-ocr-fallback 时 PaddleOCR-VL 仅观察降级。
4. 媒体复用 segmented_transcribe 的缓存/重取逻辑，解析完成后清理（决策 1）。

本脚本仅用 Python 标准库；视觉模型经 OpenAI 兼容 /v1/chat/completions 调用。

配置 ~/.config/my-mind/vision.json（建议 chmod 600；无密钥可 644；文件缺失全部用默认值）：
    {"backend": "local", "local": {"base_url": "...", "model": "..."},
     "glm": {"base_url": "...", "api_key": "...", "model": "glm-4.6v"},
     "frame_max": 12, "frame_long_edge": 1280}
glm.api_key 的任何片段不得进入日志、错误消息或笔记（对齐 dtk/xhs 客户端的 redact 模式）。

CLI 契约：
    python3 .codex/skills/inbox-capture/scripts/keyframe_vision.py \
        [--note <收件箱笔记路径>] [--max-notes 3] [--dry-run] [--backend local|glm] \
        [--skip-confirm] [--allow-ocr-fallback]
退出码：0=正常运行（无论是否全部成功）；1=硬错误（配置损坏、--note 指向的笔记不存在等）。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# 复用批次 4 的媒体缓存/重取与笔记保真读写（同目录纯标准库模块）。
import segmented_transcribe as segmented  # noqa: E402

TZ: ZoneInfo = segmented.TZ
ROOT: Path = segmented.ROOT
DEFAULT_INBOX: Path = segmented.DEFAULT_INBOX
RUN_RECORD_DIR: Path = segmented.RUN_RECORD_DIR
# L8：KEYFRAME_VISION_COMMAND 唯一定义在 capture_link.py（CLI 契约与入口脚本同处），
# segmented_transcribe/keyframe_vision 不再各留一份字面量。

# —— 设计稿子设计四 + 实测结论的集中常量（阈值调整只改这里） ——
VISION_CONFIG_PATH = Path.home() / ".config" / "my-mind" / "vision.json"
DEFAULT_BACKEND = "local"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8304"  # 专用 llama-server（--ctx-size 40960 --parallel 1）；不要写 8303（每槽仅 8k 上下文）
DEFAULT_LOCAL_MODEL = "Qwen3-VL-8B-GGUF"
DEFAULT_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"  # 官方端点 .../paas/v4/chat/completions（由 chat_completions_url 拼出）
DEFAULT_GLM_MODEL = "glm-4.6v"
DEFAULT_FRAME_MAX = 12
DEFAULT_FRAME_LONG_EDGE = 1280
# 实测有效的抽帧滤镜：场景切换(>0.3) + 首帧 + 距上一选中帧 ≥30 秒兜底。
SCENE_SELECT_FILTER = "select='gt(scene,0.3)+isnan(prev_selected_t)+gte(t-prev_selected_t,30)'"
FRAME_JPEG_QUALITY = 4  # ffmpeg mjpeg 的 -q:v（2=最好，31=最差），q=4 实测文字可读且体积小
FRAME_FALLBACK_SECONDS = 30
FRAME_TOKENS_AT_1280 = 2850  # 实测：1280px 一帧 ≈2850 token
CONTEXT_TOKEN_BUDGET = 40000  # 请求整体需 <40k（8304 实例 ctx 40960）
CONTEXT_TOKEN_RESERVE = 4500  # prompt 文本 + max_tokens 3000 + 余量
COMPRESSED_LONG_EDGE = 1024  # 超预算时的压档长边
CONFIRM_FRAME_COUNT = 2  # 视觉确认只抽 2 帧
VLM_TEMPERATURE = 0.2
VLM_MAX_TOKENS = 3000
VLM_CHAT_TIMEOUT_SECONDS = 600
HEALTH_TIMEOUT_SECONDS = 5
MIN_ANALYSIS_CHARS = 100  # 空白/过短输出视为异常（防零帧输入时的自信脑补）
FFMPEG_TIMEOUT_SECONDS = segmented.FFMPEG_TIMEOUT_SECONDS
MAX_VIDEO_BYTES = segmented.MAX_VIDEO_BYTES

STATUS_PENDING = "未开始"
STATUS_FAILED = "失败"
STATUS_OCR_FALLBACK = "仅OCR降级"
STATUS_NOT_APPLICABLE = "不适用"
WORK_TYPE_UI = "UI演示视频"
TYPE_SOURCE_MANUAL = "人工"
TYPE_SOURCE_VISION = "视觉确认"
TYPE_SOURCE_HEURISTIC = "启发式"
SUPPORTED_PLATFORMS = ("抖音", "小红书")
# L6：replace_vision_section 的替换边界只认这些真实小节标题（capture_link.build_note 会写出的固定集合），
# 防止旧「画面解析」内容里夹带的 "## " 行把边界提前截断留下残留。
KNOWN_SECTION_HEADINGS = (
    "## 基础信息",
    "## 质量门禁",
    "## 解析说明",
    "## 平台信息",
    "## 原始图片素材",
    "## 媒体校验失败",
    "## 文案摘录",
    "## 简介摘录",
    "## 图片文字 OCR",
    "## 图片文字 OCR 失败",
    "## 视频内容摘录",
    "## 视频内容摘录失败",
    "## YouTube 字幕解析",
    "## YouTube 字幕解析失败",
    "## 官方转录来源",
    "## 章节目录",
    "## 外部转录解析失败",
    "## 标签和分类",
    "## 字幕可用性",
    "## 为什么保存",
    "## 初步想法",
    "## 阅读思考",
    "## 后续处理建议",
    "## 原始链接",
)

TIME_MENTION_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:s|秒)")
PTS_TIME_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")

VISION_ANALYSIS_PROMPT = (
    "你是 UI 演示视频的画面解析助手。下面按时间顺序给出同一条视频的若干关键帧，"
    "每帧前面有形如 [12.3s] 的时间戳标记。请用中文分三部分输出：\n"
    "1. 逐帧观察：按时间戳逐帧列出画面中可见的文字与界面元素事实（窗口、按钮、菜单、终端命令、"
    "弹窗、标注框等），只描述看得见的内容，看不清的写「看不清」，不要补全或臆测看不见的东西。\n"
    "2. 推断：单独成段，每条以「推断：」开头，解释界面变化与操作意图。\n"
    "3. 流程总结：用 3-5 句话概括整体操作流程。"
)

VISION_CONFIRM_PROMPT = (
    "下面是同一条视频的 2 个关键帧。请判断：这是否软件界面/操作演示录屏？"
    "请只回答「是」「否」或「不确定」三选一，不要解释。"
)

_capture_link_module: Any | None = None


class VisionConfigError(Exception):
    """vision.json 损坏等启动期不可恢复问题。"""


def _capture_link():
    """按需加载 capture_link（复用 UI 演示关键词启发式与 PaddleOCR-VL 降级档）；失败抛中文错误。"""
    global _capture_link_module
    if _capture_link_module is None:
        try:
            import capture_link  # noqa: PLC0415 - 按需加载，避免正常路径背上 6000 行模块
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法加载 capture_link 模块（画面解析依赖其启发式/OCR 函数）：{exc!r}") from exc
        _capture_link_module = capture_link
    return _capture_link_module


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def today_date() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d")


def redact_secrets(text: str, secrets: list[str]) -> str:
    """与 dtk/xhs 客户端同款 redact：任何密钥片段替换为 ***，保证不进日志/错误/笔记。"""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def default_config() -> dict[str, Any]:
    """默认配置（测试与缺省路径共用；返回新字典避免共享可变状态）。"""
    return {
        "backend": DEFAULT_BACKEND,
        "local": {"base_url": DEFAULT_LOCAL_BASE_URL, "model": DEFAULT_LOCAL_MODEL},
        "glm": {"base_url": DEFAULT_GLM_BASE_URL, "api_key": "", "model": DEFAULT_GLM_MODEL},
        "frame_max": DEFAULT_FRAME_MAX,
        "frame_long_edge": DEFAULT_FRAME_LONG_EDGE,
    }


def load_vision_config(path: Path | None = None) -> dict[str, Any]:
    """读取 ~/.config/my-mind/vision.json；文件缺失全部用默认值，损坏抛 VisionConfigError。"""
    config = default_config()
    config_path = path if path is not None else VISION_CONFIG_PATH
    if not config_path.is_file():
        return config
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisionConfigError(f"画面解析配置文件不可读或不是合法 JSON：{config_path}（{exc}）。") from exc
    if not isinstance(data, dict):
        raise VisionConfigError(f"画面解析配置文件顶层必须是 JSON 对象：{config_path}。")
    backend = str(data.get("backend") or "").strip().lower()
    if backend and backend not in {"local", "glm"}:
        raise VisionConfigError(f"画面解析配置 backend 仅支持 local/glm，当前为 {backend!r}。")
    if backend:
        config["backend"] = backend
    for section in ("local", "glm"):
        raw = data.get(section)
        if isinstance(raw, dict):
            for key in ("base_url", "model", "api_key"):
                value = str(raw.get(key) or "").strip()
                if value:
                    config[section][key] = value
    try:
        frame_max = int(data.get("frame_max") or config["frame_max"])
        frame_long_edge = int(data.get("frame_long_edge") or config["frame_long_edge"])
    except (TypeError, ValueError) as exc:
        raise VisionConfigError(f"画面解析配置 frame_max / frame_long_edge 必须是整数（{exc}）。") from exc
    config["frame_max"] = max(1, min(50, frame_max))
    config["frame_long_edge"] = max(256, min(4096, frame_long_edge))
    return config


def glm_configured(config: dict[str, Any]) -> bool:
    """glm 备用后端可用性：base_url 有默认值，关键看是否配置了 api_key。"""
    return bool(config["glm"].get("base_url") and config["glm"].get("api_key"))


@dataclass
class BackendSpec:
    name: str  # local / glm
    base_url: str
    model: str
    api_key: str = ""

    @property
    def label(self) -> str:
        return f"{self.name}（{self.model}）"


def backend_specs(config: dict[str, Any]) -> list[BackendSpec]:
    """按降级链排序的后端列表：配置的主后端在前，另一后端可用时跟其后。"""
    primary = str(config.get("backend") or DEFAULT_BACKEND)
    specs: dict[str, BackendSpec] = {
        "local": BackendSpec(
            name="local",
            base_url=str(config["local"].get("base_url") or DEFAULT_LOCAL_BASE_URL),
            model=str(config["local"].get("model") or DEFAULT_LOCAL_MODEL),
        ),
        "glm": BackendSpec(
            name="glm",
            base_url=str(config["glm"].get("base_url") or DEFAULT_GLM_BASE_URL),
            model=str(config["glm"].get("model") or DEFAULT_GLM_MODEL),
            api_key=str(config["glm"].get("api_key") or ""),
        ),
    }
    order = [primary]
    for name in ("local", "glm"):
        if name not in order:
            if name == "glm" and not glm_configured(config):
                continue  # glm 未配置 api_key 时不进降级链（不配置就只用本地）
            order.append(name)
    return [specs[name] for name in order]


# ---------------------------------------------------------------------------
# 纯函数：抽帧命令 / 时间戳 / 预算 / 输出防呆（测试重点覆盖）
# ---------------------------------------------------------------------------


def build_extract_command(media_path: Path, out_pattern: str, *, long_edge: int) -> list[str]:
    """构造 ffmpeg 抽帧命令（场景切换 + 每 30 秒兜底 + 长边压缩 + showinfo 时间戳）。

    -fps_mode passthrough 必须显式给：ffmpeg 8 默认输出同步会把 select 丢掉的帧用复制帧
    补回 CFR 时间轴（实测 dup=86 drop=0），导致写出大量重复帧、时间戳配对错乱。
    scale 用条件表达式保证「长边」语义：横屏限宽、竖屏限高（评审 H1：固定宽会把 9:16 竖屏
    放大到 1280×2276，单帧 ~9000 token 击穿上下文预算）。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法抽取关键帧。")
    scale_filter = f"scale='if(gt(iw,ih),{long_edge},-2)':'if(gt(iw,ih),-2,{long_edge})'"
    video_filter = f"{SCENE_SELECT_FILTER},{scale_filter},showinfo"
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(media_path),
        "-vf",
        video_filter,
        "-fps_mode",
        "passthrough",
        "-q:v",
        str(FRAME_JPEG_QUALITY),
        "-start_number",
        "0",
        out_pattern,
    ]


def parse_pts_times(ffmpeg_log: str) -> list[float]:
    """按出现顺序提取 showinfo 的 pts_time 时间戳（秒）。"""
    return [float(match.group(1)) for match in PTS_TIME_RE.finditer(ffmpeg_log or "")]


def uniform_sample(items: list[Any], limit: int) -> list[Any]:
    """超出上限时均匀抽样（保留首尾，覆盖整段时间轴）。"""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    positions = []
    for index in range(limit):
        position = round(index * (len(items) - 1) / (limit - 1))
        if not positions or position > positions[-1]:
            positions.append(position)
    return [items[position] for position in positions]


def fit_frames_budget(
    frame_count: int,
    long_edge: int,
    *,
    budget: int = CONTEXT_TOKEN_BUDGET,
    reserve: int = CONTEXT_TOKEN_RESERVE,
) -> tuple[int, int, str]:
    """按上下文预算动态校验：超限先降帧数，帧数触底（≤2 帧）仍超限再压到 1024px。

    返回 (帧数, 长边, 备注)；备注说明降档原因（写入笔记 画面解析备注）。
    """
    available = budget - reserve

    def per_frame(edge: int) -> float:
        return FRAME_TOKENS_AT_1280 * (edge / 1280.0) ** 2

    count, edge = frame_count, long_edge
    if count * per_frame(edge) <= available:
        return count, edge, ""
    allowed = int(available // per_frame(edge))
    if allowed >= 2:
        candidate = min(count, allowed)
        if candidate * per_frame(edge) <= available:
            return candidate, edge, f"帧数按上下文预算（约 {available} token）由 {frame_count} 帧降为 {candidate} 帧"
    if edge > COMPRESSED_LONG_EDGE:
        # 降到 2 帧仍超限（长边过大）：压到 1024px 再按预算重定帧数。
        edge = COMPRESSED_LONG_EDGE
        allowed = max(1, int(available // per_frame(edge)))
        count = min(frame_count, allowed)
        return count, edge, f"长边压到 {COMPRESSED_LONG_EDGE}px 并降为 {count} 帧（原 {frame_count} 帧 @{long_edge}px 超上下文预算）"
    count = min(count, max(1, allowed))
    return count, edge, f"帧数按上下文预算（约 {available} token）降为 {count} 帧"


def extract_frames(media_path: Path, out_dir: Path, *, long_edge: int, frame_limit: int) -> tuple[list[dict[str, Any]], str | None]:
    """执行抽帧并返回 [{path, time}]（按时间顺序、已按上限均匀抽样）；失败返回中文原因。

    零帧输入是硬失败：调用方绝不能再把空帧列表送给视觉模型（模型会自信脑补）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # 同目录可能留着上一轮（如视觉确认 2 帧）的旧帧：不清理会让旧图混进本轮的时间戳配对。
    for stale in out_dir.glob("frame-*.jpg"):
        try:
            stale.unlink()
        except OSError:
            pass
    out_pattern = str(out_dir / "frame-%04d.jpg")
    try:
        command = build_extract_command(media_path, out_pattern, long_edge=long_edge)
    except RuntimeError as exc:
        return [], str(exc)
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], f"关键帧抽取超时（>{FFMPEG_TIMEOUT_SECONDS} 秒）。"
    frames: list[dict[str, Any]] = []
    timestamps = parse_pts_times(f"{result.stderr}\n{result.stdout}")
    for position, path in enumerate(sorted(out_dir.glob("frame-*.jpg"))):
        try:
            if path.stat().st_size <= 0:
                # L1：0 字节坏帧跳过时同步丢弃它在 showinfo 序列里的时间戳，
                # 否则后续帧会整体前移一位、时间戳配对错位。
                continue
        except OSError:
            continue
        if position < len(timestamps):
            time = round(timestamps[position], 1)
        else:
            # L2：showinfo 不足时按 30 秒间隔推算的假时间戳，调用方必须在备注标注「时间戳为估算」。
            time = float(position * FRAME_FALLBACK_SECONDS)
            frames.append(
                {"index": len(frames), "path": path, "time": time, "time_estimated": True}
            )
            continue
        frames.append({"index": len(frames), "path": path, "time": time})
    if not frames:
        reason = result.stderr.strip() or result.stdout.strip() or f"ffmpeg 返回码 {result.returncode}"
        return [], f"未能抽取到任何帧：{reason[-800:]}"
    return uniform_sample(frames, frame_limit), None


def frames_timestamp_note(frames: list[dict[str, Any]]) -> str:
    """L2：帧列表里有估算时间戳（showinfo 输出不足，按 30 秒间隔推算）时返回备注文案。"""
    if any(bool(frame.get("time_estimated")) for frame in frames):
        return "时间戳为估算（showinfo 时间戳不足，按 30 秒间隔推算）"
    return ""


def extract_message_text(content: Any) -> str:
    """兼容 llama-server 兼容层的两种 content 形态：str 或分段 list。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part for part in parts if part.strip())
    return ""


def validate_model_output(text: str, *, min_chars: int) -> str | None:
    """输出防呆：空 / 全空白 / 过短 → 中文错误（视觉模型输出异常）。"""
    stripped = (text or "").strip()
    if not stripped:
        return "视觉模型输出异常：响应为空。"
    if len(stripped) < min_chars:
        return f"视觉模型输出异常：有效输出仅 {len(stripped)} 字（低于 {min_chars} 字下限）。"
    return None


def hallucination_warning(output_text: str, frame_count: int) -> str:
    """疑似脑补的简单启发式：输出提到的时间点数 > 帧数×1.5 时标记警告（入备注，不判失败）。"""
    if frame_count <= 0:
        return ""
    mentions = len(TIME_MENTION_RE.findall(output_text or ""))
    if mentions > frame_count * 1.5:
        return f"输出提到的时间点约 {mentions} 处，明显多于帧数 {frame_count}，疑似脑补，建议人工核验"
    return ""


def parse_confirm_answer(text: str) -> tuple[str, str]:
    """解析视觉确认回答（是/否/不确定）；按首个出现的位置判定，无法解析按不确定处理。

    「不是」按否定处理（优先于「是」），避免模型回一句"不是软件界面"被误判成肯定。
    """
    stripped = (text or "").strip()
    if not stripped:
        return "不确定", "视觉确认输出为空，按不确定处理"
    positions: list[tuple[int, str]] = []
    for keyword, answer in (("不确定", "不确定"), ("不是", "否"), ("否", "否"), ("是", "是")):
        index = stripped.find(keyword)
        if index >= 0:
            positions.append((index, answer))
    if not positions:
        return "不确定", f"无法解析视觉确认回答（截取：{stripped[:60]}），按不确定处理"
    positions.sort()
    return positions[0][1], ""


# ---------------------------------------------------------------------------
# 视觉后端：探活与 OpenAI 兼容调用
# ---------------------------------------------------------------------------


def probe_backend_health(spec: BackendSpec) -> str | None:
    """local 后端 GET /health 探活；失败返回中文原因（含 README 指引），绝不自己拉起服务。"""
    url = f"{spec.base_url.rstrip('/')}/health"
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            if 200 <= response.status < 300:
                return None
            return f"视觉服务不可用（/health 返回 {response.status}），启动命令见 README 采集环境依赖。"
    except urllib.error.HTTPError as exc:
        return f"视觉服务不可用（{spec.base_url} /health 返回 {exc.code}），启动命令见 README 采集环境依赖。"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = redact_secrets(f"{spec.base_url} {exc}", [spec.api_key])
        return f"视觉服务不可用（{reason}），启动命令见 README 采集环境依赖。"


def build_messages(frames: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
    """OpenAI 兼容多模态消息：每帧前插 [时间戳s] 文本块，图片走 base64 data URL。"""
    content: list[dict[str, Any]] = []
    for frame in frames:
        data = base64.b64encode(Path(frame["path"]).read_bytes()).decode("ascii")
        content.append({"type": "text", "text": f"[{frame['time']:g}s]"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}
        )
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


def chat_completions_url(base_url: str) -> str:
    """OpenAI 兼容端点拼接（评审 M1）：

    - base_url 已以 /chat/completions 结尾 → 直接用；
    - 已以 /v1 或其他版本段（/v2、/v4 …）结尾 → 只追加 /chat/completions
      （glm 默认 https://open.bigmodel.cn/api/paas/v4 → 官方端点 .../paas/v4/chat/completions）；
    - 否则 → 追加 /v1/chat/completions（llama-server 等标准 OpenAI 兼容服务）。
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return "/v1/chat/completions"
    if base.endswith("/chat/completions"):
        return base
    if re.search(r"/v\d+$", base):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def chat_completion(spec: BackendSpec, messages: list[dict[str, Any]]) -> tuple[str, str | None]:
    """调 /v1/chat/completions；返回 (拼接后的文本, 中文错误)。错误已 redact，不含 api_key 片段。"""
    url = chat_completions_url(spec.base_url)
    payload = {
        "model": spec.model,
        "messages": messages,
        "temperature": VLM_TEMPERATURE,
        "max_tokens": VLM_MAX_TOKENS,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if spec.api_key:
        headers["Authorization"] = f"Bearer {spec.api_key}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=VLM_CHAT_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[-500:]
        except Exception:  # noqa: BLE001 - 错误体读不出时只报状态码
            detail = ""
        reason = redact_secrets(f"HTTP {exc.code}{'：' + detail if detail else ''}", [spec.api_key])
        return "", f"视觉模型请求失败（{reason}）。"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = redact_secrets(f"{exc}", [spec.api_key])
        return "", f"视觉模型请求失败（{reason}）。"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return "", "视觉模型响应不是 JSON。"
    choices = data.get("choices") if isinstance(data, dict) else None
    message = (choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None) or {}
    text = extract_message_text(message.get("content") if isinstance(message, dict) else "")
    if not text.strip():
        return "", "视觉模型响应缺少 choices[0].message.content 文本。"
    return text, None


def run_vision_backends(
    config: dict[str, Any],
    frames: list[dict[str, Any]],
    *,
    prompt: str,
    min_chars: int,
    health_check: bool = True,
) -> tuple[str, str, str | None]:
    """按降级链尝试后端；返回 (后端名, 模型输出, 错误)。成功时错误为 None。"""
    errors: list[str] = []
    for spec in backend_specs(config):
        if health_check and spec.name == "local":
            health_error = probe_backend_health(spec)
            if health_error:
                errors.append(f"local：{health_error}")
                continue
        text, chat_error = chat_completion(spec, build_messages(frames, prompt))
        if chat_error:
            errors.append(f"{spec.name}：{chat_error}")
            continue
        output_error = validate_model_output(text, min_chars=min_chars)
        if output_error:
            errors.append(f"{spec.name}：{output_error}")
            continue
        return spec.name, text, None
    return "", "", "视觉后端全部失败：" + ("；".join(errors) or "没有可用的视觉后端。")


def run_ocr_fallback(frames: list[dict[str, Any]]) -> tuple[str, str | None]:
    """PaddleOCR-VL 降级档：对帧做文档式解析，仅产出观察（无推断）。"""
    capture = _capture_link()
    try:
        records, ocr_error = capture.run_paddleocr_vl_ocr([Path(frame["path"]) for frame in frames])
    except Exception as exc:  # noqa: BLE001 - OCR 环境异常按失败处理
        return "", f"PaddleOCR-VL 调用异常：{exc}"
    if ocr_error:
        return "", f"PaddleOCR-VL 降级失败：{ocr_error}"
    text_by_path = {str(record.get("path") or ""): str(record.get("text") or "").strip() for record in records if isinstance(record, dict)}
    lines: list[str] = []
    for frame in frames:
        text = text_by_path.get(str(frame["path"]), "")
        if text:
            lines.append(f"- [{frame['time']:g}s] {text.replace(chr(10), ' / ')}")
        else:
            # L9：无文字帧也要占位，保持逐帧时间轴完整（否则看不出该帧是被看过还是被丢了）。
            lines.append(f"- [{frame['time']:g}s] （无文字）")
    if not any(text_by_path.get(str(frame["path"]), "") for frame in frames):
        return "", "PaddleOCR-VL 未从任何帧提取到文字。"
    return "\n".join(lines), None


# ---------------------------------------------------------------------------
# 笔记读写：在批次 4 InboxNote 上扩展画面解析所需的字段与小节
# ---------------------------------------------------------------------------


class VisionNote(segmented.InboxNote):
    """收件箱笔记的保真读写（复用 segmented_transcribe.InboxNote）+ 画面解析扩展。"""

    def set_modality_vision(self, label: str) -> None:
        """更新 frontmatter 与正文基础信息里「模态覆盖」的画面解析片段（转写等保持原样）。"""
        self._update_modality_line("模态覆盖:", label)
        self._update_modality_line("- 模态覆盖：", label)

    def _update_modality_line(self, prefix: str, label: str) -> None:
        # 与 InboxNote._update_modality_line 同款逻辑，只改匹配键（画面解析=）。
        for index, line in enumerate(self._lines):
            if not line.startswith(prefix):
                continue
            value = segmented.yaml_unquote(line[len(prefix):])
            parts = [part.strip() for part in value.split("|")]
            rewritten = False
            for position, part in enumerate(parts):
                if part.startswith("画面解析="):
                    parts[position] = f"画面解析={label}"
                    rewritten = True
                    break
            if not rewritten:
                parts.append(f"画面解析={label}")
            new_value = " | ".join(parts)
            rendered = segmented.yaml_quote(new_value)
            separator = " " if prefix.rstrip().endswith(":") else ""
            self._lines[index] = f"{prefix}{separator}{rendered}" if rendered else f"{prefix}{separator}".rstrip()
            return

    def replace_vision_section(self, section_lines: list[str]) -> None:
        """替换正文「## 画面解析」小节；没有则插到「## 标签和分类」前（对齐 replace_extract_section 的保真策略）。

        L6：替换边界只认 KNOWN_SECTION_HEADINGS 里的真实小节标题（或分段注释/文件尾）——
        旧「画面解析」内容里夹带的 "## " 行（如模型输出带了二级标题）不能再把边界提前截断，
        否则截断点之后会残留上一轮的旧内容。
        """
        heading = "## 画面解析"
        start = None
        for index, line in enumerate(self._lines):
            if line.strip() == heading:
                start = index
                break
        if start is not None:
            end = len(self._lines)
            for index in range(start + 1, len(self._lines)):
                line = self._lines[index]
                if line.strip() in KNOWN_SECTION_HEADINGS or segmented.SEGMENT_COMMENT_RE.search(line):
                    end = index
                    break
            self._lines[start:end] = section_lines
            return
        anchor = None
        for index, line in enumerate(self._lines):
            if line.strip() == "## 标签和分类" or segmented.SEGMENT_COMMENT_RE.search(line):
                anchor = index
                break
        block = ["", *section_lines]
        if anchor is not None:
            self._lines[anchor:anchor] = block
        else:
            insert_at = len(self._lines)
            while insert_at > 0 and self._lines[insert_at - 1].strip() == "":
                insert_at -= 1
            self._lines[insert_at:insert_at] = block

    def work_type_source(self) -> str:
        """作品类型来源：启发式/视觉确认/人工；缺失返回空串（评审 M2：视为来源未知，不改写）。"""
        return self.field("作品类型来源").strip()


def note_keyword_hit(note: VisionNote) -> bool:
    """标题+文案是否命中 UI 演示关键词启发式（复用 capture_link 共用关键词组）。"""
    text = f"{note.field('标题')}\n{note.body_section_text('## 文案摘录')}"
    return bool(_capture_link().ui_demo_keyword_heuristic(text))


def vision_confirm_allowed(note: VisionNote) -> tuple[bool, str]:
    """视觉确认改写许可：人工 > 视觉确认 > 启发式；只有启发式来源的类型允许改写。

    评审 M2：字段缺失（老笔记，可能早于该字段上线前被用户手工改过类型）视为来源未知，
    保守起见不允许视觉确认改写。
    """
    source = note.work_type_source()
    if not source:
        return False, "类型来源未知，未做视觉确认改写；如需视觉确认请将 作品类型来源 设为 启发式"
    if source == TYPE_SOURCE_MANUAL:
        return False, f"作品类型为人工判定（作品类型来源=人工），视觉确认不改写"
    if source == TYPE_SOURCE_VISION:
        return False, "作品类型已由视觉确认判定，不重复确认"
    return True, ""


def note_content_extract_proxy(note: VisionNote) -> dict[str, Any] | None:
    """从笔记字段构造 assess_content_quality 所需的转写代理（word_count 取 内容摘录字数）。"""
    raw = note.field("内容摘录字数")
    digits = re.sub(r"\D+", "", raw) or "0"
    word_count = int(digits) if digits else 0
    if word_count <= 0 and note.field("转写状态") != segmented.STATUS_DONE:
        return None
    return {
        "source": note.field("内容摘录来源") or "公开视频音频转写",
        "backend": note.field("内容摘录后端") or "faster-whisper",
        "model": "",
        "word_count": word_count,
    }


def reassess_quality(note: VisionNote, vision_label: str) -> tuple[str, str]:
    """画面解析完成后重评内容质量门禁（设计稿子设计五规则表）。"""
    capture = _capture_link()
    quality, gate = capture.assess_content_quality(
        platform=note.field("来源平台") or "抖音",
        material_type=note.field("资料类型") or "社媒链接",
        parse_status=note.field("解析状态") or "已解析",
        description=note.body_section_text("## 文案摘录"),
        platform_meta={},
        content_extract=note_content_extract_proxy(note),
        content_extract_error="",
        image_ocr=None,
        image_ocr_error="",
        youtube_subtitle=None,
        external_transcript=None,
        work_type=note.field("作品类型") or "",
        vision_status=vision_label,
    )
    note.set_field("内容质量", quality)
    note.set_field("质量门禁", gate)
    note.set_body_info("内容质量", quality)
    note.set_body_info("质量门禁", gate)
    return quality, gate


# ---------------------------------------------------------------------------
# 媒体获取与并发锁（复用 segmented_transcribe；锁名为画面解析专用）
# ---------------------------------------------------------------------------


# 评审 M3：画面解析能用的只有视频容器；16k wav（分段转写中间产物）可能比低码率 mp4 还大，
# 不限容器会选中纯音频 → select 滤镜 0 帧 → 失败环。
VISION_MEDIA_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv")


def find_cached_media(cache_dir: Path) -> Path | None:
    """缓存目录里最大的视频文件（画面解析需要视频本体，不走 wav 路径）。

    只认视频容器后缀并显式排除分段转写的 segmented-audio wav；目录里没有视频文件
    （只有 wav 或空）视为无缓存，交由调用方走媒体重取。
    """
    if not cache_dir.is_dir():
        return None
    candidates = [
        path
        for path in cache_dir.iterdir()
        if path.is_file()
        and path.name != segmented.AUDIO_WAV_NAME
        and path.suffix.lower() in VISION_MEDIA_SUFFIXES
        and path.stat().st_size > 0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def acquire_vision_lock(cache_dir: Path) -> tuple[Path | None, str]:
    """并发双跑防护：直接复用 segmented_transcribe 的转写锁（同一媒体缓存目录、同一锁文件名），
    保证画面解析与分段转写/另一次画面解析不会同时处理同一 content_id。
    """
    return segmented.acquire_transcribe_lock(cache_dir)


def release_vision_lock(lock_path: Path | None) -> None:
    segmented.release_transcribe_lock(lock_path)


def ensure_media(note: VisionNote) -> tuple[Path | None, str, str | None]:
    """拿到可抽帧的媒体：缓存目录 → 按 原始链接+作品页链接 重取。返回 (路径, 来源标签, 错误)。"""
    platform = note.field("来源平台")
    content_id = segmented.media_cache_id(note)
    client = segmented.platform_client(platform)
    try:
        cache_dir = client.media_cache_dir(content_id)
    except Exception as exc:  # noqa: BLE001
        return None, "", f"媒体缓存目录不可用：{exc}"
    cached = find_cached_media(cache_dir)
    if cached is not None:
        return cached, "缓存媒体", None
    media_path, fetch_error = segmented.refetch_media(
        platform, [note.field("来源链接"), note.field("原始链接")], content_id
    )
    if media_path is None:
        return None, "", fetch_error or "媒体重取失败：未知原因。"
    return media_path, "媒体重取", None


def cleanup_media_cache(note: VisionNote) -> None:
    """解析完成后删除媒体缓存整目录（决策 1）；失败吞掉。"""
    try:
        platform = note.field("来源平台")
        segmented.platform_client(platform).cleanup_media(segmented.media_cache_id(note))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 状态机：单笔记处理
# ---------------------------------------------------------------------------


@dataclass
class VisionRunResult:
    path: Path
    outcome: str = ""  # 完成(N帧)/仅OCR降级/失败/视觉确认-是/视觉确认-否/视觉确认-不确定/跳过
    frames: int = 0
    backend: str = ""
    remark: str = ""
    error: str = ""


def note_needs_full_analysis(note: VisionNote) -> bool:
    work_type = note.field("作品类型")
    status = note.field("画面解析") or STATUS_PENDING
    return work_type == WORK_TYPE_UI and status in ("", STATUS_PENDING, STATUS_FAILED)


def note_needs_vision_confirm(note: VisionNote) -> bool:
    if note.field("作品类型") not in {"口播视频", "未知"}:
        return False
    if note.field("视觉确认").strip():
        return False  # 已确认过，不重复问
    if not note_keyword_hit(note):
        return False
    allowed, _reason = vision_confirm_allowed(note)
    return allowed


def vision_confirm_blocked_reason(note: VisionNote) -> str:
    """评审 M2：本可走视觉确认、但被「作品类型来源」门槛挡下的备注文案；非该场景返回空串。"""
    if note.field("作品类型") not in {"口播视频", "未知"}:
        return ""
    if note.field("视觉确认").strip():
        return ""
    if not note_keyword_hit(note):
        return ""
    allowed, reason = vision_confirm_allowed(note)
    return "" if allowed else reason


def _frame_policy_label(long_edge: int, frame_max: int) -> str:
    return f"场景切换 + 每 {FRAME_FALLBACK_SECONDS} 秒兜底，长边 {long_edge}px，上限 {frame_max} 帧、超出时均匀抽样"


def _mark_vision_failed(note: VisionNote, reason: str, result: VisionRunResult) -> None:
    result.outcome = STATUS_FAILED
    result.remark = reason
    note.set_field("画面解析", STATUS_FAILED)
    note.set_field("画面解析备注", reason)
    note.set_modality_vision(STATUS_FAILED)
    reassess_quality(note, STATUS_FAILED)


def process_note(
    path: Path,
    *,
    config: dict[str, Any],
    allow_ocr_fallback: bool = False,
    skip_confirm: bool = False,
) -> VisionRunResult:
    """处理单条笔记：备媒体 → 抽帧 →（视觉确认 →）视觉模型分析 → 回写笔记。"""
    note = VisionNote(path)
    result = VisionRunResult(path=path)
    platform = note.field("来源平台")
    if platform not in SUPPORTED_PLATFORMS:
        result.outcome = "跳过"
        result.remark = f"来源平台 {platform or '未知'} 不支持画面解析（仅 {'/'.join(SUPPORTED_PLATFORMS)}）。"
        return result

    full_analysis = note_needs_full_analysis(note)
    confirm_needed = (not skip_confirm) and (not full_analysis) and note_needs_vision_confirm(note)
    if not full_analysis and not confirm_needed:
        result.outcome = "跳过"
        # 评审 M2：来源未知挡下视觉确认时给出可操作的备注（写运行记录，不改笔记内容）。
        blocked = "" if skip_confirm else vision_confirm_blocked_reason(note)
        result.remark = blocked or "笔记不需要画面解析（类型/状态不匹配）。"
        return result

    lock_dir: Path | None = None
    try:
        lock_dir = segmented.platform_client(platform).media_cache_dir(segmented.media_cache_id(note))
    except Exception:  # noqa: BLE001 - 锁目录不可用不阻断，按无锁降级处理
        lock_dir = None
    lock_path: Path | None = None
    if lock_dir is not None:
        lock_path, lock_reason = acquire_vision_lock(lock_dir)
        if lock_path is None:
            result.outcome = "跳过"
            result.remark = f"跳过该笔记：{lock_reason}"
            return result

    try:
        return _process_note_locked(note, result, config, allow_ocr_fallback=allow_ocr_fallback)
    finally:
        release_vision_lock(lock_path)


def _process_note_locked(
    note: VisionNote,
    result: VisionRunResult,
    config: dict[str, Any],
    *,
    allow_ocr_fallback: bool,
) -> VisionRunResult:
    started = time.monotonic()

    # 1) 媒体（缓存优先，丢失重取）。
    try:
        media_path, media_source, media_error = ensure_media(note)
    except Exception as exc:  # noqa: BLE001 - 平台不支持/缓存目录异常等按媒体失败处理
        media_path, media_source, media_error = None, "", f"{exc}"
    if media_path is None:
        reason = f"媒体重取失败：{media_error or '未获得可用媒体'}"
        if note_needs_full_analysis(note):
            _mark_vision_failed(note, reason, result)
            note.save()
        else:
            result.outcome = "跳过"
            result.remark = f"视觉确认未执行：{reason}（保留待下次重试）"
        return result

    frame_max = int(config.get("frame_max") or DEFAULT_FRAME_MAX)
    long_edge = int(config.get("frame_long_edge") or DEFAULT_FRAME_LONG_EDGE)

    with tempfile.TemporaryDirectory(prefix="my-mind-vision-") as temp_dir_name:
        frames_dir = Path(temp_dir_name) / "frames"

        # 2) UI 演示视觉确认（决策 10）：口播/未知 且关键词命中 → 抽 2 帧问是否软件界面录屏。
        if not note_needs_full_analysis(note):
            confirm_frames, confirm_error = extract_frames(
                media_path, frames_dir, long_edge=long_edge, frame_limit=CONFIRM_FRAME_COUNT
            )
            if not confirm_frames:
                result.outcome = "跳过"
                result.remark = f"视觉确认未执行：{confirm_error}（保留待下次重试）"
                return result
            backend, answer_text, confirm_backend_error = run_vision_backends(
                config, confirm_frames, prompt=VISION_CONFIRM_PROMPT, min_chars=1
            )
            if confirm_backend_error:
                result.outcome = "跳过"
                result.remark = f"视觉确认未执行：{confirm_backend_error}（保留待下次重试）"
                return result
            answer, parse_error = parse_confirm_answer(answer_text)
            confirm_note = parse_error or f"视觉确认回答：{answer_text.strip()[:80]}"
            if answer == "是":
                note.set_field("作品类型", WORK_TYPE_UI)
                note.set_field("作品类型来源", TYPE_SOURCE_VISION)
                note.set_body_info("作品类型", WORK_TYPE_UI)
                note.set_field("视觉确认", f"是（{today_date()}，视觉判定为软件界面/操作演示录屏）")
                note.save()
                result.outcome = "视觉确认-是"
                result.remark = f"视觉确认判定为 UI 演示录屏，作品类型升级为 {WORK_TYPE_UI}，继续画面解析。{confirm_note}"
                # 落入下方完整画面解析（帧重新按完整上限抽取）。
            else:
                label = "否" if answer == "否" else "不确定"
                note.set_field("视觉确认", f"{label}（{today_date()}，{confirm_note}）")
                if answer == "否":
                    note.set_field("画面解析", STATUS_NOT_APPLICABLE)
                    vision_remark = f"视觉确认为非软件界面录屏（{today_date()}），画面解析不适用"
                    note.set_modality_vision(STATUS_NOT_APPLICABLE)
                else:
                    vision_remark = f"视觉确认结果不确定（{today_date()}），建议人工判定作品类型后重跑"
                # 评审 M4：确认「否/不确定」收尾时只有转写已完成才清媒体缓存；
                # 分段转写还没跑完的笔记保留缓存供其复用（备注注明）。
                transcript_done = note.field("转写状态") == segmented.STATUS_DONE
                if not transcript_done:
                    vision_remark = f"{vision_remark}；媒体缓存保留至转写完成"
                note.set_field("画面解析备注", vision_remark)
                note.save()
                if transcript_done:
                    cleanup_media_cache(note)
                result.outcome = f"视觉确认-{label}"
                result.remark = f"视觉确认为{label}：{confirm_note}"
                return result

        # 3) 完整画面分析：预算校验 → 抽帧（0 帧=硬失败，绝不调模型）→ 降级链调用。
        budget_count, budget_edge, budget_note = fit_frames_budget(frame_max, long_edge)
        frames, extract_error = extract_frames(
            media_path, frames_dir, long_edge=budget_edge, frame_limit=budget_count
        )
        if extract_error or not frames:
            # 零帧输入时视觉模型会自信脑补——这里硬失败，绝不调用模型。
            _mark_vision_failed(note, extract_error or "未能抽取到任何帧", result)
            note.save()
            return result
        result.frames = len(frames)
        timestamp_note = frames_timestamp_note(frames)
        backend, output_text, backend_error = run_vision_backends(
            config, frames, prompt=VISION_ANALYSIS_PROMPT, min_chars=MIN_ANALYSIS_CHARS
        )
        if not backend_error:
            _finish_analysis_success(
                note,
                result,
                frames=frames,
                output_text=output_text,
                backend=backend,
                config=config,
                long_edge=budget_edge,
                frame_max=frame_max,
                budget_note=budget_note,
                timestamp_note=timestamp_note,
                elapsed=time.monotonic() - started,
                media_source=media_source,
            )
            note.save()
            cleanup_media_cache(note)
            return result

        # 4) 全部视觉后端失败：默认直接标失败保留重跑机会；--allow-ocr-fallback 才走 PaddleOCR-VL。
        if not allow_ocr_fallback:
            _mark_vision_failed(note, backend_error, result)
            note.save()
            return result
        ocr_text, ocr_error = run_ocr_fallback(frames)
        if ocr_error:
            _mark_vision_failed(note, f"{backend_error}；OCR 降级亦失败：{ocr_error}", result)
            note.save()
            return result
        _finish_analysis_ocr(
            note,
            result,
            frames=frames,
            ocr_text=ocr_text,
            long_edge=budget_edge,
            frame_max=frame_max,
            timestamp_note=timestamp_note,
            elapsed=time.monotonic() - started,
            media_source=media_source,
        )
        note.save()
        cleanup_media_cache(note)
        return result


def _finish_analysis_success(
    note: VisionNote,
    result: VisionRunResult,
    *,
    frames: list[dict[str, Any]],
    output_text: str,
    backend: str,
    config: dict[str, Any],
    long_edge: int,
    frame_max: int,
    budget_note: str,
    timestamp_note: str = "",
    elapsed: float,
    media_source: str,
) -> None:
    warning = hallucination_warning(output_text, len(frames))
    remarks = [part for part in (budget_note, timestamp_note, warning) if part]
    spec = next((item for item in backend_specs(config) if item.name == backend), None)
    backend_label = spec.label if spec else backend
    label = f"完成({len(frames)}帧)"
    # 视觉确认刚把类型升级为 UI 演示的场景：保留确认摘要，最终结果以画面解析为准。
    prior = result.remark if result.outcome.startswith("视觉确认") else ""
    result.outcome = label
    result.backend = backend
    result.remark = "；".join(([prior] if prior else []) + (remarks or ["画面解析完成"]))
    # 评审 M2：作品类型来源缺失时不再回填「启发式」——来源未知就保持未知，不虚构出处。
    note.set_field("画面解析", label)
    note.set_field("画面解析备注", "；".join(remarks))
    note.set_modality_vision(label)
    section = [
        "## 画面解析",
        "",
        f"- 帧数：{len(frames)} 帧（{_frame_policy_label(long_edge, frame_max)}）",
        f"- 帧来源：{media_source}",
        f"- 后端：{backend_label}",
        f"- 耗时：{int(round(elapsed))} 秒",
    ]
    if remarks:
        section.append(f"- 备注：{'；'.join(remarks)}")
    section.extend(["", "### 逐帧观察与推断", "", output_text.strip()])
    note.replace_vision_section(section)
    reassess_quality(note, label)


def _finish_analysis_ocr(
    note: VisionNote,
    result: VisionRunResult,
    *,
    frames: list[dict[str, Any]],
    ocr_text: str,
    long_edge: int,
    frame_max: int,
    timestamp_note: str = "",
    elapsed: float,
    media_source: str,
) -> None:
    label = STATUS_OCR_FALLBACK
    result.outcome = label
    result.backend = "paddleocr-vl"
    base_remark = "视觉后端失败，已按仅OCR降级产出帧内文字观察（无推断）"
    result.remark = "；".join(part for part in (base_remark, timestamp_note) if part)
    note.set_field("画面解析", label)
    note.set_field("画面解析备注", result.remark)
    note.set_modality_vision(label)
    section = [
        "## 画面解析",
        "",
        f"- 帧数：{len(frames)} 帧（{_frame_policy_label(long_edge, frame_max)}）",
        f"- 帧来源：{media_source}",
        "- 后端：PaddleOCR-VL（仅OCR降级：只有帧内文字观察，无推断与流程总结）",
        f"- 耗时：{int(round(elapsed))} 秒",
    ]
    if timestamp_note:
        section.append(f"- 备注：{timestamp_note}")
    section.extend(
        [
            "",
            "### 逐帧OCR（仅观察）",
            "",
            ocr_text.strip(),
        ]
    )
    note.replace_vision_section(section)
    reassess_quality(note, label)


# ---------------------------------------------------------------------------
# 收件箱扫描、运行记录与 CLI
# ---------------------------------------------------------------------------


def scan_inbox(inbox: Path, max_notes: int, *, skip_confirm: bool = False) -> list[Path]:
    """按文件名时间序扫描需要画面解析/视觉确认的笔记，最多 max_notes 条。"""
    if not inbox.is_dir():
        return []
    selected: list[Path] = []
    for path in sorted(inbox.glob("*.md")):
        if len(selected) >= max_notes:
            break
        try:
            note = VisionNote(path)
        except (OSError, UnicodeDecodeError):
            continue
        if note.field("来源平台") not in SUPPORTED_PLATFORMS:
            continue
        if note_needs_full_analysis(note) or (not skip_confirm and note_needs_vision_confirm(note)):
            selected.append(path)
    return selected


def _unique_path(directory: Path, filename: str) -> Path:
    """与 segmented_transcribe._unique_path 同款（最小复制，避免跨模块取私有名）。"""
    path = directory / filename
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 2
    while True:
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def write_run_record(
    results: list[VisionRunResult],
    *,
    backend: str,
    frame_max: int,
    elapsed_seconds: float,
    scanned_candidates: int,
    extra_args: str = "",
) -> Path:
    """把运行证据写入 85_运行记录/画面解析-YYYY-MM-DD-HHMM.md（仓库运行记录惯例 frontmatter）。"""
    now = dt.datetime.now(TZ)
    done = [item for item in results if item.outcome.startswith("完成")]
    lines = [
        "---",
        "类别: 运行记录",
        "记录类型: 画面解析",
        f"生成时间: {now.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"后端: {backend}",
        f"帧上限: {frame_max}",
        f"实际耗时秒: {round(elapsed_seconds)}",
        f"处理笔记数: {len(results)}",
        "---",
        "",
        "# 画面解析运行记录",
        "",
        f"- 候选笔记数：{scanned_candidates}",
        f"- 处理笔记数：{len(results)}",
        f"- 画面解析完成：{len(done)} 条",
        f"- 失败：{sum(1 for item in results if item.outcome == STATUS_FAILED) or '无'}",
    ]
    if extra_args:
        lines.append(f"- 调用参数：{extra_args}")
    lines.extend(["", "## 笔记明细", ""])
    if not results:
        lines.append("- 本轮没有可处理的笔记。")
    for item in results:
        lines.extend(
            [
                f"### {item.path.name}",
                "",
                f"- 结果：{item.outcome or '未知'}",
                f"- 帧数：{item.frames or '—'}",
                f"- 后端：{item.backend or '—'}",
                f"- 备注：{item.remark or '无'}",
                "",
            ]
        )
    RUN_RECORD_DIR.mkdir(parents=True, exist_ok=True)
    record_path = _unique_path(RUN_RECORD_DIR, f"画面解析-{now.strftime('%Y-%m-%d-%H%M')}.md")
    record_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return record_path


def _describe_dry_run(note_path: Path) -> str:
    try:
        note = VisionNote(note_path)
    except (OSError, UnicodeDecodeError) as exc:
        return f"- {note_path.name}：无法读取（{exc}）"
    work_type = note.field("作品类型") or "未知"
    vision_status = note.field("画面解析") or STATUS_PENDING
    if note_needs_full_analysis(note):
        action = f"将执行画面解析（当前 {vision_status}），帧上限由配置决定"
    elif note_needs_vision_confirm(note):
        action = "将执行 UI 演示视觉确认（抽 2 帧判定类型）"
    else:
        action = "无需处理"
    return (
        f"- {note_path.name}：作品类型={work_type}，画面解析={vision_status}，"
        f"转写状态={note.field('转写状态') or '无'}，{action}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="UI 演示视频的画面解析：关键帧提取 + 视觉模型分析（设计稿子设计四，批次 5）。",
    )
    parser.add_argument(
        "--note",
        default="",
        help="只处理指定的收件箱笔记路径（默认扫描 00_收件箱 中最早的待解析笔记）",
    )
    parser.add_argument(
        "--max-notes",
        type=int,
        default=3,
        help="单次运行最多考虑的笔记条数，默认 3",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的笔记与当前状态，不抽帧、不写任何文件")
    parser.add_argument(
        "--backend",
        choices=("local", "glm"),
        default="",
        help="覆盖 vision.json 的主后端（默认读配置）",
    )
    parser.add_argument(
        "--skip-confirm",
        action="store_true",
        help="跳过 UI 演示视觉确认（只处理已是 UI演示视频 的笔记）",
    )
    parser.add_argument(
        "--allow-ocr-fallback",
        action="store_true",
        help="视觉后端全部失败时允许 PaddleOCR-VL 仅观察降级（默认关闭，直接标失败保留重跑机会）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    try:
        config = load_vision_config()
        if args.backend:
            config["backend"] = args.backend
        if config["backend"] == "glm" and not glm_configured(config):
            print("画面解析硬错误：glm 后端未配置（vision.json 缺少 glm.api_key）。", file=sys.stderr)
            return 1

        if args.note:
            note_path = Path(args.note)
            if not note_path.is_absolute():
                note_path = ROOT / note_path
            if not note_path.is_file():
                print(f"画面解析硬错误：笔记不存在 {note_path}", file=sys.stderr)
                return 1
            candidates = [note_path]
        else:
            candidates = scan_inbox(DEFAULT_INBOX, max(1, args.max_notes), skip_confirm=args.skip_confirm)

        if args.dry_run:
            print(f"画面解析 dry-run：候选 {len(candidates)} 条笔记（不抽帧、不调用模型、不写任何文件）")
            for path in candidates:
                print(_describe_dry_run(path))
            return 0

        results: list[VisionRunResult] = []
        for path in candidates:
            try:
                results.append(
                    process_note(
                        path,
                        config=config,
                        allow_ocr_fallback=args.allow_ocr_fallback,
                        skip_confirm=args.skip_confirm,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 单笔记异常不拖垮整轮运行。
                results.append(VisionRunResult(path=path, outcome="异常", remark=f"处理异常：{exc!r}", error=str(exc)))

        elapsed = time.monotonic() - started
        extra_args = "--note " + args.note if args.note else ""
        record_path = write_run_record(
            results,
            backend=config["backend"],
            frame_max=int(config.get("frame_max") or DEFAULT_FRAME_MAX),
            elapsed_seconds=elapsed,
            scanned_candidates=len(candidates),
            extra_args=extra_args,
        )
        done = sum(1 for item in results if item.outcome.startswith("完成"))
        failed = sum(1 for item in results if item.outcome == STATUS_FAILED)
        print(
            f"画面解析运行完成：处理 {len(results)} 条笔记，完成 {done} 条，失败 {failed} 条"
            f"（后端 {config['backend']}，实际 {elapsed:.0f} 秒；运行记录 "
            f"{record_path.relative_to(ROOT) if record_path.is_relative_to(ROOT) else record_path}）"
        )
        for item in results:
            frames = f"{item.frames} 帧，" if item.frames else ""
            print(f"- {item.path.name}：{item.outcome or '未知'}，{frames}备注：{item.remark or '无'}")
        return 0
    except VisionConfigError as exc:
        print(f"画面解析硬错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 硬错误（参数/存储不可用等）。
        print(f"画面解析硬错误：{exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
