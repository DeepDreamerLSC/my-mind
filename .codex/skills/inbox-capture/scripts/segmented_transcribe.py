#!/usr/bin/env python3
"""长视频分段转写状态机（issue #1 批次 4，设计稿子设计三）。

职责：扫描收件箱中「转写状态 ∈ {未开始, 进行中}」的长视频笔记，按 VAD 语音停顿把音频
切成 60–180 秒转录块逐块转写，进度回写笔记（frontmatter 汇总 + 正文末尾 HTML 注释分段
清单），支持预算断点续跑；覆盖 ≥98% 判定完成并复用 capture_link 的摘要/时间线/质量门禁。

本脚本仅用 Python 标准库；VAD 与 faster-whisper 转写都在 ~/.cache/my-mind/faster-whisper-venv
子进程里执行（复用 capture_link.candidate_faster_whisper_pythons 探测机制）。

CLI 契约（后台总控集成依赖，勿改）：
    python3 .codex/skills/inbox-capture/scripts/segmented_transcribe.py \
        [--budget-seconds 900] [--note <收件箱笔记路径>] [--max-notes 3] [--dry-run]
退出码：0=正常运行（无论是否转完）；1=硬错误（如 --note 指向的笔记不存在）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# 自建服务客户端（同目录独立模块，纯标准库）：媒体缓存定位、断点下载、ffprobe、清理。
try:
    import dtk_client
except Exception:  # noqa: BLE001 - 模块缺失时保持可运行，媒体重取在备注里记录降级。
    dtk_client = None  # type: ignore[assignment]

try:
    import xhs_client
except Exception:  # noqa: BLE001
    xhs_client = None  # type: ignore[assignment]


TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[4]
DEFAULT_INBOX = ROOT / "00_收件箱"
RUN_RECORD_DIR = ROOT / "85_运行记录"

# —— 设计稿子设计三的集中常量（阈值调整只改这里） ——
TRANSCRIPT_COMPLETE_RATIO = 0.98  # 覆盖 ≥98% 判定「已完成」
SEGMENT_MIN_SECONDS = 60.0  # 转录块目标下限（按块内语音时长计）
SEGMENT_MAX_SECONDS = 180.0  # 转录块目标上限
DEFAULT_BUDGET_SECONDS = 900  # 后台总控日更续跑预算（决策 11）
DEFAULT_MAX_NOTES = 3
TRANSCRIBE_MODEL = "small"  # 模型与 int8 精度同一段式默认；语言策略不同：一段式默认 zh，分段为逐块自动检测（长视频可能夹外语）
VAD_MIN_SILENCE_MS = 2000  # ≥2 秒静默视为语音停顿（faster-whisper vad 默认同源）
VAD_SPEECH_PAD_MS = 400
VAD_TIMEOUT_SECONDS = 900
BLOCK_TRANSCRIBE_TIMEOUT_SECONDS = 1800
FFMPEG_TIMEOUT_SECONDS = 1800
MAX_VIDEO_BYTES = 1024 * 1024 * 1024  # 与 capture_link 的 DOUYIN/XHS_VIDEO_MAX_BYTES 一致
AUDIO_WAV_NAME = "segmented-audio-16k.wav"
WAV_HEADER_BYTES = 44  # ffmpeg 输出的规范 PCM wav 头长度（16k mono 16bit）
WAV_SIZE_TOLERANCE_BYTES = 1024  # 校验缓存的余量（容忍少量尾部填充块）
TRANSCRIBE_LOCK_NAME = ".transcribe-lock"
TRANSCRIBE_LOCK_STALE_SECONDS = 2 * 3600  # 超过 2 小时的锁视为死锁，可抢占
SEGMENT_COMMENT_KEY = "my-mind:transcript-segments"
SEGMENT_COMMENT_RE = re.compile(r"<!--\s*my-mind:transcript-segments\s+(.*?)\s*-->", re.DOTALL)
STATUS_PENDING = "未开始"
STATUS_RUNNING = "进行中"
STATUS_DONE = "已完成"
STATUS_FAILED = "失败"
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_RUNNING)
MEDIA_SUFFIXES = (".mp4", ".mp3", ".m4a", ".wav", ".mov", ".webm", ".flv", ".aac", ".ogg", ".mpga")
SAFE_CACHE_ID_RE = re.compile(r"[0-9A-Za-z._-]+")

_monotonic = time.monotonic  # 测试可替换的时钟
_capture_link_module: Any | None = None


class SegmentStateError(Exception):
    """分段清单损坏等不可恢复的进度存储问题。"""


def _capture_link():
    """按需加载 capture_link（复用纯函数与转写环境探测）；失败抛中文错误。"""
    global _capture_link_module
    if _capture_link_module is None:
        try:
            import capture_link  # noqa: PLC0415 - 按需加载，避免正常路径背上 6000 行模块
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法加载 capture_link 模块（分段转写依赖其摘要/时间线/门禁函数）：{exc!r}") from exc
        _capture_link_module = capture_link
    return _capture_link_module


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def yaml_quote(value: Any) -> str:
    """与 capture_link.yaml_scalar 相同的引号策略，保证写回的 frontmatter 可再解析。"""
    if value is None or value == "":
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if any(ch in text for ch in [":", "#", "[", "]", "{", "}", ",", "\"", "'"]) or text.startswith(("-", "@", "`")):
        return json.dumps(text, ensure_ascii=False)
    return text


def yaml_unquote(text: str) -> str:
    text = str(text or "").strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            return str(json.loads(text))
        except Exception:  # noqa: BLE001 - 非 JSON 引号原文返回。
            return text
    return text


# ---------------------------------------------------------------------------
# 笔记读写：只更新指定字段与注释块，其余内容逐字保留
# ---------------------------------------------------------------------------


class InboxNote:
    """收件箱 Markdown 笔记的保真读写。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        # utf-8-sig：读取时剥掉可能的 BOM（\ufeff），否则带 BOM 笔记的首行 frontmatter 检测会失效。
        raw = self.path.read_text(encoding="utf-8-sig")
        self._lines = raw.split("\n")
        self._fm_end = -1  # frontmatter 结束分隔线（---）的下标
        if self._lines and self._lines[0].strip() == "---":
            for index in range(1, len(self._lines)):
                if self._lines[index].strip() == "---":
                    self._fm_end = index
                    break

    # —— frontmatter ——

    def _fm_range(self) -> tuple[int, int]:
        """返回 frontmatter 属性行区间 [start, end)；无 frontmatter 时返回空区间。"""
        if self._fm_end <= 0:
            return (1, 1)
        return (1, self._fm_end)

    def field(self, key: str) -> str:
        start, end = self._fm_range()
        prefix = f"{key}:"
        for index in range(start, end):
            line = self._lines[index]
            if line.startswith(prefix):
                return yaml_unquote(line[len(prefix):])
        return ""

    def set_field(self, key: str, value: Any, *, after: tuple[str, ...] = ("时长", "解析状态")) -> None:
        rendered = yaml_quote(value)
        new_line = f"{key}: {rendered}" if rendered else f"{key}:"
        prefix = f"{key}:"
        start, end = self._fm_range()
        for index in range(start, end):
            if self._lines[index].startswith(prefix):
                self._lines[index] = new_line
                return
        insert_at = end
        for anchor in after:
            anchor_prefix = f"{anchor}:"
            for index in range(start, end):
                if self._lines[index].startswith(anchor_prefix):
                    insert_at = max(insert_at, index + 1)
        if self._fm_end <= 0:
            # 没有 frontmatter 的笔记：补一个最小 frontmatter，保证新字段有处可写。
            self._lines[0:0] = ["---", new_line, "---"]
            self._fm_end = 2
            return
        self._lines.insert(insert_at, new_line)
        self._fm_end += 1

    def set_modality_transcript(self, label: str) -> None:
        """更新 frontmatter 与正文基础信息里的「模态覆盖」转写片段（文案/图片OCR 等保持原样）。"""
        self._update_modality_line("模态覆盖:", label)
        self._update_modality_line("- 模态覆盖：", label)

    def _update_modality_line(self, prefix: str, label: str) -> None:
        for index, line in enumerate(self._lines):
            if not line.startswith(prefix):
                continue
            value = yaml_unquote(line[len(prefix):])
            parts = [part.strip() for part in value.split("|")]
            rewritten = False
            for position, part in enumerate(parts):
                if part.startswith("转写="):
                    parts[position] = f"转写={label}"
                    rewritten = True
                    break
            if not rewritten:
                parts.append(f"转写={label}")
            new_value = " | ".join(parts)
            rendered = yaml_quote(new_value)
            # frontmatter 用 ASCII 冒号加空格，正文列表行用全角冒号，保持两种行各自的既有格式。
            separator = " " if prefix.rstrip().endswith(":") else ""
            self._lines[index] = f"{prefix}{separator}{rendered}" if rendered else f"{prefix}{separator}".rstrip()
            return

    def set_body_info(self, label: str, value: str) -> None:
        """更新正文「基础信息」列表行（如 - 转写状态：未开始）；不存在则忽略。"""
        prefix = f"- {label}："
        for index, line in enumerate(self._lines):
            if line.startswith(prefix):
                self._lines[index] = f"{prefix}{value}"
                return

    # —— 分段清单注释 ——

    def segments_state(self) -> dict[str, Any] | None:
        """读取正文末尾的分段清单注释；缺失返回 None，损坏抛 SegmentStateError。"""
        for line in self._lines:
            match = SEGMENT_COMMENT_RE.search(line)
            if not match:
                continue
            payload = match.group(1)
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SegmentStateError(f"分段清单注释不是有效 JSON：{exc}") from None
            return normalize_segments_state(data)
        return None

    def set_segments_state(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        # "-->" 会提前终结 HTML 注释；用 JSON 的 \u003e 转义保住原文（json.loads 解回 ">"）。
        payload = payload.replace("-->", "--\\u003e")
        new_line = f"<!-- {SEGMENT_COMMENT_KEY} {payload} -->"
        for index, line in enumerate(self._lines):
            if SEGMENT_COMMENT_RE.search(line):
                self._lines[index] = new_line
                return
        # 追加到正文末尾（保留原有的结尾空行结构）。
        insert_at = len(self._lines)
        while insert_at > 0 and self._lines[insert_at - 1].strip() == "":
            insert_at -= 1
        if insert_at > 0 and self._lines[insert_at - 1].strip() != "":
            self._lines[insert_at:insert_at] = ["", new_line]
        else:
            self._lines.insert(insert_at, new_line)

    def replace_extract_section(self, section_lines: list[str]) -> None:
        """用分段转写结果替换「## 视频内容摘录失败」小节；没有则插到「## 标签和分类」前。"""
        heading = "## 视频内容摘录失败"
        start = None
        for index, line in enumerate(self._lines):
            if line.strip() == heading:
                start = index
                break
        if start is not None:
            end = len(self._lines)
            for index in range(start + 1, len(self._lines)):
                line = self._lines[index]
                if line.startswith("## ") or SEGMENT_COMMENT_RE.search(line):
                    end = index
                    break
            self._lines[start:end] = section_lines
            return
        anchor = None
        for index, line in enumerate(self._lines):
            if line.strip() == "## 标签和分类" or SEGMENT_COMMENT_RE.search(line):
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

    def body_section_text(self, heading: str) -> str:
        """读取正文指定小节（## heading）到下一小节之间的纯文本。"""
        lines: list[str] = []
        inside = False
        for line in self._lines:
            if line.strip() == heading:
                inside = True
                continue
            if inside:
                if line.startswith("## ") or SEGMENT_COMMENT_RE.search(line):
                    break
                lines.append(line)
        return "\n".join(lines).strip()

    def save(self) -> None:
        """原子写回：先写同目录临时文件再 os.replace，写入中途崩溃不会截断收件箱笔记（M4）。"""
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text("\n".join(self._lines), encoding="utf-8")
        os.replace(tmp_path, self.path)


def _state_number(value: Any, field: str) -> float:
    """分段清单数值字段解析；损坏值转 SegmentStateError（含字段名与原因），走既有失败路径（M3）。"""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SegmentStateError(f"分段清单字段 {field} 数值非法：{value!r}（{exc}）。") from exc


def normalize_segments_state(data: Any) -> dict[str, Any]:
    """把注释 JSON 规范为完整状态字典；容忍设计稿示意的一维块列表格式。

    所有数值字段（s/e/sp/segs/skipped/media_duration/zero_progress_runs）损坏时抛
    SegmentStateError（含字段名），由 process_note 走 _mark_failed 标失败转人工，不再裸抛 ValueError。
    """
    if isinstance(data, list):
        data = {"segments": data}
    if not isinstance(data, dict):
        raise SegmentStateError("分段清单顶层必须是 JSON 对象或块数组。")
    raw_segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    segments: list[dict[str, Any]] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            raise SegmentStateError("分段块必须是 JSON 对象。")
        start = int(round(_state_number(item.get("s") or 0, "segments.s")))
        end = int(round(_state_number(item.get("e") or 0, "segments.e")))
        raw_speech = item.get("sp")
        # sp 缺失时回退为块物理时长（设计稿示意的一维块列表没有 sp）；给了值就必须可解析。
        speech = _state_number(raw_speech, "segments.sp") if raw_speech not in (None, "") else float(end - start)
        raw_segs = item.get("segs") or []
        if not isinstance(raw_segs, list):
            raw_segs = []
        segs: list[list[Any]] = []
        for part in raw_segs:
            if not isinstance(part, (list, tuple)) or len(part) < 3:
                continue
            segs.append(
                [
                    _state_number(part[0], "segments.segs.起"),
                    _state_number(part[1], "segments.segs.止"),
                    str(part[2]),
                ]
            )
        segments.append(
            {
                "s": start,
                "e": end,
                "sp": round(max(0.0, speech), 3),
                "done": bool(item.get("done")),
                "text": str(item.get("text") or ""),
                "segs": segs,
            }
        )
    skipped = []
    for item in data.get("skipped") if isinstance(data.get("skipped"), list) else []:
        if not isinstance(item, dict):
            continue
        skipped.append(
            {
                "s": round(_state_number(item.get("s") or 0, "skipped.s"), 1),
                "e": round(_state_number(item.get("e") or 0, "skipped.e"), 1),
            }
        )
    speech_total = data.get("speech_total")
    if not isinstance(speech_total, (int, float)) or speech_total <= 0:
        speech_total = sum(block["sp"] for block in segments)
    return {
        "v": 1,
        "media_duration": round(_state_number(data.get("media_duration") or 0, "media_duration"), 1),
        "speech_total": round(float(speech_total), 1),
        "zero_progress_runs": int(_state_number(data.get("zero_progress_runs") or 0, "zero_progress_runs")),
        "language": str(data.get("language") or ""),
        "planned_at": str(data.get("planned_at") or ""),
        "segments": segments,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# VAD 区间 → 转录块规划
# ---------------------------------------------------------------------------


def split_long_intervals(intervals: list[tuple[float, float]], max_seconds: float) -> list[tuple[float, float]]:
    """把超过上限的单条连续语音等分切开（无停顿可用的兜底，边界只能落在语音内；见设计稿决策 4 的边界说明）。"""
    pieces: list[tuple[float, float]] = []
    for start, end in intervals:
        while end - start > max_seconds + 1e-6:
            pieces.append((start, start + max_seconds))
            start = start + max_seconds
        pieces.append((start, end))
    return pieces


def plan_blocks(
    intervals: list[tuple[float, float]],
    *,
    min_seconds: float = SEGMENT_MIN_SECONDS,
    max_seconds: float = SEGMENT_MAX_SECONDS,
) -> list[dict[str, Any]]:
    """把 VAD 语音区间贪心合并成 60–180 秒转录块；块边界只落在语音停顿处。

    块时长按「块内语音时长合计」计（与 转写覆盖 分子分母同一口径）；块物理跨度含内部停顿。
    """
    cleaned: list[tuple[float, float]] = []
    for start, end in sorted((float(s), float(e)) for s, e in intervals if e > s):
        if cleaned and start <= cleaned[-1][1] + 1e-6:
            cleaned[-1] = (cleaned[-1][0], max(cleaned[-1][1], end))
        else:
            cleaned.append((start, end))
    pieces = split_long_intervals(cleaned, max_seconds)

    groups: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    current_speech = 0.0
    for piece in pieces:
        span = piece[1] - piece[0]
        if current and current_speech + span > max_seconds + 1e-6:
            groups.append(current)
            current = []
            current_speech = 0.0
        current.append(piece)
        current_speech += span
    if current:
        groups.append(current)

    blocks: list[dict[str, Any]] = []
    for group in groups:
        blocks.append(_make_block(group))
    # 保险合并：相邻两块都偏短且合并后仍在上限时并成一块（贪心通常已避免，保护边界情形）。
    merged: list[dict[str, Any]] = []
    for block in blocks:
        if (
            merged
            and (merged[-1]["sp"] < min_seconds or block["sp"] < min_seconds)
            and merged[-1]["sp"] + block["sp"] <= max_seconds + 1e-6
        ):
            previous = merged[-1]
            previous["e"] = block["e"]
            previous["sp"] = round(previous["sp"] + block["sp"], 3)
        else:
            merged.append(block)
    return merged


def _make_block(group: list[tuple[float, float]]) -> dict[str, Any]:
    return {
        "s": int(round(group[0][0])),
        "e": int(round(group[-1][1])),
        "sp": round(sum(end - start for start, end in group), 3),
        "done": False,
        "text": "",
        "segs": [],
    }


def skipped_intervals(blocks: list[dict[str, Any]], media_duration: float) -> list[dict[str, float]]:
    """计算未被任何转录块覆盖的无语音区间（静默/BGM，计入覆盖解释但不转写）。"""
    if not blocks:
        return []
    start_bound = 0.0
    end_bound = float(media_duration or 0) or float(blocks[-1]["e"])
    skipped: list[dict[str, float]] = []

    def add_gap(gap_start: float, gap_end: float) -> None:
        if gap_end - gap_start >= 1.0:
            skipped.append({"s": round(gap_start, 1), "e": round(gap_end, 1)})

    cursor = start_bound
    for block in blocks:
        add_gap(cursor, float(block["s"]))
        cursor = max(cursor, float(block["e"]))
    add_gap(cursor, end_bound)
    return skipped


# ---------------------------------------------------------------------------
# 媒体获取：缓存优先，丢失时按作品页链接重取
# ---------------------------------------------------------------------------


def platform_client(platform: str):
    client = dtk_client if platform == "抖音" else xhs_client if platform == "小红书" else None
    if client is None:
        raise RuntimeError(f"来源平台 {platform or '未知'} 不支持媒体重取（仅 抖音/小红书）。")
    return client


def media_cache_id(note: InboxNote) -> str:
    """缓存目录名：优先笔记 内容编号；缺失/非法时用作品页链接哈希兜底（对齐 capture_link）。"""
    content_id = note.field("内容编号").strip()
    if content_id and SAFE_CACHE_ID_RE.fullmatch(content_id):
        return content_id
    anchor = note.field("来源链接") or note.field("原始链接")
    digest = hashlib.sha1(anchor.encode("utf-8")).hexdigest()[:16]
    return f"no-id-{digest}"


def acquire_transcribe_lock(cache_dir: Path) -> tuple[Path | None, str]:
    """并发双跑防护（M4）：媒体缓存目录内 O_CREAT|O_EXCL 建 .transcribe-lock。

    返回 (锁路径, 原因)：拿到锁返回 (路径, "")；锁存在且 mtime < 2 小时返回 (None, 中文原因)；
    超过 2 小时的过期锁视为死锁可抢占（删除重建）。最多重试 3 次，避免异常态死循环。
    """
    lock_path = cache_dir / TRANSCRIBE_LOCK_NAME
    for _ in range(3):
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                continue  # 锁恰好被持有者释放：重试抢占。
            if age < TRANSCRIBE_LOCK_STALE_SECONDS:
                return None, f"另一进程正在转写该笔记（锁已存在 {int(age)} 秒 < {TRANSCRIBE_LOCK_STALE_SECONDS // 60} 分钟）。"
            try:
                lock_path.unlink()  # 过期死锁：抢占后重建。
            except OSError:
                return None, "发现过期的转写锁但无法删除，本轮跳过，请人工检查缓存目录。"
            continue
        except OSError as exc:
            return None, f"无法创建转写锁：{exc}"
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {now_datetime()}\n")
        return lock_path, ""
    return None, "转写锁多次冲突未能建立，本轮跳过。"


def release_transcribe_lock(lock_path: Path | None) -> None:
    """运行结束（含异常路径）删除转写锁；锁已被缓存清理一并删除时静默通过。"""
    if lock_path is None:
        return
    _remove_quietly(lock_path)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def wav_looks_complete(wav: Path) -> bool:
    """用 wave 头校验缓存 wav 完整性：getnframes()*sampwidth*channels+44 ≈ st_size（H1）。

    16k 单声道 16bit 即 frames*2+44；打不开（非 wav/垃圾文件）或大小对不上（写入中断的半截文件）
    都返回 False，调用方应删除缓存 wav 并按缓存缺失路径重走（媒体重取）。
    """
    try:
        with wave.open(str(wav), "rb") as handle:
            expected = handle.getnframes() * handle.getsampwidth() * handle.getnchannels() + WAV_HEADER_BYTES
        return abs(wav.stat().st_size - expected) <= WAV_SIZE_TOLERANCE_BYTES
    except (wave.Error, OSError, EOFError):
        return False


def find_cached_audio_or_media(cache_dir: Path) -> tuple[Path | None, Path | None]:
    """返回 (已抽好的 16k wav, 可抽音频的媒体文件)；都没有则 (None, None)。

    H1：缓存 wav 需通过 wave 头完整性校验才算命中；截断/垃圾 wav 删除后继续找缓存媒体，
    都没有则按缓存缺失路径重走（媒体重取），避免在残缺音频上规划分段导致部分转写被误判完成。
    """
    if not cache_dir.is_dir():
        return None, None
    wav = cache_dir / AUDIO_WAV_NAME
    if wav.is_file() and wav.stat().st_size > 44:
        if wav_looks_complete(wav):
            return wav, None
        try:
            wav.unlink()
        except OSError:
            pass
    candidates = [
        path
        for path in cache_dir.iterdir()
        if path.is_file() and path != wav and path.suffix.lower() in MEDIA_SUFFIXES and path.stat().st_size > 0
    ]
    if not candidates:
        return None, None
    return None, max(candidates, key=lambda path: path.stat().st_size)


def refetch_media(platform: str, urls: list[str], content_id: str) -> tuple[Path | None, str | None]:
    """按笔记的 原始链接+作品页链接 重新解析并下载媒体到缓存目录（复用 download_media + max_bytes）。"""
    client = platform_client(platform)
    try:
        cache_dir = client.media_cache_dir(content_id)
    except Exception as exc:  # noqa: BLE001
        return None, f"媒体缓存目录不可用：{exc}"
    attempted: list[str] = []
    for url in urls:
        if not url:
            continue
        try:
            if platform == "抖音":
                data = dtk_client.parse(url)
                media_url = _capture_link().dtk_media_url(data)
            else:
                data = xhs_client.fetch_detail(url)
                media_url = next((u for u in _capture_link().xhs_download_urls(data) if u), "")
        except Exception as exc:  # noqa: BLE001 - 单个链接失败换下一个（来源链接→原始链接）。
            attempted.append(f"{url}：解析失败：{exc}")
            continue
        if not media_url:
            attempted.append(f"{url}：解析成功但未返回可下载的视频直链。")
            continue
        try:
            media_path = client.download_media(
                media_url,
                cache_dir,
                max_bytes=MAX_VIDEO_BYTES,
                filename=f"{content_id}.mp4",
            )
            return media_path, None
        except Exception as exc:  # noqa: BLE001
            attempted.append(f"{url}：下载失败：{exc}")
    return None, "；".join(attempted) or "笔记没有可用的作品链接用于重取媒体。"


def run_ffmpeg_extract(media_path: Path, wav_path: Path) -> str | None:
    """ffmpeg 抽 16k 单声道 wav；失败返回中文原因。

    H1：先写同目录 .tmp 临时文件，成功后 os.replace 原子改名——写入中途被杀不会在缓存里
    留下半截 wav 被下次运行当成有效音频。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "未找到 ffmpeg，无法抽取 16k 单声道音频。"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = wav_path.with_name(wav_path.name + ".tmp")
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        # 临时文件带 .tmp 后缀后 ffmpeg 无法按扩展名推断封装格式，必须显式指定 wav。
        "-f",
        "wav",
        str(tmp_path),
    ]
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
        _remove_quietly(tmp_path)
        return f"音频抽取超时（>{FFMPEG_TIMEOUT_SECONDS} 秒）。"
    if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size <= 44:
        reason = result.stderr.strip() or result.stdout.strip() or f"ffmpeg 返回码 {result.returncode}"
        _remove_quietly(tmp_path)
        return f"音频抽取失败：{reason[-800:]}"
    os.replace(tmp_path, wav_path)
    return None


def cut_block_audio(wav_path: Path, start: int, end: int, output_path: Path) -> str | None:
    """从整段 wav 切出 [start, end) 秒的块音频；失败返回中文原因。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "未找到 ffmpeg，无法切分音频块。"
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(wav_path),
        "-ss",
        str(start),
        "-t",
        str(max(1, end - start)),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_path),
    ]
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
        return f"音频块切分超时（>{FFMPEG_TIMEOUT_SECONDS} 秒）。"
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 44:
        reason = result.stderr.strip() or result.stdout.strip() or f"ffmpeg 返回码 {result.returncode}"
        return f"音频块切分失败：{reason[-800:]}"
    return None


def ensure_audio_note(note: InboxNote) -> tuple[Path | None, float, str | None]:
    """拿到可切的 16k wav：缓存 wav → 缓存媒体抽音频 → 按 原始链接+作品页链接 重取再抽。

    返回 (wav 路径, 重取媒体的 ffprobe 时长秒（缓存命中时为 0）, 错误)。
    """
    platform = note.field("来源平台")
    content_id = media_cache_id(note)
    client = platform_client(platform)
    try:
        cache_dir = client.media_cache_dir(content_id)
    except Exception as exc:  # noqa: BLE001
        return None, 0.0, f"媒体缓存目录不可用：{exc}"

    cached_wav, cached_media = find_cached_audio_or_media(cache_dir)
    if cached_wav is not None:
        return cached_wav, 0.0, None
    if cached_media is not None:
        error = run_ffmpeg_extract(cached_media, cache_dir / AUDIO_WAV_NAME)
        if error is None:
            return cache_dir / AUDIO_WAV_NAME, 0.0, None

    media_path, fetch_error = refetch_media(platform, [note.field("来源链接"), note.field("原始链接")], content_id)
    if media_path is None:
        return None, 0.0, fetch_error or "媒体重取失败：未知原因。"
    wav_path = cache_dir / AUDIO_WAV_NAME
    error = run_ffmpeg_extract(media_path, wav_path)
    if error is not None:
        return None, 0.0, error
    duration = 0.0
    try:
        probe = client.ffprobe_check(media_path)
        duration = float(probe.get("duration_seconds") or 0)
    except Exception:  # noqa: BLE001 - 时长仅用于跳过区间解释，探测失败不阻断。
        duration = 0.0
    return wav_path, duration, None


# ---------------------------------------------------------------------------
# venv 子进程：Silero VAD（faster_whisper.vad，零新依赖）与 faster-whisper 转写
# ---------------------------------------------------------------------------

VAD_HELPER = r'''
import json, sys, wave
import numpy as np
from faster_whisper.vad import get_speech_timestamps, VadOptions

wav_path = sys.argv[1]
with wave.open(wav_path, "rb") as handle:
    rate = handle.getframerate()
    frames = handle.getnframes()
    raw = handle.readframes(frames)
audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
options = VadOptions(min_silence_duration_ms=%d, speech_pad_ms=%d)
chunks = get_speech_timestamps(audio, options, sampling_rate=rate)
intervals = [[round(chunk["start"] / rate, 3), round(chunk["end"] / rate, 3)] for chunk in chunks]
print(json.dumps({"sampling_rate": rate, "duration": round(frames / rate, 3) if rate else 0.0, "intervals": intervals}))
''' % (VAD_MIN_SILENCE_MS, VAD_SPEECH_PAD_MS)

WHISPER_SERVER_HELPER = r'''
import json, sys
from faster_whisper import WhisperModel

model = WhisperModel(sys.argv[1], device="auto", compute_type="int8")
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"转写请求不是 JSON：{exc}"}, ensure_ascii=False), flush=True)
        continue
    if request.get("stop"):
        break
    try:
        iterator, info = model.transcribe(request["audio"], language=None, vad_filter=True)
        segments = [
            {"start": round(segment.start, 3), "end": round(segment.end, 3), "text": (segment.text or "").strip()}
            for segment in iterator
            if (segment.text or "").strip()
        ]
        print(json.dumps({"language": getattr(info, "language", "") or "", "segments": segments}, ensure_ascii=False), flush=True)
    except Exception as exc:  # 单块失败回传错误，不杀常驻进程。
        print(json.dumps({"error": f"块转写异常：{exc!r}"}, ensure_ascii=False), flush=True)
'''


def candidate_transcribe_pythons() -> list[str]:
    """复用 capture_link.candidate_faster_whisper_pythons（venv python 优先）。"""
    try:
        return list(_capture_link().candidate_faster_whisper_pythons())
    except RuntimeError:
        return []


def run_vad(wav_path: Path) -> tuple[list[tuple[float, float]], float, str | None]:
    """在 venv 里跑 Silero VAD；返回 (语音区间, wav 时长秒, 错误)。

    子进程输出按 UTF-8 容错解码（errors=replace）：脏输出/非 UTF-8 字节不会让笔记进程崩溃，
    统一走「VAD 输出不是 JSON」的中文错误路径。
    """
    attempted: list[str] = []
    for python in candidate_transcribe_pythons():
        probe = subprocess.run(
            [python, "-c", "import numpy; from faster_whisper.vad import get_speech_timestamps"],
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if probe.returncode != 0:
            attempted.append(f"{python} 缺少 VAD 依赖：{(probe.stderr or probe.stdout).strip()[-300:]}")
            continue
        try:
            result = subprocess.run(
                [python, "-c", VAD_HELPER, str(wav_path)],
                text=True,
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=VAD_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            attempted.append(f"{python}：VAD 超时（>{VAD_TIMEOUT_SECONDS} 秒）。")
            continue
        if result.returncode != 0:
            reason = result.stderr.strip() or result.stdout.strip() or f"返回码 {result.returncode}"
            attempted.append(f"{python}：VAD 失败：{reason[-500:]}")
            continue
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            attempted.append(f"{python}：VAD 输出不是 JSON。")
            continue
        intervals = [(float(item[0]), float(item[1])) for item in data.get("intervals", []) if len(item) >= 2]
        return intervals, float(data.get("duration") or 0.0), None
    return [], 0.0, "未找到可运行 Silero VAD 的 faster-whisper Python 环境。\n" + "\n".join(attempted)


class _WhisperWorker:
    """常驻 faster-whisper 子进程：模型只加载一次，逐块按行 JSON 协议复用（进程内不再逐块重载）。"""

    def __init__(self, python: str) -> None:
        self.python = python
        self.proc: subprocess.Popen[str] | None = None
        self._stderr = tempfile.TemporaryFile(prefix="my-mind-seg-fw-err-")
        self._lines: queue.Queue[str | None] = queue.Queue()

    def start(self) -> str | None:
        """启动常驻进程并起后台读线程；失败返回中文原因。"""
        try:
            self.proc = subprocess.Popen(
                [self.python, "-c", WHISPER_SERVER_HELPER, TRANSCRIBE_MODEL],
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            return f"启动常驻转写进程失败：{exc}"
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        return None

    def _pump_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)  # 进程退出（EOF）哨兵。

    def transcribe(self, block_wav: Path) -> tuple[list[dict[str, Any]], str, str | None]:
        """转写单个块：一行请求换一行应答；任何异常返回中文错误并关闭本 worker。"""
        proc = self.proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return [], "", "常驻转写进程已退出。"
        request = json.dumps({"audio": str(block_wav)}, ensure_ascii=False)
        try:
            proc.stdin.write(request + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return [], "", self._crash_reason("写入常驻转写进程失败")
        try:
            line = self._lines.get(timeout=BLOCK_TRANSCRIBE_TIMEOUT_SECONDS)
        except queue.Empty:
            self.close()
            return [], "", f"块转写超时（>{BLOCK_TRANSCRIBE_TIMEOUT_SECONDS} 秒），已关闭常驻进程。"
        if line is None:
            return [], "", self._crash_reason("常驻转写进程中途退出")
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return [], "", self._crash_reason("常驻转写进程输出不是 JSON")
        if data.get("error"):
            return [], "", str(data["error"])
        segments = [item for item in data.get("segments", []) if isinstance(item, dict) and item.get("text")]
        return segments, str(data.get("language") or ""), None

    def _crash_reason(self, prefix: str) -> str:
        """附上 stderr 尾部便于排查，并关闭 worker（下次调用会重建新进程）。"""
        detail = ""
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            detail = self._stderr.read().decode("utf-8", "replace").strip()[-500:]
        except (OSError, ValueError):
            detail = ""
        self.close()
        return f"{prefix}{'：' + detail if detail else ''}"

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        try:
            self._stderr.close()
        except OSError:
            pass


_whisper_worker: _WhisperWorker | None = None  # 进程内复用的常驻转写进程（模型只加载一次）


def transcribe_block(block_wav: Path) -> tuple[list[dict[str, Any]], str, str | None]:
    """用 venv 的 faster-whisper（small/int8/语言自动）转写单个音频块。

    模型经常驻子进程在进程内只加载一次，逐块按行 JSON 复用；复用失败（超时/崩溃/脏输出）时
    关闭重建再试一轮，全部失败返回中文错误（笔记不崩，走零进展/失败路径）。
    """
    global _whisper_worker
    attempted: list[str] = []
    if _whisper_worker is not None:
        segments, language, error = _whisper_worker.transcribe(block_wav)
        if error is None:
            return segments, language, None
        attempted.append(f"常驻进程复用失败（将重建）：{error}")
        _whisper_worker.close()
        _whisper_worker = None
    for python in candidate_transcribe_pythons():
        probe = subprocess.run(
            [python, "-c", "import faster_whisper"],
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if probe.returncode != 0:
            attempted.append(f"{python} 不可用：{(probe.stderr or probe.stdout).strip()[-300:]}")
            continue
        worker = _WhisperWorker(python)
        start_error = worker.start()
        if start_error is not None:
            attempted.append(f"{python}：{start_error}")
            worker.close()
            continue
        segments, language, error = worker.transcribe(block_wav)
        if error is not None:
            attempted.append(f"{python}：{error}")
            worker.close()
            continue
        _whisper_worker = worker
        return segments, language, None
    return [], "", "faster-whisper 块转写失败。\n" + "\n".join(attempted)


def close_whisper_worker() -> None:
    """运行结束关闭常驻转写进程（main 的 finally 调用；测试直接调用 process_note 时靠守护线程兜底）。"""
    global _whisper_worker
    if _whisper_worker is not None:
        _whisper_worker.close()
        _whisper_worker = None


# ---------------------------------------------------------------------------
# 状态机：单笔记处理
# ---------------------------------------------------------------------------


@dataclass
class NoteRunResult:
    path: Path
    status: str = ""
    done_seconds: float = 0.0
    speech_total: float = 0.0
    completed_blocks: int = 0
    zero_progress: bool = False
    remark: str = ""
    error: str = ""


def coverage_seconds(state: dict[str, Any]) -> tuple[float, float]:
    done = sum(block["sp"] for block in state["segments"] if block.get("done"))
    total = float(state.get("speech_total") or 0) or sum(block["sp"] for block in state["segments"])
    return done, total


def coverage_text(done: float, total: float) -> str:
    return f"{int(round(done))}/{int(round(total))}秒"


def modality_label(status: str, ratio: float) -> str:
    if status == STATUS_DONE:
        return "100%"
    if status == STATUS_FAILED:
        return "失败"
    if status == STATUS_PENDING:
        return "未开始(分段)"
    if ratio >= TRANSCRIPT_COMPLETE_RATIO - 1e-9:
        return "100%"
    return f"{max(0, min(100, round(ratio * 100)))}%"


def write_progress(note: InboxNote, state: dict[str, Any], status: str, remark: str) -> None:
    """把汇总进度写回笔记：frontmatter 三字段 + 模态覆盖 + 分段清单注释（其余内容不动）。"""
    done, total = coverage_seconds(state)
    ratio = (done / total) if total > 0 else 0.0
    note.set_field("转写状态", status)
    note.set_field("转写覆盖", coverage_text(done, total))
    note.set_field("转写备注", remark)
    note.set_modality_transcript(modality_label(status, ratio))
    note.set_body_info("转写状态", status)
    note.set_body_info("转写覆盖", coverage_text(done, total))
    note.set_segments_state(state)


def cleanup_media_cache(platform: str, content_id: str) -> None:
    """转写完成后删除媒体缓存整目录（决策 1）；失败吞掉。"""
    try:
        platform_client(platform).cleanup_media(content_id)
    except Exception:  # noqa: BLE001
        pass


def finalize_note(note: InboxNote, state: dict[str, Any], platform: str, content_id: str, result: NoteRunResult) -> None:
    """覆盖 ≥98%：合并全部完成块文本，生成摘要/关键点/时间线/摘录，重评内容质量门禁。"""
    capture = _capture_link()
    blocks = [block for block in state["segments"] if block.get("done")]
    pending = [block for block in state["segments"] if not block.get("done")]
    full_text = "\n".join((block.get("text") or "").strip() for block in blocks).strip()
    global_segments = [
        {"time": capture.fmt_duration(offset_start), "text": text}
        for block in blocks
        for offset_start, _offset_end, text in block.get("segs") or []
    ]
    summary = capture.summarize_transcript_text(full_text)
    timeline = capture.build_transcript_timeline(global_segments)
    content_extract = {
        "source": "长视频分段转写",
        "backend": "faster-whisper",
        "model": TRANSCRIBE_MODEL,
        "language": str(state.get("language") or ""),
        "segment_count": len(global_segments),
        "word_count": summary["word_count"],
        "summary": summary["summary"],
        "key_points": summary["key_points"],
        "timeline": timeline,
        "excerpt": summary["excerpt"],
    }
    quality, gate = capture.assess_content_quality(
        platform=note.field("来源平台") or platform,
        material_type=note.field("资料类型") or "社媒链接",
        parse_status=note.field("解析状态") or "已解析",
        description=note.body_section_text("## 文案摘录"),
        platform_meta={},
        content_extract=content_extract,
        content_extract_error="",
        image_ocr=None,
        image_ocr_error="",
        youtube_subtitle=None,
        external_transcript=None,
        work_type=note.field("作品类型") or "",
    )

    done, total = coverage_seconds(state)
    skipped = state.get("skipped") or []
    skip_seconds = sum(float(item.get("e") or 0) - float(item.get("s") or 0) for item in skipped)
    remark = (
        f"分段转写完成：{len(blocks)}/{len(state['segments'])} 块，覆盖 {coverage_text(done, total)}（≥98%）。"
        + (f"剩余 {len(pending)} 块（约 {int(round(sum(b['sp'] for b in pending)))} 秒语音）不足 2%，按完整判定收尾。" if pending else "")
    )

    note.set_field("转写状态", STATUS_DONE)
    note.set_field("转写覆盖", coverage_text(done, total))
    note.set_field("转写备注", remark)
    note.set_modality_transcript("100%")
    note.set_field("内容质量", quality)
    note.set_field("质量门禁", gate)
    note.set_field("内容摘录来源", "长视频分段转写")
    note.set_field("内容摘录后端", "faster-whisper")
    note.set_field("内容摘录字数", summary["word_count"])
    note.set_body_info("转写状态", STATUS_DONE)
    note.set_body_info("转写覆盖", coverage_text(done, total))
    note.set_body_info("内容质量", quality)
    note.set_body_info("质量门禁", gate)

    section: list[str] = [
        "## 视频内容摘录",
        "",
        "- 来源：长视频分段转写（按语音停顿分段，覆盖 ≥98%）",
        "- 后端：faster-whisper",
        f"- 模型：{TRANSCRIBE_MODEL}",
        f"- 语言：{content_extract['language'] or '自动检测'}",
        f"- 转写片段数：{content_extract['segment_count']}",
        f"- 分段块数：{len(blocks)}/{len(state['segments'])}",
        f"- 估算字数：{content_extract['word_count']}",
        f"- 语音总时长：{int(round(total))} 秒；无语音跳过区间 {len(skipped)} 处（约 {int(round(skip_seconds))} 秒，静默/BGM 不转写）",
        "",
        "说明：长视频分段转写已完成，本节由全部分块合并生成摘要、关键点、时间线摘录和完整转写摘录。",
    ]
    if content_extract["summary"]:
        section.extend(["", "### 摘要", "", str(content_extract["summary"])])
    if content_extract["key_points"]:
        section.extend(["", "### 关键点", ""])
        section.extend(f"- {point}" for point in content_extract["key_points"][:7])
    if timeline:
        section.extend(["", "### 时间线摘录", ""])
        for item in timeline:
            section.append(f"- {item.get('time', '')}：{item.get('text', '')}")
    if content_extract["excerpt"]:
        section.extend(["", "### 转写摘录", "", str(content_extract["excerpt"])])
    note.replace_extract_section(section)

    result.status = STATUS_DONE
    result.remark = remark
    cleanup_media_cache(platform, content_id)


def process_note(path: Path, *, deadline: float) -> NoteRunResult:
    """处理单条笔记：备音频 → （首次）VAD 分段 → 预算内逐块转写 → 覆盖判定/收尾。"""
    note = InboxNote(path)
    result = NoteRunResult(path=path)
    platform = note.field("来源平台")
    content_id = media_cache_id(note)

    previous_status = note.field("转写状态")
    if previous_status == STATUS_DONE:
        result.status = STATUS_DONE
        result.remark = "笔记已完成分段转写，跳过。"
        return result

    # M4：并发双跑防护——媒体缓存目录内的轻量 advisory 锁（拿不到锁则本轮跳过该笔记）。
    lock_dir: Path | None = None
    try:
        lock_dir = platform_client(platform).media_cache_dir(content_id)
    except Exception:  # noqa: BLE001 - 锁目录不可用（平台不支持等）不阻断，按无锁降级处理。
        lock_dir = None
    lock_path: Path | None = None
    if lock_dir is not None:
        lock_path, lock_reason = acquire_transcribe_lock(lock_dir)
        if lock_path is None:
            result.remark = f"跳过该笔记：{lock_reason}"
            print(f"分段转写跳过 {path.name}：{lock_reason}")
            return result

    try:
        return _process_note_locked(note, result, platform, content_id, deadline=deadline)
    finally:
        release_transcribe_lock(lock_path)


def _process_note_locked(
    note: InboxNote,
    result: NoteRunResult,
    platform: str,
    content_id: str,
    *,
    deadline: float,
) -> NoteRunResult:
    """process_note 持锁后的主体；锁由调用方在 finally 释放。"""
    previous_status = note.field("转写状态")

    state: dict[str, Any] | None = None
    try:
        state = note.segments_state()
    except SegmentStateError as exc:
        _mark_failed(note, None, f"分段清单损坏：{exc}；需人工核验。", result)
        note.save()
        return result

    if previous_status == STATUS_FAILED:
        # 显式 --note 重跑失败笔记：清零连续零进展计数，重新尝试。
        if state is not None:
            state["zero_progress_runs"] = 0

    # 1) 媒体与音频（缓存优先，丢失重取）。
    try:
        wav_path, probed_duration, audio_error = ensure_audio_note(note)
    except Exception as exc:  # noqa: BLE001 - 平台不支持/缓存目录异常等按媒体失败处理。
        wav_path, probed_duration, audio_error = None, 0.0, f"{exc}"
    if audio_error or wav_path is None:
        _mark_failed(note, state, f"媒体重取失败：{audio_error or '未获得可用音频'}", result)
        note.save()
        return result

    # 2) 首次运行：VAD 分段规划并立即回写（进行中 + 0/N秒）。
    if state is None or not state.get("segments"):
        intervals, vad_duration, vad_error = run_vad(wav_path)
        if vad_error:
            if vad_error.startswith("未找到可运行 Silero VAD"):
                _mark_failed(note, state, f"VAD 环境不可用：{vad_error}", result)
                note.save()
                return result
            _record_zero_progress(note, state, f"VAD 失败：{vad_error}", result)
            note.save()
            return result
        blocks = plan_blocks(intervals)
        if not blocks:
            _mark_failed(note, state, "整条媒体未检测到语音区间（纯静默/BGM？），无可转写内容，请人工核验。", result)
            note.save()
            return result
        media_duration = probed_duration or vad_duration
        state = {
            "v": 1,
            "media_duration": round(float(media_duration), 1),
            "speech_total": round(sum(block["sp"] for block in blocks), 1),
            # 保留此前零进展存根里的计数（VAD 失败重试成功的场景）。
            "zero_progress_runs": int((state or {}).get("zero_progress_runs") or 0),
            "language": "",
            "planned_at": now_datetime(),
            "segments": blocks,
            "skipped": skipped_intervals(blocks, media_duration),
        }
        done, total = coverage_seconds(state)
        write_progress(note, state, STATUS_RUNNING, f"VAD 分段完成：{len(blocks)} 块 / {int(round(total))} 秒语音，开始逐块转写。")
        note.save()

    # 3) 预算内逐块转写（已完成块直接跳过；每块完成即回写，防中断丢进度）。
    speech_total = float(state.get("speech_total") or 0) or sum(block["sp"] for block in state["segments"])
    last_error = ""
    budget_note = ""
    with tempfile.TemporaryDirectory(prefix="my-mind-seg-cut-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for index, block in enumerate(state["segments"]):
            if block.get("done"):
                continue
            if result.completed_blocks > 0 and _monotonic() >= deadline:
                budget_note = "转写预算耗尽，剩余块待下次续跑。"
                break
            block_wav = temp_dir / f"block-{index:04d}.wav"
            cut_error = cut_block_audio(wav_path, int(block["s"]), int(block["e"]), block_wav)
            if cut_error:
                last_error = cut_error
                continue
            segments, language, transcribe_error = transcribe_block(block_wav)
            if transcribe_error or not segments:
                last_error = transcribe_error or "转写未返回文本。"
                continue
            try:
                text = _capture_link().normalize_cjk_text("\n".join(str(item.get("text") or "") for item in segments))
            except RuntimeError:
                text = "\n".join(str(item.get("text") or "") for item in segments)
            block["done"] = True
            block["text"] = text
            block["segs"] = [
                [round(float(block["s"]) + float(item.get("start") or 0), 3), round(float(block["s"]) + float(item.get("end") or 0), 3), str(item.get("text") or "")]
                for item in segments
            ]
            state["language"] = language or state.get("language") or ""
            result.completed_blocks += 1
            done, total = coverage_seconds(state)
            write_progress(note, state, STATUS_RUNNING, f"第 {index + 1} 块完成，覆盖 {coverage_text(done, total)}。")
            note.save()

    # 4) 覆盖判定与收尾。
    done_seconds, total_seconds = coverage_seconds(state)
    result.done_seconds, result.speech_total = done_seconds, total_seconds
    ratio = (done_seconds / total_seconds) if total_seconds > 0 else 0.0
    if ratio >= TRANSCRIPT_COMPLETE_RATIO - 1e-9:
        finalize_note(note, state, platform, content_id, result)
        note.save()
        return result

    if result.completed_blocks == 0:
        _record_zero_progress(note, state, last_error or budget_note or "本轮没有完成任何分段块。", result)
        note.save()
        return result

    state["zero_progress_runs"] = 0
    pending = sum(1 for block in state["segments"] if not block.get("done"))
    remark = budget_note or (f"已完成 {result.completed_blocks} 块，剩余 {pending} 块待续跑。")
    if last_error:
        remark = f"{remark}（最近失败：{last_error[-300:]}）"
    write_progress(note, state, STATUS_RUNNING, remark)
    note.save()
    result.status = STATUS_RUNNING
    result.remark = remark
    return result


def _mark_failed(note: InboxNote, state: dict[str, Any] | None, reason: str, result: NoteRunResult) -> None:
    result.status = STATUS_FAILED
    result.remark = reason
    note.set_field("转写状态", STATUS_FAILED)
    note.set_field("转写备注", reason)
    note.set_modality_transcript("失败")
    note.set_body_info("转写状态", STATUS_FAILED)
    if state is not None:
        done, total = coverage_seconds(state)
        note.set_field("转写覆盖", coverage_text(done, total))
        note.set_body_info("转写覆盖", coverage_text(done, total))
        note.set_segments_state(state)
    else:
        note.set_field("转写覆盖", "0/0秒")


def _record_zero_progress(note: InboxNote, state: dict[str, Any] | None, reason: str, result: NoteRunResult) -> None:
    """零进展记账：计数存分段清单注释；连续 2 次零进展 → 失败（设计稿子设计三）。"""
    result.zero_progress = True
    if state is None:
        # 还没有分段清单（如 VAD 失败）：写入空清单存根，让计数能跨运行保留。
        state = {
            "v": 1,
            "media_duration": 0.0,
            "speech_total": 0.0,
            "zero_progress_runs": 0,
            "language": "",
            "planned_at": "",
            "segments": [],
            "skipped": [],
        }
    runs = int(state.get("zero_progress_runs") or 0) + 1
    state["zero_progress_runs"] = runs
    if runs >= 2:
        _mark_failed(note, state, f"连续 {runs} 次运行零进展（最近原因：{reason}），已标失败，转人工核验。", result)
        return
    status = STATUS_RUNNING if state.get("segments") else STATUS_PENDING
    result.status = status
    result.remark = f"本轮零进展：{reason}（零进展计数 {runs}/2，再连续一次将标失败）。"
    write_progress(note, state, status, result.remark)


# ---------------------------------------------------------------------------
# 收件箱扫描、运行记录与 CLI
# ---------------------------------------------------------------------------


def scan_inbox(inbox: Path, max_notes: int) -> list[Path]:
    """按文件名时间序扫描收件箱中 转写状态 ∈ {未开始, 进行中} 的笔记，最多 max_notes 条。"""
    if not inbox.is_dir():
        return []
    selected: list[Path] = []
    for path in sorted(inbox.glob("*.md")):
        if len(selected) >= max_notes:
            break
        try:
            note = InboxNote(path)
        except (OSError, UnicodeDecodeError):
            continue
        if note.field("转写状态") in ACTIVE_STATUSES:
            selected.append(path)
    return selected


def _unique_path(directory: Path, filename: str) -> Path:
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
    results: list[NoteRunResult],
    *,
    budget_seconds: int,
    elapsed_seconds: float,
    scanned_candidates: int,
    extra_args: str = "",
) -> Path:
    """把运行证据写入 85_运行记录/分段转写-YYYY-MM-DD-HHMM.md（仓库运行记录惯例 frontmatter）。"""
    now = dt.datetime.now(TZ)
    lines = [
        "---",
        "类别: 运行记录",
        "记录类型: 分段转写",
        f"生成时间: {now.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"预算秒数: {budget_seconds}",
        f"实际耗时秒: {round(elapsed_seconds)}",
        f"处理笔记数: {len(results)}",
        "---",
        "",
        "# 分段转写运行记录",
        "",
        f"- 候选笔记数：{scanned_candidates}",
        f"- 处理笔记数：{len(results)}",
        f"- 完成分段块数：{sum(item.completed_blocks for item in results)}",
        f"- 零进展笔记：{sum(1 for item in results if item.zero_progress) or '无'}",
    ]
    if extra_args:
        lines.append(f"- 调用参数：{extra_args}")
    lines.extend(["", "## 笔记明细", ""])
    if not results:
        lines.append("- 本轮没有可处理的笔记。")
    for item in results:
        total = item.speech_total or 0
        ratio = (item.done_seconds / total) if total > 0 else 0.0
        percent = f"{round(ratio * 100)}%" if total > 0 else "0%"
        lines.extend(
            [
                f"### {item.path.name}",
                "",
                f"- 转写状态：{item.status or '未知'}",
                f"- 转写覆盖：{coverage_text(item.done_seconds, total)}（{percent}）",
                f"- 本次完成块数：{item.completed_blocks}",
                f"- 备注：{item.remark or '无'}",
                "",
            ]
        )
    RUN_RECORD_DIR.mkdir(parents=True, exist_ok=True)
    record_path = _unique_path(RUN_RECORD_DIR, f"分段转写-{now.strftime('%Y-%m-%d-%H%M')}.md")
    record_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return record_path


def _describe_dry_run(note_path: Path) -> str:
    try:
        note = InboxNote(note_path)
    except (OSError, UnicodeDecodeError) as exc:
        return f"- {note_path.name}：无法读取（{exc}）"
    status = note.field("转写状态") or "无"
    coverage = note.field("转写覆盖") or "无"
    try:
        state = note.segments_state()
    except SegmentStateError:
        state = None
    if state is not None:
        done_blocks = sum(1 for block in state["segments"] if block.get("done"))
        pending_blocks = len(state["segments"]) - done_blocks
        detail = f"分段 {done_blocks}/{len(state['segments'])} 块已完成，待转写 {pending_blocks} 块"
    else:
        detail = "尚未 VAD 分段（首次运行会先取媒体并分段）"
    return f"- {note_path.name}：转写状态={status}，覆盖={coverage}，{detail}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="长视频分段转写状态机：处理收件箱中排队/进行中的长视频笔记（设计稿子设计三）。",
    )
    parser.add_argument(
        "--budget-seconds",
        type=int,
        default=DEFAULT_BUDGET_SECONDS,
        help=f"本次运行的转写预算（秒），默认 {DEFAULT_BUDGET_SECONDS}；预算耗尽后完成当前块即收尾",
    )
    parser.add_argument(
        "--note",
        default="",
        help="只处理指定的收件箱笔记路径（默认扫描 00_收件箱 中最早的待转写笔记）",
    )
    parser.add_argument(
        "--max-notes",
        type=int,
        default=DEFAULT_MAX_NOTES,
        help=f"单次运行最多考虑的笔记条数，默认 {DEFAULT_MAX_NOTES}",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的笔记与当前进度，不转写、不写任何文件")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = _monotonic()
    try:
        if args.note:
            note_path = Path(args.note)
            if not note_path.is_absolute():
                note_path = ROOT / note_path
            if not note_path.is_file():
                print(f"分段转写硬错误：笔记不存在 {note_path}", file=sys.stderr)
                return 1
            candidates = [note_path]
        else:
            candidates = scan_inbox(DEFAULT_INBOX, max(1, args.max_notes))

        if args.dry_run:
            print(f"分段转写 dry-run：候选 {len(candidates)} 条笔记（不执行转写、不写任何文件）")
            for path in candidates:
                print(_describe_dry_run(path))
            return 0

        deadline = started + max(0, args.budget_seconds)
        results: list[NoteRunResult] = []
        for path in candidates:
            if results and _monotonic() >= deadline:
                break  # 预算耗尽：不再开新笔记（首条笔记至少尝试一块）。
            try:
                results.append(process_note(path, deadline=deadline))
            except Exception as exc:  # noqa: BLE001 - 单笔记异常不拖垮整轮运行。
                results.append(NoteRunResult(path=path, status="异常", remark=f"处理异常：{exc!r}", error=str(exc)))

        elapsed = _monotonic() - started
        extra_args = "--note " + args.note if args.note else ""
        record_path = write_run_record(
            results,
            budget_seconds=args.budget_seconds,
            elapsed_seconds=elapsed,
            scanned_candidates=len(candidates),
            extra_args=extra_args,
        )

        total_blocks = sum(item.completed_blocks for item in results)
        zero_names = [item.path.name for item in results if item.zero_progress]
        print(
            f"分段转写运行完成：处理 {len(results)} 条笔记，完成 {total_blocks} 块"
            f"（预算 {args.budget_seconds} 秒，实际 {elapsed:.0f} 秒；运行记录 {record_path.relative_to(ROOT) if record_path.is_relative_to(ROOT) else record_path}）"
        )
        for item in results:
            total = item.speech_total or 0
            percent = f"{round(item.done_seconds / total * 100)}%" if total > 0 else "0%"
            print(f"- {item.path.name}：{item.status or '未知'}，覆盖 {coverage_text(item.done_seconds, total)}（{percent}），本轮完成 {item.completed_blocks} 块")
        print(f"零进展笔记：{'、'.join(zero_names) if zero_names else '无'}")
        return 0
    except Exception as exc:  # noqa: BLE001 - 硬错误（参数/存储不可用等）。
        print(f"分段转写硬错误：{exc!r}", file=sys.stderr)
        return 1
    finally:
        close_whisper_worker()  # 常驻转写进程随运行结束关闭（正常/异常路径都收口）。


if __name__ == "__main__":
    raise SystemExit(main())
