"""keyframe_vision.py（批次 5：UI 演示视频画面解析）的全 mock 测试。

ffmpeg / HTTP / 视觉模型 / OCR / 平台客户端全部打桩，不发真实网络请求、不跑真实 ffmpeg。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".codex/skills/inbox-capture/scripts"
KEYFRAME_SCRIPT = SCRIPTS / "keyframe_vision.py"
SEG_SCRIPT = SCRIPTS / "segmented_transcribe.py"
CAPTURE_SCRIPT = SCRIPTS / "capture_link.py"

MEDIA_URL = "https://cdn.douyin.test/video/7704156396664769811.mp4"
# 注意：这段文案刻意不含 UI 演示关键词（演示/操作/教程/界面/tutorial/how to），
# 避免干扰「口播视频无关键词不进视觉确认」的测试。
DESCRIPTION = "这是一条抖音视频样本的分享文案，用来满足内容质量门禁对正文的最低要求，包含足够长的中文内容。" * 3
LONG_VISION_OUTPUT = (
    "1. 逐帧观察\n"
    "- [1.2s] 终端窗口显示命令 npm install，上方标题栏为 zsh。\n"
    "- [5.0s] 弹出系统授权弹窗，含「好」与「不允许」两个按钮。\n"
    "- [9.8s] 编辑器界面打开左侧文件树，标注框写着 step 3。\n"
    "2. 推断\n"
    "推断：用户在安装依赖后触发了系统权限申请，随后回到编辑器继续操作。\n"
    "3. 流程总结\n"
    "整段录屏演示了从安装依赖、确认授权到打开工程的操作流程，共三个主要步骤。"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_fake_jpeg(path: Path, size: int = 512) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"f" * size + b"\xff\xd9")
    return path


def write_note(
    directory: Path,
    name: str,
    *,
    platform: str = "抖音",
    content_id: str = "7704156396664769811",
    work_type: str = "UI演示视频",
    vision_status: str = "",
    vision_remark: str = "",
    type_source: str = "",
    confirm: str = "",
    title: str = "界面操作教程演示",
    modality_vision: str = "未开始",
    extract_words: str = "520",
    transcript_status: str = "",
    extra_frontmatter: str = "",
) -> Path:
    """构造一条抖音 UI 演示视频收件箱笔记（对齐 capture_link.build_note 的字段布局）。"""
    directory.mkdir(parents=True, exist_ok=True)
    page_url = f"https://www.douyin.com/video/{content_id}"
    modality = f"文案=完整 | 图片OCR=不适用 | 转写=已完成 | 画面解析={modality_vision}"
    lines = [
        "---",
        "类别: 收件箱",
        "资料类型: 社媒链接",
        f"作品类型: {work_type}",
        f"模态覆盖: {modality}",
        f"来源平台: {platform}",
        f"标题: {title}",
        "作者或频道: 某作者",
        f"来源链接: {page_url}",
        f"原始链接: https://v.douyin.com/share/{content_id}/",
        f"内容编号: {content_id}",
        "时长: 02:30",
    ]
    if transcript_status:
        lines.append(f"转写状态: {transcript_status}")
    if vision_status:
        lines.append(f"画面解析: {vision_status}")
    if vision_remark:
        lines.append(f"画面解析备注: {vision_remark}")
    if type_source:
        lines.append(f"作品类型来源: {type_source}")
    if confirm:
        lines.append(f"视觉确认: {confirm}")
    lines.extend(
        [
            "解析状态: 已解析",
            "内容质量: 需继续解析",
            "质量门禁: UI 演示视频的画面解析尚未执行，不应直接进入前台阅读推送。",
            "内容摘录来源: 公开视频音频转写",
            "内容摘录后端: faster-whisper",
            f"内容摘录字数: {extract_words}",
            "阅读状态: 已读",
            "---",
            "",
            f"# {title}",
            "",
            "## 基础信息",
            "",
            f"- 来源平台：{platform}",
            f"- 作品类型：{work_type}",
            f"- 模态覆盖：{modality}",
            "- 内容质量：需继续解析",
            "- 质量门禁：UI 演示视频的画面解析尚未执行。",
            "",
            "## 解析说明",
            "",
            "短视频一段式转写已完成。",
            "",
            "## 文案摘录",
            "",
            DESCRIPTION,
            "",
            "## 视频内容摘录",
            "",
            "- 来源：公开视频音频转写",
            "- 估算字数：520",
            "",
            "## 标签和分类",
            "",
            "### 分类",
            "",
            "- 暂无",
            "",
            "### 标签",
            "",
            "- 暂无",
            "",
            "## 原始链接",
            "",
            page_url,
        ]
    )
    if extra_frontmatter:
        lines.extend(["", extra_frontmatter])
    path = directory / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class FakePlatformClient:
    """替换 keyframe_vision.segmented.dtk_client 的假客户端：不发真实网络请求。"""

    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="my-mind-vision-cache-")
        self.root = Path(self.temp.name)
        self.cleaned: list[str] = []

    def cleanup(self) -> None:
        self.temp.cleanup()

    def media_cache_dir(self, content_id: str) -> Path:
        return self.root / content_id

    def cleanup_media(self, content_id: str) -> str:
        self.cleaned.append(content_id)
        return "已删除媒体缓存目录（测试）"


class FakeUrllibResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# 纯函数：抽帧命令 / 时间戳 / 抽样 / 预算
# ---------------------------------------------------------------------------


class FrameExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_frame_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_extract_command_uses_verified_scene_filter_and_scale(self) -> None:
        with mock.patch.object(self.kv.shutil, "which", return_value="/usr/bin/ffmpeg"):
            command = self.kv.build_extract_command(Path("a.mp4"), "/tmp/frame-%04d.jpg", long_edge=1280)
        self.assertEqual("/usr/bin/ffmpeg", command[0])
        vf_index = command.index("-vf")
        # 评审 H1：scale 必须是真「长边」语义（横屏限宽 1280、竖屏限高 1280），不能固定宽。
        self.assertEqual(
            "select='gt(scene,0.3)+isnan(prev_selected_t)+gte(t-prev_selected_t,30)',"
            "scale='if(gt(iw,ih),1280,-2)':'if(gt(iw,ih),-2,1280)',showinfo",
            command[vf_index + 1],
        )
        self.assertEqual("4", command[command.index("-q:v") + 1])
        self.assertEqual("0", command[command.index("-start_number") + 1])
        # ffmpeg 8 默认输出同步会用复制帧补回 CFR 时间轴（dup 帧），必须 passthrough。
        self.assertEqual("passthrough", command[command.index("-fps_mode") + 1])

    def test_extract_command_scale_is_true_long_edge_semantics(self) -> None:
        """必修 2：滤镜串包含条件表达式，长边数值从常量插值（改 frame_long_edge 配置也生效）。"""
        with mock.patch.object(self.kv.shutil, "which", return_value="/usr/bin/ffmpeg"):
            command = self.kv.build_extract_command(Path("a.mp4"), "/tmp/frame-%04d.jpg", long_edge=1024)
        video_filter = command[command.index("-vf") + 1]
        self.assertIn("scale='if(gt(iw,ih),1024,-2)':'if(gt(iw,ih),-2,1024)'", video_filter)
        # 竖屏（ih>iw）时宽度为 -2（按高 1024 等比缩放），横屏反之；不再出现固定宽的 1024:-2。
        self.assertNotIn("scale=1024:-2", video_filter)
        self.assertNotIn("scale=1280:-2", video_filter)

    def test_parse_pts_times_in_order(self) -> None:
        log = "[showinfo @ 0x1] n:0 pts_time:1.234\n[showinfo @ 0x1] n:1 pts_time:31.5\nnoise pts_time:x"
        self.assertEqual([1.234, 31.5], self.kv.parse_pts_times(log))

    def test_uniform_sample_limits_and_keeps_boundaries(self) -> None:
        items = list(range(20))
        sampled = self.kv.uniform_sample(items, 12)
        self.assertEqual(12, len(sampled))
        self.assertEqual(0, sampled[0])
        self.assertEqual(19, sampled[-1])
        self.assertEqual(sorted(sampled), sampled)
        self.assertEqual(list(range(5)), self.kv.uniform_sample(list(range(5)), 12))

    def test_extract_frames_pairs_files_with_timestamps_and_samples(self) -> None:
        out_dir = self.dir / "frames"

        def fake_run(command, **_kwargs):
            out_pattern = command[-1]
            start = int(command[command.index("-start_number") + 1])
            for index in range(start, start + 20):
                write_fake_jpeg(Path(out_pattern).parent / f"frame-{index:04d}.jpg")
            stderr = "".join(f"[showinfo] n:{index} pts_time:{index * 7.5:.3f}\n" for index in range(20))
            return subprocess.CompletedProcess(command, 0, "", stderr)

        with mock.patch.object(self.kv.subprocess, "run", side_effect=fake_run), mock.patch.object(
            self.kv.shutil, "which", return_value="/usr/bin/ffmpeg"
        ):
            frames, error = self.kv.extract_frames(Path("a.mp4"), out_dir, long_edge=1280, frame_limit=12)
        self.assertIsNone(error)
        self.assertEqual(12, len(frames))
        self.assertEqual(0.0, frames[0]["time"])
        self.assertEqual(142.5, frames[-1]["time"])
        self.assertTrue(all(Path(frame["path"]).exists() for frame in frames))

    def test_extract_frames_clears_stale_frames_before_run(self) -> None:
        # 视觉确认（2 帧）与完整分析共用 frames 目录：旧帧必须清理，不能混进新一轮时间戳配对。
        out_dir = self.dir / "frames"
        write_fake_jpeg(out_dir / "frame-0009.jpg")

        def fake_run(command, **_kwargs):
            out_pattern = command[-1]
            for index in range(2):
                write_fake_jpeg(Path(out_pattern).parent / f"frame-{index:04d}.jpg")
            stderr = "[showinfo] n:0 pts_time:1.0\n[showinfo] n:1 pts_time:2.0\n"
            return subprocess.CompletedProcess(command, 0, "", stderr)

        with mock.patch.object(self.kv.subprocess, "run", side_effect=fake_run), mock.patch.object(
            self.kv.shutil, "which", return_value="/usr/bin/ffmpeg"
        ):
            frames, error = self.kv.extract_frames(Path("a.mp4"), out_dir, long_edge=1280, frame_limit=12)
        self.assertIsNone(error)
        self.assertEqual(2, len(frames))
        self.assertEqual([1.0, 2.0], [frame["time"] for frame in frames])
        self.assertFalse((out_dir / "frame-0009.jpg").exists())

    def test_extract_frames_zero_files_is_hard_error(self) -> None:
        def fake_run(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(self.kv.subprocess, "run", side_effect=fake_run), mock.patch.object(
            self.kv.shutil, "which", return_value="/usr/bin/ffmpeg"
        ):
            frames, error = self.kv.extract_frames(Path("a.mp4"), self.dir / "frames", long_edge=1280, frame_limit=12)
        self.assertEqual([], frames)
        self.assertIsNotNone(error)
        self.assertIn("未能抽取到任何帧", error)

    def test_extract_frames_skipping_zero_byte_frame_drops_its_timestamp(self) -> None:
        """L1：0 字节坏帧跳过时同步丢弃对应时间戳，后续帧不错位配对。"""
        out_dir = self.dir / "frames"

        def fake_run(command, **_kwargs):
            out_pattern = command[-1]
            # 3 帧时间戳，但第 2 帧（index 1）写成 0 字节坏文件。
            for index in range(3):
                path = Path(out_pattern).parent / f"frame-{index:04d}.jpg"
                if index == 1:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"")
                else:
                    write_fake_jpeg(path)
            stderr = "".join(f"[showinfo] n:{index} pts_time:{index * 10:.3f}\n" for index in range(3))
            return subprocess.CompletedProcess(command, 0, "", stderr)

        with mock.patch.object(self.kv.subprocess, "run", side_effect=fake_run), mock.patch.object(
            self.kv.shutil, "which", return_value="/usr/bin/ffmpeg"
        ):
            frames, error = self.kv.extract_frames(Path("a.mp4"), out_dir, long_edge=1280, frame_limit=12)
        self.assertIsNone(error)
        self.assertEqual(2, len(frames))
        # 保留的两帧拿到的是各自的 pts_time（0s 与 20s），而不是 0s 与 10s。
        self.assertEqual([0.0, 20.0], [frame["time"] for frame in frames])

    def test_extract_frames_marks_estimated_timestamps_when_showinfo_insufficient(self) -> None:
        """L2：showinfo 时间戳不足时回退假时间戳，帧上带 time_estimated 标记。"""
        out_dir = self.dir / "frames"

        def fake_run(command, **_kwargs):
            out_pattern = command[-1]
            for index in range(3):
                write_fake_jpeg(Path(out_pattern).parent / f"frame-{index:04d}.jpg")
            stderr = "[showinfo] n:0 pts_time:1.500\n"  # 只有 1 条，覆盖不了 3 帧
            return subprocess.CompletedProcess(command, 0, "", stderr)

        with mock.patch.object(self.kv.subprocess, "run", side_effect=fake_run), mock.patch.object(
            self.kv.shutil, "which", return_value="/usr/bin/ffmpeg"
        ):
            frames, error = self.kv.extract_frames(Path("a.mp4"), out_dir, long_edge=1280, frame_limit=12)
        self.assertIsNone(error)
        self.assertEqual(1.5, frames[0]["time"])
        self.assertFalse(bool(frames[0].get("time_estimated")))
        self.assertTrue(bool(frames[1].get("time_estimated")))
        self.assertTrue(bool(frames[2].get("time_estimated")))
        self.assertIn("时间戳为估算", self.kv.frames_timestamp_note(frames))
        self.assertEqual("", self.kv.frames_timestamp_note(frames[:1]))

    def test_extract_frames_without_ffmpeg_returns_chinese_error(self) -> None:
        with mock.patch.object(self.kv.shutil, "which", return_value=None):
            frames, error = self.kv.extract_frames(Path("a.mp4"), self.dir / "frames", long_edge=1280, frame_limit=12)
        self.assertEqual([], frames)
        self.assertIn("未找到 ffmpeg", error)

    def test_fit_frames_budget_allows_12_frames_at_1280(self) -> None:
        count, edge, note = self.kv.fit_frames_budget(12, 1280)
        self.assertEqual(12, count)
        self.assertEqual(1280, edge)
        self.assertEqual("", note)

    def test_fit_frames_budget_reduces_frames_when_over_budget(self) -> None:
        count, edge, note = self.kv.fit_frames_budget(20, 1280)
        self.assertEqual(12, count)
        self.assertEqual(1280, edge)
        self.assertIn("降为 12 帧", note)

    def test_fit_frames_budget_compresses_edge_when_frames_floor_reached(self) -> None:
        # 长边 3584px 时单帧 ≈22k token：降到 2 帧仍超预算 → 压到 1024px。
        count, edge, note = self.kv.fit_frames_budget(4, 3584)
        self.assertEqual(1024, edge)
        self.assertEqual(4, count)
        self.assertIn("1024px", note)

    def test_fit_frames_budget_keeps_edge_when_frames_reduction_suffices(self) -> None:
        count, edge, note = self.kv.fit_frames_budget(20, 2560)
        self.assertEqual(2560, edge)
        self.assertEqual(3, count)
        self.assertIn("降为 3 帧", note)


# ---------------------------------------------------------------------------
# 缓存媒体选择（评审 M3）：只认视频容器，排除分段转写 wav
# ---------------------------------------------------------------------------


class CachedMediaSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_media_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cache = Path(temp.name) / "7704156396664769811"

    def _write(self, name: str, size: int) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / name).write_bytes(b"x" * size)

    def test_prefers_mp4_even_when_wav_is_larger(self) -> None:
        # 16k wav 可能比低码率 mp4 还大：不限容器会选中纯音频 → select 滤镜 0 帧。
        self._write("segmented-audio-16k.wav", 90_000_000)
        self._write("7704156396664769811.mp4", 8_000_000)
        selected = self.kv.find_cached_media(self.cache)
        self.assertIsNotNone(selected)
        self.assertEqual("7704156396664769811.mp4", selected.name)

    def test_wav_only_cache_counts_as_no_cache(self) -> None:
        self._write("segmented-audio-16k.wav", 90_000_000)
        self._write("other-audio.mp3", 5_000_000)
        self.assertIsNone(self.kv.find_cached_media(self.cache))

    def test_largest_video_container_wins(self) -> None:
        self._write("small.webm", 1_000)
        self._write("big.mov", 2_000)
        self._write("zero.mkv", 0)
        selected = self.kv.find_cached_media(self.cache)
        self.assertIsNotNone(selected)
        self.assertEqual("big.mov", selected.name)

    def test_missing_dir_returns_none(self) -> None:
        self.assertIsNone(self.kv.find_cached_media(self.cache))


# ---------------------------------------------------------------------------
# 输出防呆：str/list、空/超短、脑补警告、确认回答解析
# ---------------------------------------------------------------------------


class ModelOutputRobustnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_output_module")

    def test_extract_message_text_handles_str_and_segment_list(self) -> None:
        self.assertEqual("纯文本", self.kv.extract_message_text("纯文本"))
        segmented_content = [
            {"type": "text", "text": "第一段"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,x"}},
            {"type": "text", "text": "第二段"},
        ]
        self.assertEqual("第一段\n第二段", self.kv.extract_message_text(segmented_content))
        self.assertEqual("a\nb", self.kv.extract_message_text(["a", "b"]))
        self.assertEqual("", self.kv.extract_message_text(None))
        self.assertEqual("", self.kv.extract_message_text([{"type": "image_url"}]))

    def test_validate_model_output_rejects_empty_and_short(self) -> None:
        self.assertIn("视觉模型输出异常", self.kv.validate_model_output("", min_chars=100))
        self.assertIn("视觉模型输出异常", self.kv.validate_model_output("   \n  ", min_chars=100))
        self.assertIn("视觉模型输出异常", self.kv.validate_model_output("太短", min_chars=100))
        self.assertIsNone(self.kv.validate_model_output(LONG_VISION_OUTPUT, min_chars=100))

    def test_hallucination_warning_heuristic(self) -> None:
        text = " ".join(f"[{i}.0s] 观察{i}" for i in range(20))
        warning = self.kv.hallucination_warning(text, 12)
        self.assertIn("疑似脑补", warning)
        self.assertEqual("", self.kv.hallucination_warning(LONG_VISION_OUTPUT, 3))
        self.assertEqual("", self.kv.hallucination_warning("没有任何时间点", 3))

    def test_parse_confirm_answer(self) -> None:
        self.assertEqual(("是", ""), self.kv.parse_confirm_answer("是"))
        self.assertEqual(("否", ""), self.kv.parse_confirm_answer("回答：否"))
        self.assertEqual(("不确定", ""), self.kv.parse_confirm_answer("不确定"))
        self.assertEqual(("否", ""), self.kv.parse_confirm_answer("我认为不是软件界面"))
        answer, error = self.kv.parse_confirm_answer("完全看不懂")
        self.assertEqual("不确定", answer)
        self.assertIn("无法解析", error)
        answer, error = self.kv.parse_confirm_answer("")
        self.assertEqual("不确定", answer)
        self.assertIn("输出为空", error)


# ---------------------------------------------------------------------------
# HTTP：探活、str/list 响应、glm key redact、降级链
# ---------------------------------------------------------------------------


class BackendHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_backend_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.frames = [
            {"index": index, "path": write_fake_jpeg(self.dir / f"f{index}.jpg"), "time": float(index)}
            for index in range(3)
        ]

    def test_probe_backend_health_unreachable_mentions_readme(self) -> None:
        spec = self.kv.BackendSpec(name="local", base_url="http://127.0.0.1:8304", model="m")
        with mock.patch.object(
            self.kv.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            error = self.kv.probe_backend_health(spec)
        self.assertIsNotNone(error)
        self.assertIn("视觉服务不可用", error)
        self.assertIn("README 采集环境依赖", error)
        self.assertIn("8304", error)

    def test_probe_backend_health_ok_returns_none(self) -> None:
        spec = self.kv.BackendSpec(name="local", base_url="http://127.0.0.1:8304", model="m")
        with mock.patch.object(
            self.kv.urllib.request, "urlopen", return_value=FakeUrllibResponse(b'{"status":"ok"}')
        ) as urlopen:
            self.assertIsNone(self.kv.probe_backend_health(spec))
        request = urlopen.call_args[0][0]
        self.assertEqual("http://127.0.0.1:8304/health", request.full_url)
        self.assertEqual("GET", request.get_method())

    def test_chat_completion_builds_openai_request_and_parses_str(self) -> None:
        spec = self.kv.BackendSpec(name="local", base_url="http://127.0.0.1:8304", model="Qwen3-VL-8B-GGUF")
        body = json.dumps({"choices": [{"message": {"content": LONG_VISION_OUTPUT}}]}).encode()
        with mock.patch.object(self.kv.urllib.request, "urlopen", return_value=FakeUrllibResponse(body)) as urlopen:
            text, error = self.kv.chat_completion(spec, self.kv.build_messages(self.frames, "p"))
        self.assertIsNone(error)
        self.assertEqual(LONG_VISION_OUTPUT, text)
        request = urlopen.call_args[0][0]
        self.assertEqual("http://127.0.0.1:8304/v1/chat/completions", request.full_url)
        payload = json.loads(request.data.decode())
        self.assertEqual("Qwen3-VL-8B-GGUF", payload["model"])
        self.assertEqual(0.2, payload["temperature"])
        self.assertEqual(3000, payload["max_tokens"])

    def test_chat_completion_handles_segment_list_content(self) -> None:
        spec = self.kv.BackendSpec(name="local", base_url="http://127.0.0.1:8304", model="m")
        content = [{"type": "text", "text": "观察A"}, {"type": "text", "text": "推断B"}]
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        with mock.patch.object(self.kv.urllib.request, "urlopen", return_value=FakeUrllibResponse(body)):
            text, error = self.kv.chat_completion(spec, [{"role": "user", "content": "hi"}])
        self.assertIsNone(error)
        self.assertEqual("观察A\n推断B", text)

    def test_chat_completion_glm_error_never_leaks_api_key(self) -> None:
        api_key = "super-secret-glm-key-abc123"
        spec = self.kv.BackendSpec(name="glm", base_url="https://open.bigmodel.test/api/paas/v4", model="glm-4.6v", api_key=api_key)
        leaky_body = json.dumps({"error": {"message": f"invalid key {api_key}"}}).encode()

        class FakeHttpError(urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__("https://open.bigmodel.test/api/paas/v4/v1/chat/completions", 401, "Unauthorized", None, io.BytesIO(leaky_body))
                self.held: list[urllib.error.HTTPError] = []

        raised: list[urllib.error.HTTPError] = []

        def raise_http_error(*_args, **_kwargs):
            error_obj = FakeHttpError()
            raised.append(error_obj)
            raise error_obj

        self.addCleanup(lambda: [item.close() for item in raised])
        with mock.patch.object(self.kv.urllib.request, "urlopen", side_effect=raise_http_error):
            text, error = self.kv.chat_completion(spec, [{"role": "user", "content": "hi"}])
        self.assertEqual("", text)
        self.assertIsNotNone(error)
        self.assertNotIn(api_key, error)
        self.assertIn("***", error)
        self.assertIn("HTTP 401", error)

    def test_build_messages_inserts_timestamp_before_each_frame(self) -> None:
        messages = self.kv.build_messages(self.frames, "PROMPT")
        content = messages[0]["content"]
        texts = [item["text"] for item in content if item["type"] == "text"]
        self.assertEqual(["[0s]", "[1s]", "[2s]", "PROMPT"], texts)
        images = [item for item in content if item["type"] == "image_url"]
        self.assertEqual(3, len(images))
        self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_backend_chain_local_fail_then_glm_success(self) -> None:
        config = self.kv.default_config()
        config["glm"]["api_key"] = "k"
        glm_body = json.dumps({"choices": [{"message": {"content": LONG_VISION_OUTPUT}}]}).encode()

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            if url.endswith("/health"):
                raise urllib.error.URLError("refused")
            if ":8304/" in url:
                raise urllib.error.URLError("refused")
            return FakeUrllibResponse(glm_body)

        with mock.patch.object(self.kv.urllib.request, "urlopen", side_effect=fake_urlopen):
            backend, text, error = self.kv.run_vision_backends(
                config, self.frames, prompt="p", min_chars=100
            )
        self.assertEqual("glm", backend)
        self.assertIsNone(error)
        self.assertEqual(LONG_VISION_OUTPUT, text)

    def test_backend_chain_stops_at_local_success(self) -> None:
        config = self.kv.default_config()
        config["glm"]["api_key"] = "k"
        with mock.patch.object(self.kv, "probe_backend_health", return_value=None), mock.patch.object(
            self.kv,
            "chat_completion",
            side_effect=lambda spec, messages: (LONG_VISION_OUTPUT, None),
        ) as chat:
            backend, text, error = self.kv.run_vision_backends(config, self.frames, prompt="p", min_chars=100)
        self.assertEqual("local", backend)
        self.assertIsNone(error)
        self.assertEqual(1, chat.call_count)

    def test_backend_chain_short_output_fails_all(self) -> None:
        config = self.kv.default_config()
        with mock.patch.object(self.kv, "probe_backend_health", return_value=None), mock.patch.object(
            self.kv, "chat_completion", return_value=("太短", None)
        ):
            backend, text, error = self.kv.run_vision_backends(config, self.frames, prompt="p", min_chars=100)
        self.assertEqual("", backend)
        self.assertIn("视觉模型输出异常", error)
        self.assertIn("local", error)

    def test_backend_specs_glm_requires_api_key(self) -> None:
        config = self.kv.default_config()
        self.assertEqual(["local"], [spec.name for spec in self.kv.backend_specs(config)])
        config["glm"]["api_key"] = "k"
        self.assertEqual(["local", "glm"], [spec.name for spec in self.kv.backend_specs(config)])
        config["backend"] = "glm"
        self.assertEqual(["glm", "local"], [spec.name for spec in self.kv.backend_specs(config)])

    def test_chat_completions_url_three_base_forms(self) -> None:
        """必修 5（评审 M1）：默认 glm base（/paas/v4）拼出官方端点，不再多一层 /v1。"""
        join = self.kv.chat_completions_url
        # 形态一：版本段结尾（glm 官方默认 base）→ 只补 /chat/completions。
        self.assertEqual(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            join("https://open.bigmodel.cn/api/paas/v4"),
        )
        self.assertEqual("https://api.test/v1/chat/completions", join("https://api.test/v1/"))
        # 形态二：已给全端点 → 原样使用。
        self.assertEqual(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            join("https://open.bigmodel.cn/api/paas/v4/chat/completions"),
        )
        # 形态三：裸 base（llama-server 等标准 OpenAI 兼容服务）→ 追加 /v1/chat/completions。
        self.assertEqual("http://127.0.0.1:8304/v1/chat/completions", join("http://127.0.0.1:8304"))
        self.assertEqual("https://api.test/v1/chat/completions", join("https://api.test"))

    def test_chat_completion_glm_default_base_hits_official_endpoint(self) -> None:
        spec = self.kv.BackendSpec(
            name="glm",
            base_url=self.kv.DEFAULT_GLM_BASE_URL,
            model="glm-4.6v",
            api_key="k",
        )
        body = json.dumps({"choices": [{"message": {"content": LONG_VISION_OUTPUT}}]}).encode()
        with mock.patch.object(self.kv.urllib.request, "urlopen", return_value=FakeUrllibResponse(body)) as urlopen:
            text, error = self.kv.chat_completion(spec, [{"role": "user", "content": "hi"}])
        self.assertIsNone(error)
        request = urlopen.call_args[0][0]
        self.assertEqual("https://open.bigmodel.cn/api/paas/v4/chat/completions", request.full_url)
        self.assertEqual("Bearer k", request.headers.get("Authorization"))


class VisionConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_config_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_missing_file_uses_defaults(self) -> None:
        config = self.kv.load_vision_config(self.dir / "absent.json")
        self.assertEqual("local", config["backend"])
        self.assertEqual("http://127.0.0.1:8304", config["local"]["base_url"])
        self.assertEqual("Qwen3-VL-8B-GGUF", config["local"]["model"])
        self.assertEqual(12, config["frame_max"])
        self.assertEqual(1280, config["frame_long_edge"])
        self.assertFalse(self.kv.glm_configured(config))

    def test_valid_file_overrides_and_clamps(self) -> None:
        path = self.dir / "vision.json"
        path.write_text(
            json.dumps(
                {
                    "backend": "glm",
                    "local": {"base_url": "http://127.0.0.1:9000", "model": "other"},
                    "glm": {"api_key": "k", "model": "glm-4.6v"},
                    "frame_max": 99,
                    "frame_long_edge": 100,
                }
            ),
            encoding="utf-8",
        )
        config = self.kv.load_vision_config(path)
        self.assertEqual("glm", config["backend"])
        self.assertEqual("http://127.0.0.1:9000", config["local"]["base_url"])
        self.assertTrue(self.kv.glm_configured(config))
        self.assertEqual(50, config["frame_max"])
        self.assertEqual(256, config["frame_long_edge"])

    def test_corrupt_json_raises_chinese_error(self) -> None:
        path = self.dir / "vision.json"
        path.write_text("{bad json", encoding="utf-8")
        with self.assertRaises(self.kv.VisionConfigError) as ctx:
            self.kv.load_vision_config(path)
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_invalid_backend_raises(self) -> None:
        path = self.dir / "vision.json"
        path.write_text(json.dumps({"backend": "qwen"}), encoding="utf-8")
        with self.assertRaises(self.kv.VisionConfigError):
            self.kv.load_vision_config(path)


# ---------------------------------------------------------------------------
# 笔记处理：回写保真、零帧硬失败、降级链、OCR 门、媒体清理与锁
# ---------------------------------------------------------------------------


class NoteProcessingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_note_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.inbox = self.base / "inbox"
        self.records = self.base / "records"
        self.media = self.base / "media.mp4"
        self.media.write_bytes(b"fake-media")
        self.frames_dir = self.base / "frames"
        self.frames = [
            {"index": index, "path": write_fake_jpeg(self.frames_dir / f"frame-{index:04d}.jpg"), "time": float(index * 7)}
            for index in range(3)
        ]
        self.client = FakePlatformClient()
        self.addCleanup(self.client.cleanup)

    def patch_note_processing(
        self,
        *,
        extract=None,
        backends=None,
        ocr=None,
        media_error: str | None = None,
    ) -> dict:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        mocks: dict = {}
        stack.enter_context(mock.patch.object(self.kv.segmented, "dtk_client", self.client))
        stack.enter_context(mock.patch.object(self.kv.segmented, "xhs_client", self.client))
        if media_error is None:
            mocks["media"] = stack.enter_context(
                mock.patch.object(self.kv, "ensure_media", return_value=(self.media, "缓存媒体", None))
            )
        else:
            mocks["media"] = stack.enter_context(
                mock.patch.object(self.kv, "ensure_media", return_value=(None, "", media_error))
            )
        if extract is None:
            extract = lambda media, out_dir, **kwargs: (list(self.frames), None)  # noqa: E731
        mocks["extract"] = stack.enter_context(mock.patch.object(self.kv, "extract_frames", side_effect=extract))
        if backends is None:
            backends = lambda config, frames, **kwargs: ("local", LONG_VISION_OUTPUT, None)  # noqa: E731
        mocks["backends"] = stack.enter_context(mock.patch.object(self.kv, "run_vision_backends", side_effect=backends))
        if ocr is None:
            ocr = lambda frames: ("- [0.0s] 帧内文字", None)  # noqa: E731
        mocks["ocr"] = stack.enter_context(mock.patch.object(self.kv, "run_ocr_fallback", side_effect=ocr))
        stack.enter_context(mock.patch.object(self.kv, "RUN_RECORD_DIR", self.records))
        return mocks

    def test_success_writes_section_frontmatter_and_modality(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 界面教程.md")
        self.patch_note_processing()
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("完成(3帧)", result.outcome)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("画面解析: 完成(3帧)", raw)
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=不适用 | 转写=已完成 | 画面解析=完成(3帧)", raw)
        self.assertIn("- 模态覆盖：文案=完整 | 图片OCR=不适用 | 转写=已完成 | 画面解析=完成(3帧)", raw)
        self.assertIn("## 画面解析", raw)
        self.assertIn("- 帧数：3 帧（场景切换 + 每 30 秒兜底，长边 1280px，上限 12 帧、超出时均匀抽样）", raw)
        self.assertIn("- 帧来源：缓存媒体", raw)
        self.assertIn("- 后端：local（Qwen3-VL-8B-GGUF）", raw)
        self.assertIn("### 逐帧观察与推断", raw)
        self.assertIn("[1.2s] 终端窗口显示命令", raw)
        self.assertIn("推断：", raw)
        # 评审 M2：作品类型来源缺失（老笔记）时不再回填「启发式」——来源未知保持未知。
        self.assertNotIn("作品类型来源:", raw)
        # 门禁打通：画面解析完成 + 转写已完成 → 可推送。
        self.assertIn("内容质量: 可推送", raw)
        # 既有内容逐字保留。
        self.assertIn(DESCRIPTION, raw)
        self.assertIn("## 标签和分类", raw)
        self.assertIn("## 原始链接", raw)
        # 媒体清理（决策 1）。
        self.assertEqual(["7704156396664769811"], self.client.cleaned)

    def test_zero_frames_hard_fails_without_model_call(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 空视频.md")
        self.patch_note_processing(extract=lambda media, out_dir, **kwargs: ([], "未能抽取到任何帧：ffmpeg 返回码 1"))
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("失败", result.outcome)
        self.assertIn("未能抽取到任何帧", result.remark)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("画面解析: 失败", raw)
        self.assertIn("画面解析=失败", raw)
        self.assertIn("内容质量: 需继续解析", raw)
        self.assertNotIn("## 画面解析", raw)
        self.assertEqual([], self.client.cleaned)

    def test_backend_failure_marks_failed_and_keeps_media_for_retry(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 服务挂了.md")
        self.patch_note_processing(backends=lambda config, frames, **kwargs: ("", "", "local：视觉服务不可用；glm：未配置"))
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("失败", result.outcome)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("画面解析: 失败", raw)
        self.assertIn("视觉服务不可用", raw)
        self.assertEqual([], self.client.cleaned)

    def test_ocr_fallback_gated_by_flag(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 降级A.md")
        mocks = self.patch_note_processing(backends=lambda config, frames, **kwargs: ("", "", "local：视觉服务不可用"))
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("失败", result.outcome)  # 默认直接标失败，保留重跑机会。
        self.assertEqual(0, mocks["ocr"].call_count)

        path2 = write_note(self.inbox, "2026-11-18 抖音 - 降级B.md")
        result2 = self.kv.process_note(path2, config=self.kv.default_config(), allow_ocr_fallback=True)
        self.assertEqual("仅OCR降级", result2.outcome)
        self.assertEqual(1, mocks["ocr"].call_count)
        raw = path2.read_text(encoding="utf-8")
        self.assertIn("画面解析: 仅OCR降级", raw)
        self.assertIn("画面解析=仅OCR降级", raw)
        self.assertIn("### 逐帧OCR（仅观察）", raw)
        self.assertIn("后端：PaddleOCR-VL（仅OCR降级：只有帧内文字观察，无推断与流程总结）", raw)
        self.assertIn("内容质量: 需核验", raw)
        self.assertIn("7704156396664769811", self.client.cleaned)

    def test_hallucination_warning_recorded_in_remark(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 脑补.md")
        suspicious = " ".join(f"[{i}.0s] 第{i}步操作" for i in range(20))
        self.patch_note_processing(backends=lambda config, frames, **kwargs: ("local", suspicious, ""))
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("完成(3帧)", result.outcome)
        self.assertIn("疑似脑补", result.remark)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("疑似脑补", raw)
        self.assertIn("画面解析备注:", raw)

    def test_media_refetch_failure_marks_failed(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 媒体丢了.md")
        self.patch_note_processing(media_error="笔记没有可用的作品链接用于重取媒体。")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("失败", result.outcome)
        self.assertIn("媒体重取失败", result.remark)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("画面解析: 失败", raw)

    def test_concurrent_lock_skips_note(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 锁占用.md")
        lock_dir = self.client.media_cache_dir("7704156396664769811")
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / ".transcribe-lock").write_text("other 2026-11-18 00:00:00\n", encoding="utf-8")
        mocks = self.patch_note_processing()
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result.outcome)
        self.assertIn("另一进程", result.remark)
        self.assertEqual(0, mocks["backends"].call_count)
        # 未写回任何状态。
        self.assertNotIn("画面解析:", path.read_text(encoding="utf-8"))

    def test_done_note_is_idempotent(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 已完成.md", vision_status="完成(9帧)", modality_vision="完成(9帧)")
        mocks = self.patch_note_processing()
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result.outcome)
        self.assertEqual(0, mocks["backends"].call_count)

    def test_unsupported_platform_skips(self) -> None:
        path = write_note(self.inbox, "2026-11-18 YouTube - 界面教程.md", platform="YouTube")
        self.patch_note_processing()
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result.outcome)

    def test_estimated_timestamps_flagged_in_vision_remark(self) -> None:
        """L2：showinfo 不足回退假时间戳时，画面解析备注必须标注「时间戳为估算」。"""
        path = write_note(self.inbox, "2026-11-18 抖音 - 时间戳估算.md")
        estimated_frames = [
            {"index": 0, "path": write_fake_jpeg(self.base / "est-0.jpg"), "time": 1.0, "time_estimated": True},
            {"index": 1, "path": write_fake_jpeg(self.base / "est-1.jpg"), "time": 31.0, "time_estimated": True},
        ]
        self.patch_note_processing(extract=lambda media, out_dir, **kwargs: (estimated_frames, None))
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("完成(2帧)", result.outcome)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("时间戳为估算", raw)
        self.assertIn("画面解析备注: 时间戳为估算", raw)

    def test_ocr_fallback_writes_placeholder_for_textless_frames(self) -> None:
        """L9：OCR 降级时无文字帧输出占位行，逐帧时间轴完整。"""
        text_frame = self.frames[0]
        stub = types.SimpleNamespace(
            run_paddleocr_vl_ocr=lambda paths: ([{"path": str(text_frame["path"]), "text": "帧内文字"}], None)
        )
        with mock.patch.object(self.kv, "_capture_link", return_value=stub):
            ocr_text, error = self.kv.run_ocr_fallback(self.frames)
        self.assertIsNone(error)
        self.assertIn("- [0s] 帧内文字", ocr_text)
        self.assertIn("- [7s] （无文字）", ocr_text)
        self.assertIn("- [14s] （无文字）", ocr_text)
        self.assertEqual(len(self.frames), len(ocr_text.strip().splitlines()))

    def test_ocr_fallback_still_fails_when_no_frame_has_text(self) -> None:
        stub = types.SimpleNamespace(run_paddleocr_vl_ocr=lambda paths: ([{"path": str(self.frames[0]["path"]), "text": ""}], None))
        with mock.patch.object(self.kv, "_capture_link", return_value=stub):
            ocr_text, error = self.kv.run_ocr_fallback(self.frames)
        self.assertEqual("", ocr_text)
        self.assertIn("未从任何帧提取到文字", error)

    def test_replace_vision_section_stops_at_known_heading_not_inner_hash_lines(self) -> None:
        """L6：旧「画面解析」内容里夹带 "## " 行时，替换边界只认已知小节标题，不留残留。"""
        path = write_note(self.inbox, "2026-11-18 抖音 - 旧内容带二级标题.md", vision_status="完成(1帧)", modality_vision="完成(1帧)")
        raw = path.read_text(encoding="utf-8")
        raw = raw.replace(
            "## 标签和分类",
            "## 画面解析\n\n旧观察内容。\n\n## 模型输出的二级标题\n\n旧残留内容。\n\n## 标签和分类",
            1,
        )
        path.write_text(raw, encoding="utf-8")
        note = self.kv.VisionNote(path)
        note.replace_vision_section(["## 画面解析", "", "- 新一轮画面解析内容"])
        note.save()
        updated = path.read_text(encoding="utf-8")
        self.assertIn("- 新一轮画面解析内容", updated)
        self.assertNotIn("旧观察内容", updated)
        self.assertNotIn("旧残留内容", updated)
        self.assertNotIn("模型输出的二级标题", updated)
        self.assertIn("## 标签和分类", updated)
        self.assertIn("## 原始链接", updated)


# ---------------------------------------------------------------------------
# UI 演示视觉确认（决策 10）：人工 > 视觉确认 > 启发式
# ---------------------------------------------------------------------------


class VisionConfirmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_confirm_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.inbox = self.base / "inbox"
        self.records = self.base / "records"
        self.media = self.base / "media.mp4"
        self.media.write_bytes(b"fake-media")
        self.frames_dir = self.base / "frames"
        self.confirm_frames = [
            {"index": index, "path": write_fake_jpeg(self.frames_dir / f"c{index}.jpg"), "time": float(index * 15)}
            for index in range(2)
        ]
        self.full_frames = [
            {"index": index, "path": write_fake_jpeg(self.frames_dir / f"f{index}.jpg"), "time": float(index * 9)}
            for index in range(4)
        ]
        self.client = FakePlatformClient()
        self.addCleanup(self.client.cleanup)

    def patch_confirm(self, *, answer: str, calls: list | None = None) -> dict:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(self.kv.segmented, "dtk_client", self.client))
        stack.enter_context(mock.patch.object(self.kv.segmented, "xhs_client", self.client))
        stack.enter_context(mock.patch.object(self.kv, "ensure_media", return_value=(self.media, "媒体重取", None)))

        def fake_extract(media, out_dir, *, long_edge, frame_limit):
            frames = self.confirm_frames if frame_limit == self.kv.CONFIRM_FRAME_COUNT else self.full_frames
            if calls is not None:
                calls.append(("extract", frame_limit))
            return list(frames), None

        def fake_backends(config, frames, *, prompt, min_chars, health_check=True):
            if calls is not None:
                calls.append(("backend", min_chars))
            if "是否软件界面" in prompt:
                return "local", answer, ""
            return "local", LONG_VISION_OUTPUT, ""

        stack.enter_context(mock.patch.object(self.kv, "extract_frames", side_effect=fake_extract))
        stack.enter_context(mock.patch.object(self.kv, "run_vision_backends", side_effect=fake_backends))
        stack.enter_context(mock.patch.object(self.kv, "RUN_RECORD_DIR", self.records))
        return {}

    def test_confirm_yes_upgrades_type_then_runs_full_analysis(self) -> None:
        # 口播视频但标题含关键词且来源=启发式（新捕获笔记由 capture_link 写入该来源）。
        path = write_note(self.inbox, "2026-11-18 抖音 - 教程.md", work_type="口播视频", type_source="启发式")
        calls: list = []
        self.patch_confirm(answer="是", calls=calls)
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("完成(4帧)", result.outcome)
        self.assertIn("视觉确认判定为 UI 演示录屏", result.remark)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("作品类型: UI演示视频", raw)
        self.assertIn("作品类型来源: 视觉确认", raw)
        self.assertIn("- 作品类型：UI演示视频", raw)
        self.assertIn("视觉确认: 是（", raw)
        self.assertIn("## 画面解析", raw)
        # 先 2 帧确认，再按完整上限抽帧。
        self.assertEqual([("extract", 2), ("backend", 1), ("extract", 12), ("backend", 100)], calls)
        self.assertEqual(["7704156396664769811"], self.client.cleaned)

    def test_confirm_no_keeps_type_and_marks_not_applicable(self) -> None:
        # 转写未完成（无转写状态字段）：确认「否」收尾保留媒体缓存（评审 M4）。
        path = write_note(self.inbox, "2026-11-18 抖音 - 教程.md", work_type="口播视频", type_source="启发式")
        self.patch_confirm(answer="否")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("视觉确认-否", result.outcome)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("作品类型: 口播视频", raw)
        self.assertIn("视觉确认: 否（", raw)
        self.assertIn("画面解析: 不适用", raw)
        self.assertIn("画面解析=不适用", raw)
        self.assertIn("媒体缓存保留至转写完成", raw)
        self.assertNotIn("## 画面解析", raw)
        # 评审 M4：转写状态≠已完成 → 不清理媒体缓存，留给分段转写复用。
        self.assertEqual([], self.client.cleaned)

    def test_confirm_no_cleans_cache_when_transcript_done(self) -> None:
        # 转写状态=已完成：确认「否」收尾才清理媒体缓存（评审 M4 的另一分支）。
        path = write_note(
            self.inbox, "2026-11-18 抖音 - 教程已转完.md", work_type="口播视频", type_source="启发式", transcript_status="已完成"
        )
        self.patch_confirm(answer="否")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("视觉确认-否", result.outcome)
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("媒体缓存保留至转写完成", raw)
        self.assertEqual(["7704156396664769811"], self.client.cleaned)

    def test_confirm_uncertain_keeps_cache_until_transcript_done(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 教程.md", work_type="口播视频", type_source="启发式")
        self.patch_confirm(answer="不确定")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("视觉确认-不确定", result.outcome)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("视觉确认: 不确定（", raw)
        self.assertIn("建议人工判定", raw)
        self.assertIn("媒体缓存保留至转写完成", raw)
        self.assertEqual([], self.client.cleaned)
        # 再次处理：视觉确认字段已存在，不再重复问，也不进入完整解析。
        result2 = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result2.outcome)

    def test_missing_type_source_blocks_vision_confirm_with_remark(self) -> None:
        """评审 M2（必修 4）：无「作品类型来源」字段 + 关键词命中 → 不改写，备注说明补救方式。"""
        path = write_note(self.inbox, "2026-11-18 抖音 - 老笔记教程.md", work_type="口播视频")  # 无 type_source
        self.patch_confirm(answer="是")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result.outcome)
        self.assertIn("类型来源未知，未做视觉确认改写", result.remark)
        self.assertIn("如需视觉确认请将 作品类型来源 设为 启发式", result.remark)
        # 笔记不被改写：类型不变、不落视觉确认/画面解析字段。
        raw = path.read_text(encoding="utf-8")
        self.assertIn("作品类型: 口播视频", raw)
        self.assertNotIn("视觉确认:", raw)
        self.assertNotIn("画面解析:", raw)
        self.assertEqual([], self.client.cleaned)

    def test_vision_confirm_allowed_requires_known_source(self) -> None:
        allowed, reason = self.kv.vision_confirm_allowed(
            type("Note", (), {"work_type_source": lambda self: ""})()
        )
        self.assertFalse(allowed)
        self.assertIn("类型来源未知", reason)

    def test_manual_type_source_blocks_vision_confirm(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 教程.md", work_type="口播视频", type_source="人工")
        self.patch_confirm(answer="是")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result.outcome)

    def test_vision_type_source_not_reconfirmed(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 教程.md", work_type="口播视频", type_source="视觉确认")
        self.patch_confirm(answer="是")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result.outcome)

    def test_unknown_type_with_keyword_is_confirm_candidate(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 未知教程.md", work_type="未知", type_source="启发式")
        calls: list = []
        self.patch_confirm(answer="否", calls=calls)
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("视觉确认-否", result.outcome)
        self.assertEqual([("extract", 2), ("backend", 1)], calls)

    def test_spoken_video_without_keyword_is_not_candidate(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 日常分享.md", work_type="口播视频", type_source="启发式", title="今天聊点生活")
        self.patch_confirm(answer="是")
        result = self.kv.process_note(path, config=self.kv.default_config())
        self.assertEqual("跳过", result.outcome)

    def test_skip_confirm_flag_ignores_confirm_candidates(self) -> None:
        path = write_note(self.inbox, "2026-11-18 抖音 - 教程.md", work_type="口播视频", type_source="启发式")
        self.patch_confirm(answer="是")
        result = self.kv.process_note(path, config=self.kv.default_config(), skip_confirm=True)
        self.assertEqual("跳过", result.outcome)


# ---------------------------------------------------------------------------
# 扫描与 CLI
# ---------------------------------------------------------------------------


class ScanAndCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_cli_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.inbox = self.base / "inbox"
        self.records = self.base / "records"
        self.client = FakePlatformClient()
        self.addCleanup(self.client.cleanup)

    def patch_env(self) -> contextlib.ExitStack:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(self.kv.segmented, "dtk_client", self.client))
        stack.enter_context(mock.patch.object(self.kv.segmented, "xhs_client", self.client))
        stack.enter_context(mock.patch.object(self.kv, "RUN_RECORD_DIR", self.records))
        stack.enter_context(mock.patch.object(self.kv, "DEFAULT_INBOX", self.inbox))
        # 隔离本机真实 ~/.config/my-mind/vision.json，保证 CLI 测试可复现。
        stack.enter_context(mock.patch.object(self.kv, "VISION_CONFIG_PATH", self.base / "no-vision.json"))
        return stack

    def test_scan_inbox_selects_ui_demo_and_confirm_candidates(self) -> None:
        write_note(self.inbox, "01 抖音 - 待解析.md")  # UI演示 + 未开始
        write_note(self.inbox, "02 抖音 - 已完成.md", vision_status="完成(9帧)", modality_vision="完成(9帧)")
        write_note(self.inbox, "03 抖音 - 失败重跑.md", vision_status="失败", modality_vision="失败")
        write_note(self.inbox, "04 抖音 - 图文.md", work_type="图文")
        write_note(self.inbox, "05 抖音 - 口播带关键词.md", work_type="口播视频", type_source="启发式", title="装机教程")
        write_note(self.inbox, "06 抖音 - 口播无关键词.md", work_type="口播视频", type_source="启发式", title="随便聊聊")
        # 评审 M2：无「作品类型来源」的老笔记即使关键词命中也不进视觉确认候选。
        write_note(self.inbox, "07 抖音 - 老笔记关键词.md", work_type="口播视频", title="装机教程")
        selected = self.patch_env_and_scan()
        self.assertEqual(
            ["01 抖音 - 待解析.md", "03 抖音 - 失败重跑.md", "05 抖音 - 口播带关键词.md"],
            [path.name for path in selected],
        )

    def patch_env_and_scan(self):
        self.patch_env()
        return self.kv.scan_inbox(self.inbox, 10)

    def test_scan_inbox_respects_max_notes(self) -> None:
        for index in range(4):
            write_note(self.inbox, f"0{index} 抖音 - 待解析.md")
        self.patch_env()
        selected = self.kv.scan_inbox(self.inbox, 2)
        self.assertEqual(2, len(selected))

    def test_cli_note_not_found_returns_one(self) -> None:
        stack = self.patch_env()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = self.kv.main(["--note", str(self.base / "absent.md")])
        self.assertEqual(1, code)

    def test_cli_glm_without_config_is_hard_error(self) -> None:
        self.patch_env()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = self.kv.main(["--backend", "glm"])
        self.assertEqual(1, code)
        self.assertIn("glm", stderr.getvalue())

    def test_cli_dry_run_writes_nothing(self) -> None:
        path = write_note(self.inbox, "01 抖音 - 待解析.md")
        before = path.read_text(encoding="utf-8")
        self.patch_env()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.kv.main(["--dry-run"])
        self.assertEqual(0, code)
        self.assertEqual(before, path.read_text(encoding="utf-8"))
        self.assertFalse(self.records.exists())
        self.assertIn("dry-run", stdout.getvalue())
        self.assertIn("01 抖音 - 待解析.md", stdout.getvalue())

    def test_cli_processes_note_and_writes_run_record(self) -> None:
        path = write_note(self.inbox, "01 抖音 - 待解析.md")
        stack = self.patch_env()
        frames = [{"index": 0, "path": write_fake_jpeg(self.base / "f.jpg"), "time": 1.0}]
        with mock.patch.object(self.kv, "ensure_media", return_value=(self.base / "m.mp4", "缓存媒体", None)), mock.patch.object(
            self.kv, "extract_frames", return_value=(frames, None)
        ), mock.patch.object(self.kv, "run_vision_backends", return_value=("local", LONG_VISION_OUTPUT, "")):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = self.kv.main(["--note", str(path)])
        self.assertEqual(0, code)
        self.assertIn("画面解析: 完成(1帧)", path.read_text(encoding="utf-8"))
        records = list(self.records.glob("画面解析-*.md"))
        self.assertEqual(1, len(records))
        record_text = records[0].read_text(encoding="utf-8")
        self.assertIn("记录类型: 画面解析", record_text)
        self.assertIn("后端: local", record_text)
        self.assertIn("01 抖音 - 待解析.md", record_text)
        self.assertIn("运行完成", stdout.getvalue())

    def test_cli_backend_flag_overrides_config(self) -> None:
        path = write_note(self.inbox, "01 抖音 - 待解析.md")
        configfile = self.base / "vision.json"
        configfile.write_text(json.dumps({"backend": "glm", "glm": {"api_key": "k"}}), encoding="utf-8")
        self.patch_env()
        frames = [{"index": 0, "path": write_fake_jpeg(self.base / "f.jpg"), "time": 1.0}]
        captured: dict = {}
        with mock.patch.object(self.kv, "VISION_CONFIG_PATH", configfile), mock.patch.object(
            self.kv, "ensure_media", return_value=(self.base / "m.mp4", "缓存媒体", None)
        ), mock.patch.object(self.kv, "extract_frames", return_value=(frames, None)), mock.patch.object(
            self.kv,
            "run_vision_backends",
            side_effect=lambda config, fr, **kw: captured.update(primary=config["backend"]) or ("local", LONG_VISION_OUTPUT, ""),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.kv.main(["--note", str(path), "--backend", "local"])
        self.assertEqual(0, code)
        self.assertEqual("local", captured["primary"])


# ---------------------------------------------------------------------------
# 门禁与触发链集成：assess_content_quality / capture_link 提示 / segmented 收尾提示
# ---------------------------------------------------------------------------


class GateIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capture = load_module(CAPTURE_SCRIPT, "test_keyframe_gate_capture_module")
        cls.seg = load_module(SEG_SCRIPT, "test_keyframe_gate_segmented_module")
        cls.kv = load_module(KEYFRAME_SCRIPT, "test_keyframe_gate_kv_module")

    @classmethod
    def assess(cls, vision_status: str) -> tuple[str, str]:
        return cls.capture.assess_content_quality(
            platform="抖音",
            material_type="社媒链接",
            parse_status="已解析",
            description="足够长的抖音 UI 演示视频分享文案，" * 10,
            platform_meta={},
            content_extract={"source": "公开视频音频转写", "model": "small", "word_count": 500},
            content_extract_error="",
            image_ocr=None,
            image_ocr_error="",
            youtube_subtitle=None,
            external_transcript=None,
            work_type="UI演示视频",
            vision_status=vision_status,
        )

    def test_vision_done_is_pushable(self) -> None:
        quality, reason = self.assess("完成(12帧)")
        self.assertEqual("可推送", quality)
        self.assertIn("画面解析均已完成", reason)

    def test_vision_ocr_fallback_needs_review(self) -> None:
        quality, reason = self.assess("仅OCR降级")
        self.assertEqual("需核验", quality)
        self.assertIn("人工核验", reason)
        quality, reason = self.assess("部分帧失败但有观察")
        self.assertEqual("需核验", quality)

    def test_vision_failed_or_pending_needs_more_parsing(self) -> None:
        quality, reason = self.assess("失败")
        self.assertEqual("需继续解析", quality)
        self.assertIn("画面解析失败", reason)
        quality, reason = self.assess("未开始")
        self.assertEqual("需继续解析", quality)
        self.assertIn("画面解析尚未执行", reason)
        quality, reason = self.assess("")
        self.assertEqual("需继续解析", quality)

    def test_vision_not_applicable_does_not_say_waiting_for_vision(self) -> None:
        """L11：画面解析=不适用（非 UI 演示）时不落「等待画面解析」话术。"""
        quality, reason = self.assess("不适用")
        self.assertEqual("需核验", quality)
        self.assertIn("不适用", reason)
        self.assertIn("人工核验作品类型", reason)
        self.assertNotIn("等待画面解析", reason)

    def test_capture_ui_demo_hint_after_one_shot_transcribe(self) -> None:
        info = {
            "media_url": "https://cdn.douyin.test/short.mp4",
            "duration": "120",
            "work_type": "UI演示视频",
            "description": DESCRIPTION,
        }
        audio_path = self.temp_audio()
        with mock.patch.object(self.capture, "extract_audio", return_value=(audio_path, None)), mock.patch.object(
            self.capture,
            "transcribe_audio",
            return_value=("短视频转写文本。", [{"time": "00:01", "text": "短视频转写文本。"}], {"backend": "faster-whisper", "model": "small", "language": "zh"}, None),
        ):
            self.capture.attach_content_extract(
                info, backend="auto", model="small", language="zh", command_template="", max_seconds=360
            )
        self.assertTrue(info["content_extract"])
        self.assertIn("画面解析待执行：python3 .codex/skills/inbox-capture/scripts/keyframe_vision.py", info["parse_notice"])
        # 口播视频不加提示。
        spoken = {"media_url": "https://cdn.douyin.test/short.mp4", "duration": "120", "work_type": "口播视频"}
        with mock.patch.object(self.capture, "extract_audio", return_value=(audio_path, None)), mock.patch.object(
            self.capture,
            "transcribe_audio",
            return_value=("短视频转写文本。", [{"time": "00:01", "text": "短视频转写文本。"}], {"backend": "faster-whisper", "model": "small", "language": "zh"}, None),
        ):
            self.capture.attach_content_extract(
                spoken, backend="auto", model="small", language="zh", command_template="", max_seconds=360
            )
        self.assertNotIn("parse_notice", spoken)

    def temp_audio(self) -> Path:
        if not hasattr(self, "_audio_dir"):
            holder = tempfile.TemporaryDirectory()
            self.addCleanup(holder.cleanup)
            self._audio_dir = Path(holder.name)
        path = self._audio_dir / "media-audio.mp3"
        path.write_bytes(b"fake-audio")
        return path

    def test_segmented_finalize_passes_vision_status_and_hint(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        inbox = Path(temp.name) / "inbox"
        state = {
            "v": 1,
            "media_duration": 500.0,
            "speech_total": 470.0,
            "zero_progress_runs": 0,
            "language": "zh",
            "planned_at": "",
            "segments": [
                {
                    "s": 0,
                    "e": 470,
                    "sp": 470.0,
                    "done": True,
                    "text": "整段 UI 演示的口述内容。",
                    "segs": [[0, 470, "整段 UI 演示的口述内容。"]],
                }
            ],
            "skipped": [],
        }
        stub = types.SimpleNamespace(
            fmt_duration=str,
            summarize_transcript_text=lambda text: {"word_count": 600, "summary": "摘要", "key_points": ["要点"], "excerpt": text[:50]},
            build_transcript_timeline=lambda segments: [],
            assess_content_quality=self.capture.assess_content_quality,
            KEYFRAME_VISION_COMMAND=self.capture.KEYFRAME_VISION_COMMAND,
        )
        with mock.patch.object(self.seg, "_capture_link", return_value=stub), mock.patch.object(
            self.seg, "cleanup_media_cache", lambda *args, **kwargs: None
        ):
            # 未开始：保持需继续解析 + 提示画面解析待执行。
            path = write_note(inbox, "01 抖音 - 分段UI.md")
            result = self.seg.NoteRunResult(path=path)
            note = self.seg.InboxNote(path)
            self.seg.finalize_note(note, state, "抖音", "7704156396664769811", result)
            note.save()
            raw = path.read_text(encoding="utf-8")
            self.assertIn("画面解析待执行：python3 .codex/skills/inbox-capture/scripts/keyframe_vision.py", raw)
            self.assertIn("内容质量: 需继续解析", raw)

            # 画面解析已完成（keyframe_vision 回写过）：收尾重评升为可推送。
            path2 = write_note(inbox, "02 抖音 - 分段UI完成.md", vision_status="完成(12帧)", modality_vision="完成(12帧)")
            result2 = self.seg.NoteRunResult(path=path2)
            note2 = self.seg.InboxNote(path2)
            self.seg.finalize_note(note2, state, "抖音", "7704156396664769811", result2)
            note2.save()
            self.assertIn("内容质量: 可推送", path2.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
