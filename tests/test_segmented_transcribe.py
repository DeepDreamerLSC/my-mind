from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".codex/skills/inbox-capture/scripts"
SEG_SCRIPT = SCRIPTS / "segmented_transcribe.py"
CAPTURE_SCRIPT = SCRIPTS / "capture_link.py"

MEDIA_URL = "https://cdn.douyin.test/video/7704156396664769811.mp4"
XHS_MEDIA_URL = "https://sns-video.xhscdn.test/note/65a1b2c3.mp4"
DESCRIPTION = "这是抖音长视频样本的分享文案，用来满足内容质量门禁对正文的最低要求，包含足够长的中文内容。" * 3
LONG_TEXT = "这条长视频讲解了分段转写状态机的实现思路与预算续跑策略，" * 12  # 300+ 汉字，满足可推送门禁


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_clock(start: float = 0.0, step: float = 1.0):
    """单调时钟桩：每次调用推进 step 秒，用于模拟预算耗尽。"""
    state = {"now": start}

    def tick() -> float:
        now = state["now"]
        state["now"] += step
        return now

    return tick


def block(s: int, e: int, *, sp: float | None = None, done: bool = False, text: str = "", segs: list | None = None) -> dict:
    return {
        "s": s,
        "e": e,
        "sp": float(sp if sp is not None else e - s),
        "done": done,
        "text": text,
        "segs": segs or [],
    }


def write_real_wav(path: Path, *, seconds: float = 2.0, rate: int = 16000) -> None:
    """写一个真实的 16k 单声道 16bit wav（44 字节规范头 + 数据），供缓存校验测试使用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x01" * int(seconds * rate))


def truncate_wav(path: Path) -> None:
    """保留合法 wav 头、砍掉一半数据：构造「合法头 + 截断数据」的半截缓存文件。"""
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def render_comment(state: dict) -> str:
    return f"<!-- my-mind:transcript-segments {json.dumps(state, ensure_ascii=False)} -->"


def write_note(
    directory: Path,
    name: str,
    *,
    platform: str = "抖音",
    content_id: str = "7704156396664769811",
    work_type: str = "口播视频",
    status: str = "未开始",
    coverage: str = "0/0秒",
    remark: str = "长视频分段转写已排队，等待后台总控续跑。",
    comment: str = "",
    extra_body: str = "",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if platform == "抖音":
        page_url = f"https://www.douyin.com/video/{content_id}"
        original_url = f"https://v.douyin.com/share/{content_id}/"
        media_url = MEDIA_URL
    else:
        page_url = f"https://www.xiaohongshu.com/explore/{content_id}"
        original_url = f"https://www.xiaohongshu.com/discovery/item/{content_id}"
        media_url = XHS_MEDIA_URL
    modality = f"文案=完整 | 图片OCR=不适用 | 转写={'未开始(分段)' if status == '未开始' else status} | 画面解析=未开始"
    if status == "进行中":
        modality = "文案=完整 | 图片OCR=不适用 | 转写=40% | 画面解析=未开始"
    if status == "失败":
        modality = "文案=完整 | 图片OCR=不适用 | 转写=失败 | 画面解析=未开始"
    gate = "视频时长 30:00 超过一段式转写上限 06:00，长视频已进入分段转写队列，等待后台总控续跑。 不应直接进入前台阅读推送。"
    lines = [
        "---",
        "类别: 收件箱",
        "资料类型: 社媒链接",
        f"作品类型: {work_type}",
        f"模态覆盖: {modality}",
        f"来源平台: {platform}",
        "标题: 长视频样本",
        "作者或频道: 某作者",
        f"来源链接: {page_url}",
        f"原始链接: {original_url}",
        f"内容编号: {content_id}",
        "时长: 30:00",
        f"转写状态: {status}",
        f"转写覆盖: {coverage}",
        f"转写备注: {remark}",
        "解析状态: 已解析",
        "内容质量: 需继续解析",
        f"质量门禁: {gate}",
        "内容摘录来源:",
        "内容摘录后端:",
        "内容摘录字数:",
        "阅读状态: 已读",
        "---",
        "",
        "# 长视频样本",
        "",
        "## 基础信息",
        "",
        f"- 来源平台：{platform}",
        "- 内容质量：需继续解析",
        f"- 质量门禁：{gate}",
        f"- 作品类型：{work_type}",
        f"- 模态覆盖：{modality}",
        f"- 转写状态：{status}",
        f"- 转写覆盖：{coverage}",
        "",
        "## 解析说明",
        "",
        "长视频已进入分段转写队列，等待后台总控续跑。",
        "",
        "## 文案摘录",
        "",
        DESCRIPTION,
        "",
        "## 视频内容摘录失败",
        "",
        "视频时长 30:00 超过一段式转写上限 06:00，长视频已进入分段转写队列，等待后台总控续跑。",
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
    if extra_body:
        lines.extend(["", extra_body])
    if comment:
        lines.extend(["", comment])
    path = directory / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class FakePlatformClient:
    """替换 segmented_transcribe.dtk_client / xhs_client 的假客户端：不发真实网络请求。"""

    def __init__(self, *, parse_error: Exception | None = None, fail_download: bool = False):
        self.temp = tempfile.TemporaryDirectory(prefix="my-mind-seg-cache-")
        self.root = Path(self.temp.name)
        self.parse_error = parse_error
        self.fail_download = fail_download
        self.parsed: list[str] = []
        self.downloaded: list[tuple[str, int, str]] = []
        self.cleaned: list[str] = []
        self.probes: list[Path] = []

    def cleanup(self) -> None:
        self.temp.cleanup()

    def media_cache_dir(self, content_id: str) -> Path:
        return self.root / content_id

    def parse(self, url: str) -> dict:
        self.parsed.append(url)
        if self.parse_error:
            raise self.parse_error
        return {"kind": "video", "video": {"url": MEDIA_URL}}

    def fetch_detail(self, url: str) -> dict:
        self.parsed.append(url)
        if self.parse_error:
            raise self.parse_error
        return {"下载地址": [XHS_MEDIA_URL]}

    def download_media(self, url: str, dest_dir: Path, max_bytes: int = 0, *, filename: str = "", **_kwargs) -> Path:
        if self.fail_download:
            raise RuntimeError("媒体下载失败：HTTP 403 没有访问权限")
        self.downloaded.append((url, int(max_bytes), filename or ""))
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / (filename or "media.bin")
        path.write_bytes(b"fake-media-bytes")
        return path

    def ffprobe_check(self, path: Path) -> dict:
        self.probes.append(path)
        return {"format_name": "mp4", "duration_seconds": 1820.0, "size_bytes": 1024}

    def cleanup_media(self, content_id: str) -> str:
        self.cleaned.append(content_id)
        return "已删除媒体缓存目录（测试）"


class PlanBlocksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seg = load_module(SEG_SCRIPT, "test_segmented_plan_module")

    def test_block_boundaries_land_on_pauses(self) -> None:
        blocks = self.seg.plan_blocks([(0, 156), (160, 300), (305, 470)])
        self.assertEqual([(0, 156), (160, 300), (305, 470)], [(b["s"], b["e"]) for b in blocks])
        # 每个块边界都落在语音区间端点（即停顿处），不切在语音中间。
        for current, following in zip(blocks, blocks[1:]):
            self.assertLessEqual(current["e"], following["s"])
            self.assertGreater(following["s"], current["e"])

    def test_blocks_respect_60_180_speech_budget(self) -> None:
        intervals = [(index * 25.0, index * 25.0 + 20) for index in range(12)]  # 12 段 20 秒语音
        blocks = self.seg.plan_blocks(intervals)
        self.assertEqual([180.0, 60.0], [b["sp"] for b in blocks])
        for block_item in blocks:
            self.assertLessEqual(block_item["sp"], 180.0 + 1e-6)
            self.assertGreaterEqual(block_item["sp"], 60.0 - 1e-6)

    def test_packing_invariants_hold_for_mixed_intervals(self) -> None:
        cases = [
            [(0, 10), (20, 195), (200, 260)],  # 前小后大：产生一个短块的装箱边界
            [(0, 90), (95, 180), (185, 195)],
            [(0, 175), (180, 195)],
            [(index * 40.0, index * 40.0 + 35) for index in range(9)],
        ]
        for intervals in cases:
            with self.subTest(intervals=intervals):
                blocks = self.seg.plan_blocks(intervals)
                self.assertTrue(blocks)
                for block_item in blocks:
                    self.assertLessEqual(block_item["sp"], 180.0 + 1e-6)
                for left, right in zip(blocks, blocks[1:]):
                    # 规范装箱：相邻块合并必然超上限，否则应当已合并。
                    self.assertGreater(left["sp"] + right["sp"], 180.0 + 1e-6)
                for index, block_item in enumerate(blocks):
                    if block_item["sp"] >= 60.0:
                        continue
                    # 不足 60 秒的块只允许出现在无法与任何相邻块合并时。
                    merge_left = blocks[index - 1]["sp"] + block_item["sp"] if index > 0 else 1e9
                    merge_right = block_item["sp"] + blocks[index + 1]["sp"] if index + 1 < len(blocks) else 1e9
                    self.assertGreater(min(merge_left, merge_right), 180.0 + 1e-6)

    def test_overlong_continuous_speech_is_split_within_limit(self) -> None:
        blocks = self.seg.plan_blocks([(0, 400)])
        self.assertEqual([180.0, 180.0, 40.0], [b["sp"] for b in blocks])
        self.assertEqual([(0, 180), (180, 360), (360, 400)], [(b["s"], b["e"]) for b in blocks])

    def test_skipped_intervals_are_non_speech_gaps(self) -> None:
        blocks = self.seg.plan_blocks([(0, 120), (130, 250), (260, 380)])
        skipped = self.seg.skipped_intervals(blocks, 400)
        self.assertEqual([{"s": 120.0, "e": 130.0}, {"s": 250.0, "e": 260.0}, {"s": 380.0, "e": 400.0}], skipped)

    def test_no_speech_yields_no_blocks(self) -> None:
        self.assertEqual([], self.seg.plan_blocks([]))


class NoteStateIOTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seg = load_module(SEG_SCRIPT, "test_segmented_note_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_comment_roundtrip_with_arrow_and_quotes(self) -> None:
        path = write_note(self.dir, "note.md")
        note = self.seg.InboxNote(path)
        state = {
            "v": 1,
            "media_duration": 100.0,
            "speech_total": 90.0,
            "zero_progress_runs": 1,
            "language": "zh",
            "planned_at": "",
            "segments": [block(0, 90, done=True, text='包含 --> 与 "引号" 的文本', segs=[[0, 9, "片段 --> 文本"]])],
            "skipped": [{"s": 90.0, "e": 100.0}],
        }
        note.set_segments_state(state)
        note.save()
        raw = path.read_text(encoding="utf-8")
        comment_line = next(line for line in raw.split("\n") if "my-mind:transcript-segments" in line)
        payload = comment_line.split("my-mind:transcript-segments ", 1)[1]
        self.assertTrue(payload.endswith("-->"))
        inner = payload[: -len("-->")].strip()
        self.assertNotIn("-->", inner)  # 箭头被转义，不会提前终结注释。
        self.assertIn("--\\u003e", inner)
        reloaded = self.seg.InboxNote(path).segments_state()
        self.assertEqual("包含 --> 与 \"引号\" 的文本", reloaded["segments"][0]["text"])
        self.assertEqual("片段 --> 文本", reloaded["segments"][0]["segs"][0][2])
        self.assertEqual(1, reloaded["zero_progress_runs"])
        self.assertEqual(90.0, reloaded["speech_total"])

    def test_field_update_inserts_after_duration_and_replaces_existing(self) -> None:
        path = write_note(self.dir, "note.md")
        note = self.seg.InboxNote(path)
        note.set_field("转写状态", "进行中")
        note.set_field("转写覆盖", "120/470秒")
        note.set_field("转写备注", "第 3 块 faster-whisper 超时：含冒号的备注")
        note.save()
        lines = path.read_text(encoding="utf-8").split("\n")
        duration_index = lines.index("时长: 30:00")
        self.assertEqual("转写状态: 进行中", lines[duration_index + 1])
        self.assertEqual("转写覆盖: 120/470秒", lines[duration_index + 2])
        reloaded = self.seg.InboxNote(path)
        self.assertEqual("进行中", reloaded.field("转写状态"))
        self.assertEqual("120/470秒", reloaded.field("转写覆盖"))
        self.assertEqual("第 3 块 faster-whisper 超时：含冒号的备注", reloaded.field("转写备注"))
        # 再次更新走替换路径，不重复插入。
        reloaded.set_field("转写状态", "已完成")
        reloaded.save()
        raw = path.read_text(encoding="utf-8")
        self.assertEqual(1, raw.count("转写状态:"))
        self.assertIn("转写状态: 已完成", raw)

    def test_modality_transcript_label_updates_frontmatter_and_body(self) -> None:
        path = write_note(self.dir, "note.md")
        note = self.seg.InboxNote(path)
        note.set_modality_transcript("26%")
        note.save()
        raw = path.read_text(encoding="utf-8")
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=不适用 | 转写=26% | 画面解析=未开始", raw)
        self.assertIn("- 模态覆盖：文案=完整 | 图片OCR=不适用 | 转写=26% | 画面解析=未开始", raw)

    def test_legacy_list_comment_is_normalized(self) -> None:
        comment = render_comment([{"s": 0, "e": 156, "done": True}])
        path = write_note(self.dir, "note.md", status="进行中", comment=comment)
        state = self.seg.InboxNote(path).segments_state()
        self.assertEqual(156.0, state["speech_total"])
        self.assertTrue(state["segments"][0]["done"])
        self.assertEqual(0, state["zero_progress_runs"])

    def test_corrupt_comment_raises(self) -> None:
        path = write_note(self.dir, "note.md", comment="<!-- my-mind:transcript-segments {bad json} -->")
        with self.assertRaises(self.seg.SegmentStateError):
            self.seg.InboxNote(path).segments_state()

    def test_bom_note_frontmatter_is_detected(self) -> None:
        """顺手项回归：带 BOM 的笔记 frontmatter 检测不再失效。"""
        path = write_note(self.dir, "note.md")
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        note = self.seg.InboxNote(path)
        self.assertEqual("未开始", note.field("转写状态"))
        self.assertEqual("口播视频", note.field("作品类型"))
        note.set_field("转写状态", "进行中")
        note.save()
        reloaded = self.seg.InboxNote(path)
        self.assertEqual("进行中", reloaded.field("转写状态"))

    def test_save_is_atomic_and_leaves_no_temp_file(self) -> None:
        """M4 回归：save 原子写回，不在笔记目录留下 .tmp 残件。"""
        path = write_note(self.dir, "note.md")
        note = self.seg.InboxNote(path)
        note.set_field("转写状态", "进行中")
        note.save()
        self.assertEqual([], list(self.dir.glob("*.tmp")))
        self.assertIn("转写状态: 进行中", path.read_text(encoding="utf-8"))


class SegmentedProcessingTest(unittest.TestCase):
    """process_note / main 的状态机行为（VAD、ffmpeg、whisper、网络全部打桩）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seg = load_module(SEG_SCRIPT, "test_segmented_processing_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.inbox = self.base / "inbox"
        self.records = self.base / "records"
        self.wav = self.base / "segmented-audio-16k.wav"
        self.wav.write_bytes(b"RIFF-fake-wav" * 8)

    def patch_processing(
        self,
        *,
        intervals=None,
        transcribe=None,
        clock=None,
        patch_audio: bool = True,
    ) -> dict:
        if intervals is None:
            intervals = [(0, 120), (130, 250), (260, 380), (390, 500)]  # 语音 120+120+120+110=470 秒
        if transcribe is None:
            counter = {"n": 0}

            def fake_transcribe(_block_wav):
                counter["n"] += 1
                return (
                    [
                        {"start": 1.0, "end": 9.0, "text": f"第{counter['n']}块的第一句转写文本。"},
                        {"start": 9.5, "end": 18.0, "text": LONG_TEXT},
                    ],
                    "zh",
                    None,
                )

            transcribe = fake_transcribe
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        mocks: dict = {}
        # 默认把平台客户端换成假客户端：M4 转写锁会写 media_cache_dir，假客户端保证落在临时目录；
        # 测试已自带假客户端（无论打桩先后）时沿用其补丁，不覆盖。
        if not isinstance(self.seg.dtk_client, FakePlatformClient):
            fake_platform = FakePlatformClient()
            self.addCleanup(fake_platform.cleanup)
            stack.enter_context(mock.patch.object(self.seg, "dtk_client", fake_platform))
        if not isinstance(self.seg.xhs_client, FakePlatformClient):
            fake_xhs = FakePlatformClient()
            self.addCleanup(fake_xhs.cleanup)
            stack.enter_context(mock.patch.object(self.seg, "xhs_client", fake_xhs))
        if patch_audio:
            mocks["audio"] = stack.enter_context(
                mock.patch.object(self.seg, "ensure_audio_note", return_value=(self.wav, 500.0, None))
            )
        mocks["vad"] = stack.enter_context(
            mock.patch.object(self.seg, "run_vad", return_value=(intervals, 500.0, None))
        )
        mocks["cut"] = stack.enter_context(mock.patch.object(self.seg, "cut_block_audio", return_value=None))
        mocks["transcribe"] = stack.enter_context(
            mock.patch.object(self.seg, "transcribe_block", side_effect=transcribe)
        )
        if clock is not None:
            stack.enter_context(mock.patch.object(self.seg, "_monotonic", side_effect=clock))
        stack.enter_context(mock.patch.object(self.seg, "RUN_RECORD_DIR", self.records))
        stack.enter_context(mock.patch.object(self.seg, "DEFAULT_INBOX", self.inbox))
        return mocks

    def test_budget_exhaustion_stops_mid_run_and_writes_progress(self) -> None:
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频A.md")
        mocks = self.patch_processing(clock=make_clock(start=400.0, step=400.0))
        result = self.seg.process_note(path, deadline=100.0)
        self.assertEqual(1, result.completed_blocks)
        self.assertEqual("进行中", result.status)
        self.assertIn("预算耗尽", result.remark)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 进行中", raw)
        self.assertIn("转写覆盖: 120/470秒", raw)
        self.assertIn("转写=26%", raw)
        self.assertEqual(1, mocks["transcribe"].call_count)
        state = self.seg.InboxNote(path).segments_state()
        self.assertIsNotNone(state)
        self.assertEqual(4, len(state["segments"]))
        self.assertEqual(1, sum(1 for item in state["segments"] if item["done"]))
        self.assertEqual(0, state["zero_progress_runs"])
        # 既有内容不被破坏。
        self.assertIn(DESCRIPTION, raw)
        self.assertIn("## 标签和分类", raw)

    def test_rerun_skips_done_blocks_and_finishes(self) -> None:
        comment = render_comment(
            {
                "v": 1,
                "media_duration": 500.0,
                "speech_total": 470.0,
                "zero_progress_runs": 0,
                "language": "zh",
                "planned_at": "",
                "segments": [
                    block(0, 120, done=True, text="第一块既有转写内容，保留原样。", segs=[[0, 60, "第一块既有转写内容，保留原样。"]]),
                    block(130, 250),
                    block(260, 380),
                    block(390, 500),
                ],
                "skipped": [{"s": 120.0, "e": 130.0}],
            }
        )
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频B.md", status="进行中", coverage="120/470秒", comment=comment)
        mocks = self.patch_processing(clock=make_clock(step=0.5))
        result = self.seg.process_note(path, deadline=10_000.0)
        self.assertEqual(3, result.completed_blocks)
        self.assertEqual(3, mocks["transcribe"].call_count)  # 已完成块不再转写。
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 已完成", raw)
        self.assertIn("转写覆盖: 470/470秒", raw)
        self.assertIn("转写=100%", raw)
        self.assertIn("### 摘要", raw)
        self.assertIn("### 关键点", raw)
        self.assertIn("### 时间线摘录", raw)
        self.assertIn("### 转写摘录", raw)
        self.assertIn("第一块既有转写内容，保留原样。", raw)
        self.assertNotIn("## 视频内容摘录失败", raw)
        self.assertIn("内容摘录来源: 长视频分段转写", raw)

    def test_completion_at_98_percent_despite_failing_tail_block(self) -> None:
        def failing_transcribe(_block_wav):
            return [], "", "faster-whisper 块转写失败：模拟尾块崩溃"

        comment = render_comment(
            {
                "v": 1,
                "media_duration": 140.0,
                "speech_total": 122.0,
                "zero_progress_runs": 0,
                "language": "",
                "planned_at": "",
                "segments": [
                    block(0, 120, done=True, text=LONG_TEXT, segs=[[0, 60, LONG_TEXT[:80]], [60, 118, LONG_TEXT[80:160]]]),
                    block(130, 132, sp=2.0),
                ],
                "skipped": [{"s": 120.0, "e": 130.0}],
            }
        )
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频C.md", status="进行中", coverage="120/122秒", comment=comment)
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            self.patch_processing(transcribe=failing_transcribe, clock=make_clock(step=0.5))
            result = self.seg.process_note(path, deadline=1_000.0)
        self.assertEqual("已完成", result.status)
        self.assertIn("不足 2%", result.remark)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 已完成", raw)
        self.assertIn("转写覆盖: 120/122秒", raw)
        self.assertIn("转写=100%", raw)
        self.assertIn("### 摘要", raw)
        self.assertIn("### 时间线摘录", raw)
        self.assertIn("内容质量: 可推送", raw)  # word_count ≥300 的口播视频按「已转写」重新评估。
        self.assertIn("内容摘录字数: ", raw)
        # 尾块保持未完成，只按 98% 判定收尾；媒体缓存清理。
        state = self.seg.InboxNote(path).segments_state()
        self.assertFalse(state["segments"][1]["done"])
        self.assertEqual(["7704156396664769811"], fake_client.cleaned)

    def test_consecutive_zero_progress_runs_mark_failure(self) -> None:
        def failing_transcribe(_block_wav):
            return [], "", "faster-whisper 块转写失败：模拟持续故障"

        comment = render_comment(
            {
                "v": 1,
                "media_duration": 220.0,
                "speech_total": 200.0,
                "zero_progress_runs": 0,
                "language": "",
                "planned_at": "",
                "segments": [block(0, 100), block(120, 220)],
                "skipped": [{"s": 100.0, "e": 120.0}],
            }
        )
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频D.md", status="进行中", coverage="0/200秒", comment=comment)
        self.patch_processing(transcribe=failing_transcribe, clock=make_clock(step=0.5))
        first = self.seg.process_note(path, deadline=1_000.0)
        self.assertTrue(first.zero_progress)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 进行中", raw)
        self.assertIn("零进展计数 1/2", raw)
        second = self.seg.process_note(path, deadline=1_000.0)
        self.assertEqual("失败", second.status)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 失败", raw)
        self.assertIn("连续 2 次运行零进展", raw)
        self.assertIn("转写=失败", raw)
        state = self.seg.InboxNote(path).segments_state()
        self.assertEqual(2, state["zero_progress_runs"])

    def test_media_refetch_failure_marks_failed(self) -> None:
        fake_client = FakePlatformClient(parse_error=RuntimeError("自建 dtk 服务解析失败：cookie 失效"))
        self.addCleanup(fake_client.cleanup)
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频E.md")
        self.patch_processing(patch_audio=False)  # 走真实 ensure_audio_note → 触发重取失败。
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            result = self.seg.process_note(path, deadline=1_000.0)
        self.assertEqual("失败", result.status)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 失败", raw)
        self.assertIn("媒体重取失败", raw)
        self.assertIn("cookie 失效", raw)
        self.assertEqual(2, len(fake_client.parsed))  # 来源链接 + 原始链接 都试过。

    def test_cached_wav_short_circuits_refetch(self) -> None:
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        cache_dir = fake_client.media_cache_dir("7704156396664769811")
        cache_dir.mkdir(parents=True)
        write_real_wav(cache_dir / "segmented-audio-16k.wav")  # H1 后缓存命中需要通过 wave 完整性校验。
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频F.md")
        mocks = self.patch_processing(patch_audio=False)  # 走真实 ensure_audio_note → 触发重取失败。
        self.assertNotIn("audio", mocks)  # 未打桩 ensure_audio_note，走真实缓存命中路径。
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            with mock.patch.object(self.seg, "run_ffmpeg_extract") as extract:
                self.seg.process_note(path, deadline=1_000.0)
        extract.assert_not_called()
        self.assertEqual([], fake_client.downloaded)
        self.assertIn("转写状态: 进行中", path.read_text(encoding="utf-8"))

    def test_cached_video_media_is_extracted_without_refetch(self) -> None:
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        cache_dir = fake_client.media_cache_dir("7704156396664769811")
        cache_dir.mkdir(parents=True)
        (cache_dir / "7704156396664769811.mp4").write_bytes(b"fake-mp4-bytes")
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频G.md")
        self.patch_processing(patch_audio=False)
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            with mock.patch.object(
                self.seg,
                "run_ffmpeg_extract",
                side_effect=lambda _media, wav: (wav.parent.mkdir(parents=True, exist_ok=True), wav.write_bytes(b"RIFF-new" * 8), None)[-1],
            ) as extract:
                self.seg.process_note(path, deadline=1_000.0)
        extract.assert_called_once()
        self.assertEqual([], fake_client.downloaded)
        self.assertIn("转写状态: 进行中", path.read_text(encoding="utf-8"))

    def test_truncated_cached_wav_is_discarded_and_refetched(self) -> None:
        """H1：合法头+截断数据的半截 wav 不能当有效音频，删除后走媒体重取重新抽音频。"""
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        cache_dir = fake_client.media_cache_dir("7704156396664769811")
        wav_path = cache_dir / "segmented-audio-16k.wav"
        write_real_wav(wav_path, seconds=10.0)
        truncate_wav(wav_path)
        path = write_note(self.inbox, "2026-11-17 抖音 - 截断缓存A.md")
        self.patch_processing(patch_audio=False, clock=make_clock(start=400.0, step=400.0))
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            with mock.patch.object(
                self.seg,
                "run_ffmpeg_extract",
                side_effect=lambda _media, wav: (wav.parent.mkdir(parents=True, exist_ok=True), wav.write_bytes(b"RIFF-fresh-extract" * 4), None)[-1],
            ) as extract:
                result = self.seg.process_note(path, deadline=100.0)
        extract.assert_called_once()  # 缓存被判定无效，按缓存缺失路径重取媒体并重新抽音频。
        self.assertEqual([(MEDIA_URL, self.seg.MAX_VIDEO_BYTES, "7704156396664769811.mp4")], fake_client.downloaded)
        self.assertEqual(b"RIFF-fresh-extract" * 4, wav_path.read_bytes())  # 半截内容已被替换。
        self.assertEqual("进行中", result.status)
        self.assertIn("转写状态: 进行中", path.read_text(encoding="utf-8"))

    def test_garbage_cached_wav_is_deleted_and_refetched(self) -> None:
        """H1：纯垃圾字节的假 wav（wave 打不开）同样按缓存缺失重走。"""
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        cache_dir = fake_client.media_cache_dir("7704156396664769811")
        wav_path = cache_dir / "segmented-audio-16k.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(b"not-a-wav-at-all" * 64)
        path = write_note(self.inbox, "2026-11-17 抖音 - 垃圾缓存B.md")
        self.patch_processing(patch_audio=False, clock=make_clock(start=400.0, step=400.0))
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            with mock.patch.object(
                self.seg,
                "run_ffmpeg_extract",
                side_effect=lambda _media, wav: (wav.parent.mkdir(parents=True, exist_ok=True), wav.write_bytes(b"RIFF-fresh-extract" * 4), None)[-1],
            ) as extract:
                self.seg.process_note(path, deadline=100.0)
        extract.assert_called_once()
        self.assertEqual(1, len(fake_client.downloaded))
        self.assertEqual(b"RIFF-fresh-extract" * 4, wav_path.read_bytes())

    def test_corrupt_numeric_state_marks_note_failed(self) -> None:
        """M3：分段清单数值字段手工损坏 → SegmentStateError → 笔记标失败转人工，不裸抛 ValueError。"""
        def base_state() -> dict:
            return json.loads(
                json.dumps(
                    {
                        "v": 1,
                        "media_duration": 220.0,
                        "speech_total": 200.0,
                        "zero_progress_runs": 0,
                        "language": "",
                        "planned_at": "",
                        "segments": [block(0, 100), block(120, 220)],
                        "skipped": [{"s": 100.0, "e": 120.0}],
                    }
                )
            )

        cases = {
            "s": lambda state: state["segments"][0].update(s="abc"),
            "e": lambda state: state["segments"][0].update(e="xyz"),
            "sp": lambda state: state["segments"][0].update(sp="bad"),
            "segs": lambda state: state["segments"][0].update(done=True, segs=[["bad", 5, "文本"]]),
            "skipped": lambda state: state.update(skipped=[{"s": "oops", "e": 120.0}]),
            "media_duration": lambda state: state.update(media_duration="oops"),
            "zero_progress_runs": lambda state: state.update(zero_progress_runs="many"),
        }
        for field, corrupt in cases.items():
            with self.subTest(field=field):
                state = base_state()
                corrupt(state)
                path = write_note(
                    self.inbox,
                    f"2026-11-17 抖音 - 损坏{field}.md",
                    status="进行中",
                    coverage="0/200秒",
                    comment=render_comment(state),
                )
                self.patch_processing(clock=make_clock(step=0.5))
                result = self.seg.process_note(path, deadline=1_000.0)
                self.assertEqual("失败", result.status)
                raw = path.read_text(encoding="utf-8")
                self.assertIn("转写状态: 失败", raw)
                self.assertIn("分段清单损坏", raw)
                self.assertIn(field, raw)  # 错误信息包含损坏字段名。

    def test_fresh_lock_skips_note_without_writing(self) -> None:
        """M4：另一进程持有新鲜转写锁时跳过该笔记（stdout 说明），不写笔记不删他人锁。"""
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        lock_dir = fake_client.media_cache_dir("7704156396664769811")
        lock_dir.mkdir(parents=True)
        lock_path = lock_dir / ".transcribe-lock"
        lock_path.write_text("99999 2026-11-18 00:00:00\n", encoding="utf-8")
        path = write_note(self.inbox, "2026-11-17 抖音 - 锁占用.md")
        before = path.read_text(encoding="utf-8")
        mocks = self.patch_processing(clock=make_clock(step=0.5))
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = self.seg.process_note(path, deadline=1_000.0)
        self.assertEqual("", result.status)
        self.assertIn("跳过该笔记", result.remark)
        self.assertIn("另一进程正在转写", result.remark)
        self.assertIn("锁占用", stdout.getvalue())
        mocks["transcribe"].assert_not_called()
        self.assertEqual(before, path.read_text(encoding="utf-8"))  # 笔记未被触碰。
        self.assertTrue(lock_path.exists())  # 他人持有的锁不被误删。

    def test_stale_lock_is_preempted_and_released(self) -> None:
        """M4：超过 2 小时的过期锁视为死锁可抢占，运行结束删除。"""
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        lock_dir = fake_client.media_cache_dir("7704156396664769811")
        lock_dir.mkdir(parents=True)
        lock_path = lock_dir / ".transcribe-lock"
        lock_path.write_text("88888 2026-11-17 00:00:00\n", encoding="utf-8")
        stale = time.time() - 3 * 3600
        os.utime(lock_path, (stale, stale))
        path = write_note(self.inbox, "2026-11-17 抖音 - 死锁抢占.md")
        self.patch_processing(clock=make_clock(start=400.0, step=400.0))
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            result = self.seg.process_note(path, deadline=100.0)
        self.assertEqual("进行中", result.status)  # 抢占成功，正常处理。
        self.assertIn("转写状态: 进行中", path.read_text(encoding="utf-8"))
        self.assertFalse(lock_path.exists())  # 运行结束锁已删除。

    def test_lock_held_during_run_and_deleted_after(self) -> None:
        """M4：正常处理期间持有锁，运行结束（含预算收尾路径）删除。"""
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        lock_dir = fake_client.media_cache_dir("7704156396664769811")
        lock_path = lock_dir / ".transcribe-lock"
        observed: dict[str, bool] = {}

        def transcribe_with_lock_check(_block_wav):
            observed["lock_exists_during_run"] = lock_path.exists()
            return ([{"start": 0.5, "end": 8.0, "text": LONG_TEXT}], "zh", None)

        path = write_note(self.inbox, "2026-11-17 抖音 - 锁生命周期.md")
        self.patch_processing(transcribe=transcribe_with_lock_check, clock=make_clock(start=400.0, step=400.0))
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            result = self.seg.process_note(path, deadline=100.0)
        self.assertEqual("进行中", result.status)
        self.assertTrue(observed.get("lock_exists_during_run"))  # 转写期间锁在场。
        self.assertFalse(lock_path.exists())  # 结束后删除。

    def test_no_speech_media_marks_failed(self) -> None:
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频H.md")
        self.patch_processing(intervals=[])
        result = self.seg.process_note(path, deadline=1_000.0)
        self.assertEqual("失败", result.status)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("未检测到语音区间", raw)
        self.assertIn("转写状态: 失败", raw)

    def test_body_fidelity_preserves_existing_content(self) -> None:
        extra_body = "```python\nprint('keep me')\n```\n\n- 嵌套列表一\n  - 嵌套列表二\n\n<!-- 其他系统注释：勿动 -->"
        def arrow_transcribe(_block_wav):
            return ([{"start": 0.5, "end": 5.0, "text": "文本带箭头 --> 与 \"引号\"。"}], "zh", None)

        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频I.md", extra_body=extra_body)
        self.patch_processing(transcribe=arrow_transcribe, clock=make_clock(start=400.0, step=400.0))
        self.seg.process_note(path, deadline=100.0)
        raw = path.read_text(encoding="utf-8")
        for sentinel in (
            "```python",
            "print('keep me')",
            "- 嵌套列表一",
            "  - 嵌套列表二",
            "<!-- 其他系统注释：勿动 -->",
            DESCRIPTION,
            "## 解析说明",
            "长视频已进入分段转写队列，等待后台总控续跑。",
            "来源链接: https://www.douyin.com/video/7704156396664769811",
            "内容编号: 7704156396664769811",
            "## 原始链接",
            "标题: 长视频样本",
        ):
            self.assertIn(sentinel, raw)
        state = self.seg.InboxNote(path).segments_state()
        self.assertEqual('文本带箭头 --> 与 "引号"。', state["segments"][0]["text"])
        comment_line = next(line for line in raw.split("\n") if "my-mind:transcript-segments" in line)
        payload = comment_line.split("my-mind:transcript-segments ", 1)[1]
        inner = payload[: -len("-->")].strip()
        self.assertNotIn("-->", inner)
        self.assertIn("--\\u003e", inner)


class SegmentedMediaRefetchTest(unittest.TestCase):
    """媒体缓存丢失后的重取路径（dtk / xhs 两条路，走 main 做端到端验证）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seg = load_module(SEG_SCRIPT, "test_segmented_refetch_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.inbox = self.base / "inbox"
        self.records = self.base / "records"
        self.wav = self.base / "segmented-audio-16k.wav"

    def _patch_common(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(self.seg, "run_vad", return_value=([(0, 100), (110, 210)], 220.0, None)))
        stack.enter_context(mock.patch.object(self.seg, "cut_block_audio", return_value=None))
        counter = {"n": 0}

        def fake_transcribe(_wav):
            counter["n"] += 1
            return ([{"start": 0.5, "end": 8.0, "text": f"第{counter['n']}块转写文本，{LONG_TEXT}"}], "zh", None)

        stack.enter_context(mock.patch.object(self.seg, "transcribe_block", side_effect=fake_transcribe))
        stack.enter_context(mock.patch.object(self.seg, "_monotonic", side_effect=make_clock(step=0.5)))
        stack.enter_context(mock.patch.object(self.seg, "RUN_RECORD_DIR", self.records))

    def test_douyin_refetch_downloads_and_extracts_audio(self) -> None:
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        path = write_note(self.inbox, "2026-11-17 抖音 - 长视频重取.md")
        self._patch_common()
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            with mock.patch.object(
                self.seg,
                "run_ffmpeg_extract",
                side_effect=lambda _media, wav: (wav.parent.mkdir(parents=True, exist_ok=True), wav.write_bytes(b"RIFF-extracted" * 8), None)[-1],
            ) as extract:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    rc = self.seg.main(["--note", str(path), "--budget-seconds", "900"])
        self.assertEqual(0, rc)
        self.assertEqual([(MEDIA_URL, 1024 * 1024 * 1024, "7704156396664769811.mp4")], fake_client.downloaded)
        extract.assert_called_once()
        self.assertEqual(["https://www.douyin.com/video/7704156396664769811"], fake_client.parsed)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 已完成", raw)  # 预算充足，两块全部转完并收尾。
        self.assertEqual(["7704156396664769811"], fake_client.cleaned)  # 完成后清理媒体缓存。
        state = self.seg.InboxNote(path).segments_state()
        self.assertEqual(1820.0, state["media_duration"])
        self.assertTrue(all(item["done"] for item in state["segments"]))
        # 运行记录与 stdout 摘要。
        records = list(self.records.glob("分段转写-*.md"))
        self.assertEqual(1, len(records))
        record_text = records[0].read_text(encoding="utf-8")
        self.assertIn("类别: 运行记录", record_text)
        self.assertIn("记录类型: 分段转写", record_text)
        self.assertIn("生成时间: ", record_text)
        self.assertIn("长视频重取.md", record_text)
        self.assertIn("长视频重取.md", stdout.getvalue())
        self.assertIn("完成 2 块", stdout.getvalue())

    def test_xhs_refetch_uses_note_id_cache_dir(self) -> None:
        fake_client = FakePlatformClient()
        self.addCleanup(fake_client.cleanup)
        note_id = "65a1b2c3000000001203a1b2"
        path = write_note(self.inbox, "2026-11-18 小红书 - 视频笔记.md", platform="小红书", content_id=note_id)
        self._patch_common()
        with mock.patch.object(self.seg, "xhs_client", fake_client):
            with mock.patch.object(
                self.seg,
                "run_ffmpeg_extract",
                side_effect=lambda _media, wav: (wav.parent.mkdir(parents=True, exist_ok=True), wav.write_bytes(b"RIFF-xhs" * 8), None)[-1],
            ):
                rc = self.seg.main(["--note", str(path), "--budget-seconds", "900"])
        self.assertEqual(0, rc)
        self.assertEqual([(XHS_MEDIA_URL, 1024 * 1024 * 1024, f"{note_id}.mp4")], fake_client.downloaded)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 已完成", raw)  # 两块都转完（预算充足）→ 覆盖 100% 并收尾。
        self.assertIn("转写覆盖: 200/200秒", raw)
        self.assertIn("转写=100%", raw)

    def test_refetch_download_failure_marks_failed(self) -> None:
        fake_client = FakePlatformClient(fail_download=True)
        self.addCleanup(fake_client.cleanup)
        path = write_note(self.inbox, "2026-11-17 抖音 - 下载失败.md")
        self._patch_common()
        with mock.patch.object(self.seg, "dtk_client", fake_client):
            rc = self.seg.main(["--note", str(path), "--budget-seconds", "900"])
        self.assertEqual(0, rc)  # 转写失败是正常业务结果，不是硬错误。
        raw = path.read_text(encoding="utf-8")
        self.assertIn("转写状态: 失败", raw)
        self.assertIn("媒体重取失败", raw)


class SegmentedCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seg = load_module(SEG_SCRIPT, "test_segmented_cli_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.inbox = self.base / "inbox"
        self.records = self.base / "records"
        self.wav = self.base / "segmented-audio-16k.wav"
        self.wav.write_bytes(b"RIFF-cli" * 8)

    def _patch_processing(self, *, clock=None):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        # 默认假平台客户端：M4 转写锁目录保持在临时目录，不碰真实 ~/.cache。
        if not isinstance(self.seg.dtk_client, FakePlatformClient):
            fake_platform = FakePlatformClient()
            self.addCleanup(fake_platform.cleanup)
            stack.enter_context(mock.patch.object(self.seg, "dtk_client", fake_platform))
        if not isinstance(self.seg.xhs_client, FakePlatformClient):
            fake_xhs = FakePlatformClient()
            self.addCleanup(fake_xhs.cleanup)
            stack.enter_context(mock.patch.object(self.seg, "xhs_client", fake_xhs))
        stack.enter_context(mock.patch.object(self.seg, "ensure_audio_note", return_value=(self.wav, 500.0, None)))
        stack.enter_context(mock.patch.object(self.seg, "run_vad", return_value=([(0, 120), (130, 250)], 260.0, None)))
        stack.enter_context(mock.patch.object(self.seg, "cut_block_audio", return_value=None))
        counter = {"n": 0}

        def fake_transcribe(_wav):
            counter["n"] += 1
            return ([{"start": 0.5, "end": 8.0, "text": f"第{counter['n']}块：{LONG_TEXT}"}], "zh", None)

        transcribe = stack.enter_context(mock.patch.object(self.seg, "transcribe_block", side_effect=fake_transcribe))
        if clock is not None:
            stack.enter_context(mock.patch.object(self.seg, "_monotonic", side_effect=clock))
        stack.enter_context(mock.patch.object(self.seg, "RUN_RECORD_DIR", self.records))
        stack.enter_context(mock.patch.object(self.seg, "DEFAULT_INBOX", self.inbox))
        return transcribe

    def test_missing_note_is_hard_error(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = self.seg.main(["--note", str(self.base / "nope.md")])
        self.assertEqual(1, rc)
        self.assertIn("笔记不存在", stderr.getvalue())

    def test_dry_run_writes_nothing(self) -> None:
        path = write_note(self.inbox, "2026-11-17 抖音 - 干跑.md")
        before = path.read_text(encoding="utf-8")
        transcribe = self._patch_processing()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = self.seg.main(["--note", str(path), "--dry-run"])
        self.assertEqual(0, rc)
        self.assertEqual(before, path.read_text(encoding="utf-8"))
        self.assertEqual([], list(self.records.glob("*.md")))
        transcribe.assert_not_called()
        self.assertIn("dry-run", stdout.getvalue())
        self.assertIn("干跑.md", stdout.getvalue())

    def test_scan_respects_max_notes_and_time_order(self) -> None:
        note_a = write_note(self.inbox, "2026-11-17 抖音 - A.md")
        note_b = write_note(
            self.inbox,
            "2026-11-17 抖音 - B.md",
            status="进行中",
            coverage="0/200秒",
            comment=render_comment(
                {
                    "v": 1,
                    "media_duration": 220.0,
                    "speech_total": 200.0,
                    "zero_progress_runs": 0,
                    "language": "",
                    "planned_at": "",
                    "segments": [block(0, 100), block(110, 210, sp=100.0)],
                    "skipped": [],
                }
            ),
        )
        note_c = write_note(self.inbox, "2026-11-18 抖音 - C.md")
        write_note(self.inbox, "2026-11-18 抖音 - D.md", status="已完成", coverage="470/470秒")
        write_note(self.inbox, "2026-11-19 抖音 - E.md", status="失败", remark="历史失败")
        transcribe = self._patch_processing(clock=make_clock(step=0.5))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = self.seg.main(["--max-notes", "2", "--budget-seconds", "900"])
        self.assertEqual(0, rc)
        self.assertIn("转写状态: 已完成", note_a.read_text(encoding="utf-8"))
        self.assertIn("转写状态: 已完成", note_b.read_text(encoding="utf-8"))
        # C 未被处理（max-notes=2 只取文件名最早的 A、B）；D/E 不在待处理集合。
        self.assertIn("转写状态: 未开始", note_c.read_text(encoding="utf-8"))
        self.assertNotIn("my-mind:transcript-segments", note_c.read_text(encoding="utf-8"))
        self.assertEqual(4, transcribe.call_count)  # A 首次分段 2 块 + B 既有 2 块待转写。
        summary = stdout.getvalue()
        self.assertIn("处理 2 条笔记", summary)

    def test_budget_runs_out_before_second_note(self) -> None:
        note_a = write_note(self.inbox, "2026-11-17 抖音 - A.md")
        note_b = write_note(self.inbox, "2026-11-17 抖音 - B2.md")
        self._patch_processing(clock=make_clock(start=400.0, step=400.0))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = self.seg.main(["--max-notes", "2", "--budget-seconds", "100"])
        self.assertEqual(0, rc)
        self.assertIn("转写状态: 进行中", note_a.read_text(encoding="utf-8"))
        self.assertIn("转写状态: 未开始", note_b.read_text(encoding="utf-8"))

    def test_scan_inbox_filters_active_statuses(self) -> None:
        write_note(self.inbox, "2026-11-17 抖音 - A.md", status="未开始")
        write_note(self.inbox, "2026-11-17 抖音 - B.md", status="进行中", coverage="1/2秒")
        write_note(self.inbox, "2026-11-18 抖音 - C.md", status="已完成", coverage="2/2秒")
        write_note(self.inbox, "2026-11-18 抖音 - D.md", status="失败")
        (self.inbox / "2026-11-19 普通笔记.md").write_text("---\n类别: 收件箱\n---\n\n无转写字段的普通笔记。\n", encoding="utf-8")
        selected = self.seg.scan_inbox(self.inbox, 5)
        self.assertEqual(
            ["2026-11-17 抖音 - A.md", "2026-11-17 抖音 - B.md"],
            [path.name for path in selected],
        )
        self.assertEqual([self.inbox / "2026-11-17 抖音 - A.md"], self.seg.scan_inbox(self.inbox, 1))

    def test_scan_inbox_skips_non_utf8_note_without_crash(self) -> None:
        """M6 ③：非 UTF-8 编码的收件箱笔记被跳过，不拖垮整轮扫描。"""
        write_note(self.inbox, "2026-11-17 抖音 - A.md")
        (self.inbox / "2026-11-16 抖音 - 乱码笔记.md").write_bytes(b"\xff\xfe\x00d\x00o\x00c\xff\n---\n\xfa\xb4")
        selected = self.seg.scan_inbox(self.inbox, 5)
        self.assertEqual([self.inbox / "2026-11-17 抖音 - A.md"], selected)


class CaptureSegmentedQueueTest(unittest.TestCase):
    """capture_link 集成：超一段式上限的长视频入箱即入队，不再写「超时跳过」。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.capture = load_module(CAPTURE_SCRIPT, "test_segmented_capture_module")

    def test_over_limit_video_initializes_queue_fields(self) -> None:
        info = {
            "media_url": "https://cdn.douyin.test/video.mp4",
            "duration": "1800",
            "work_type": "口播视频",
            "description": DESCRIPTION,
        }
        with mock.patch.object(self.capture, "extract_audio") as extract:
            self.capture.attach_content_extract(
                info,
                backend="auto",
                model="small",
                language="zh",
                command_template="",
                max_seconds=self.capture.DEFAULT_MAX_TRANSCRIBE_SECONDS,
                platform="抖音",
            )
        extract.assert_not_called()  # 入箱快路径：本命令内不开始转写。
        self.assertEqual(
            {"duration_seconds": 1800, "max_transcribe_seconds": 360},
            info["segmented_transcript_queue"],
        )
        self.assertIn("长视频已进入分段转写队列，等待后台总控续跑", info["content_extract_error"])
        self.assertEqual("长视频已进入分段转写队列，等待后台总控续跑。", info["parse_notice"])

        filename, note = self.capture.build_note("https://www.douyin.com/video/7704156396664769811", info, None)
        self.assertIn("转写状态: 未开始", note)
        self.assertIn("转写覆盖: 0/0秒", note)
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=不适用 | 转写=未开始(分段) | 画面解析=未开始", note)
        self.assertIn("- 转写状态：未开始", note)
        self.assertIn("- 转写覆盖：0/0秒", note)
        self.assertIn("长视频已进入分段转写队列，等待后台总控续跑。", note)
        self.assertIn("内容质量: 需继续解析", note)

    def test_forced_extract_content_over_limit_also_queues_without_blocking(self) -> None:
        info = {
            "media_url": "https://cdn.douyin.test/video.mp4",
            "duration": "400",
            "work_type": "口播视频",
            "description": DESCRIPTION,
        }
        with mock.patch.object(self.capture, "extract_audio") as extract:
            self.capture.attach_content_extract(
                info,
                backend="auto",
                model="small",
                language="zh",
                command_template="",
                max_seconds=360,
                platform="抖音",
            )
        extract.assert_not_called()
        self.assertEqual(400, info["segmented_transcript_queue"]["duration_seconds"])

    def test_over_limit_video_without_platform_kwarg_infers_douyin_from_info(self) -> None:
        """capture_url 总会传 platform；此测试锁定 info 自带作品页链接时的兜底推断仍能入队。"""
        info = {
            "media_url": "https://cdn.douyin.test/video.mp4",
            "duration": "500",
            "work_type": "口播视频",
            "webpage_url": "https://www.douyin.com/video/7704156396664769811",
            "description": DESCRIPTION,
        }
        with mock.patch.object(self.capture, "extract_audio") as extract:
            self.capture.attach_content_extract(
                info,
                backend="auto",
                model="small",
                language="zh",
                command_template="",
                max_seconds=360,
            )
        extract.assert_not_called()
        self.assertEqual(500, info["segmented_transcript_queue"]["duration_seconds"])

    def test_over_limit_youtube_keeps_legacy_error_without_queue_fields(self) -> None:
        """M2：非抖音/小红书平台（分段执行器不支持媒体重取）不入队，保持批次 4 之前的干净错误。"""
        url = "https://www.youtube.com/watch?v=longvideo"
        info = {
            "media_url": "https://rr1---sn-x.googlevideo.com/videoplayback.mp4",
            "duration": "400",
            "title": "长视频",
            "webpage_url": url,
            "description": DESCRIPTION,
        }
        with mock.patch.object(self.capture, "extract_audio") as extract:
            self.capture.attach_content_extract(
                info,
                backend="auto",
                model="small",
                language="zh",
                command_template="",
                max_seconds=360,
                platform="YouTube",
            )
        extract.assert_not_called()
        self.assertNotIn("segmented_transcript_queue", info)
        self.assertEqual("视频时长 06:40 超过转写上限 06:00", info["content_extract_error"])
        self.assertNotIn("parse_notice", info)

        filename, note = self.capture.build_note(url, info, None)
        self.assertNotIn("转写状态:", note)
        self.assertNotIn("转写覆盖:", note)
        self.assertNotIn("转写备注:", note)
        self.assertNotIn("转写=未开始(分段)", note)
        self.assertNotIn("分段转写队列", note)
        self.assertIn("视频时长 06:40 超过转写上限 06:00", note)
        self.assertIn("内容质量: 需继续解析", note)

    def test_within_limit_video_still_uses_one_shot_path(self) -> None:
        info = {"media_url": "https://cdn.douyin.test/short.mp4", "duration": "120", "work_type": "口播视频"}
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        audio_path = Path(temp.name) / "media-audio.mp3"
        audio_path.write_bytes(b"fake-audio")
        with mock.patch.object(self.capture, "extract_audio", return_value=(audio_path, None)) as extract, mock.patch.object(
            self.capture,
            "transcribe_audio",
            return_value=(
                "短视频转写文本。",
                [{"time": "00:01", "text": "短视频转写文本。"}],
                {"backend": "faster-whisper", "model": "small", "language": "zh"},
                None,
            ),
        ) as transcribe:
            self.capture.attach_content_extract(
                info,
                backend="auto",
                model="small",
                language="zh",
                command_template="",
                max_seconds=360,
            )
        extract.assert_called_once()
        transcribe.assert_called_once()
        self.assertNotIn("segmented_transcript_queue", info)
        self.assertTrue(info["content_extract"])


class WavCacheValidationTest(unittest.TestCase):
    """H1 单元级：find_cached_audio_or_media 对缓存 wav 的 wave 头完整性校验。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seg = load_module(SEG_SCRIPT, "test_segmented_wavcache_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cache_dir = Path(temp.name) / "media"

    def test_valid_wav_hits_cache_and_is_kept(self) -> None:
        wav = self.cache_dir / self.seg.AUDIO_WAV_NAME
        write_real_wav(wav, seconds=3.0)
        cached_wav, cached_media = self.seg.find_cached_audio_or_media(self.cache_dir)
        self.assertEqual(wav, cached_wav)
        self.assertIsNone(cached_media)
        self.assertTrue(wav.exists())

    def test_truncated_wav_is_deleted_and_treated_as_miss(self) -> None:
        wav = self.cache_dir / self.seg.AUDIO_WAV_NAME
        write_real_wav(wav, seconds=5.0)
        truncate_wav(wav)
        cached_wav, cached_media = self.seg.find_cached_audio_or_media(self.cache_dir)
        self.assertIsNone(cached_wav)
        self.assertIsNone(cached_media)
        self.assertFalse(wav.exists())  # 半截缓存被删除。

    def test_garbage_wav_is_deleted_and_treated_as_miss(self) -> None:
        wav = self.cache_dir / self.seg.AUDIO_WAV_NAME
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"\x00" * 5000)
        cached_wav, cached_media = self.seg.find_cached_audio_or_media(self.cache_dir)
        self.assertIsNone(cached_wav)
        self.assertIsNone(cached_media)
        self.assertFalse(wav.exists())

    def test_invalid_wav_falls_back_to_cached_media(self) -> None:
        wav = self.cache_dir / self.seg.AUDIO_WAV_NAME
        write_real_wav(wav, seconds=2.0)
        truncate_wav(wav)
        media = self.cache_dir / "7704156396664769811.mp4"
        media.write_bytes(b"fake-mp4")
        cached_wav, cached_media = self.seg.find_cached_audio_or_media(self.cache_dir)
        self.assertIsNone(cached_wav)
        self.assertEqual(media, cached_media)  # 缓存媒体仍可用于重新抽音频。
        self.assertFalse(wav.exists())


class SubprocessOutputRobustnessTest(unittest.TestCase):
    """M6 ②：子进程输出脏 JSON / 非 UTF-8 时返回中文错误，笔记进程不崩。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seg = load_module(SEG_SCRIPT, "test_segmented_subprocess_module")

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.wav = Path(temp.name) / "probe.wav"
        self.wav.write_bytes(b"RIFF-probe" * 8)

    def test_run_vad_dirty_json_output_returns_chinese_error(self) -> None:
        def fake_run(cmd, **_kwargs):
            if "import numpy" in cmd[2]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "前置噪声行\n{bad json", "")

        with mock.patch.object(self.seg, "candidate_transcribe_pythons", return_value=[sys.executable]):
            with mock.patch.object(self.seg.subprocess, "run", side_effect=fake_run):
                intervals, duration, error = self.seg.run_vad(self.wav)
        self.assertEqual([], intervals)
        self.assertEqual(0.0, duration)
        self.assertIsNotNone(error)
        self.assertIn("VAD 输出不是 JSON", error)

    def test_run_vad_non_utf8_output_returns_chinese_error(self) -> None:
        """真实子进程向 stdout 写非法 UTF-8 字节：errors=replace 保证不抛 UnicodeDecodeError。"""
        real_run = subprocess.run
        helper = "import sys; sys.stdout.buffer.write(b'\\xff\\xfe{dirty}'); sys.stdout.flush()"

        def fake_run(cmd, **kwargs):
            if "import numpy" in cmd[2]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, **kwargs)

        with mock.patch.object(self.seg, "candidate_transcribe_pythons", return_value=[sys.executable]):
            with mock.patch.object(self.seg, "VAD_HELPER", helper):
                with mock.patch.object(self.seg.subprocess, "run", side_effect=fake_run):
                    intervals, duration, error = self.seg.run_vad(self.wav)
        self.assertEqual([], intervals)
        self.assertEqual(0.0, duration)
        self.assertIn("VAD 输出不是 JSON", error)

    def test_transcribe_block_dirty_worker_output_returns_chinese_error(self) -> None:
        """常驻转写进程应答非 UTF-8/脏 JSON：返回中文错误并关闭 worker，不崩笔记进程。"""
        helper = "import sys\nsys.stdin.readline()\nsys.stdout.buffer.write(b'\\xff\\xfe{bad}\\n')\nsys.stdout.flush()\n"

        def fake_run(cmd, **_kwargs):
            self.assertEqual("import faster_whisper", cmd[2])
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(self.seg, "candidate_transcribe_pythons", return_value=[sys.executable]):
            with mock.patch.object(self.seg, "WHISPER_SERVER_HELPER", helper):
                with mock.patch.object(self.seg.subprocess, "run", side_effect=fake_run):
                    segments, language, error = self.seg.transcribe_block(self.wav)
        self.assertEqual([], segments)
        self.assertEqual("", language)
        self.assertIsNotNone(error)
        self.assertIn("输出不是 JSON", error)
        self.assertIsNone(self.seg._whisper_worker)  # 失败 worker 已关闭，不残留脏状态。


if __name__ == "__main__":
    unittest.main()
