from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / ".codex/skills/backend-control/scripts/backend_health_check.py"
RESUME_COMMAND = "python3 .codex/skills/inbox-capture/scripts/segmented_transcribe.py --budget-seconds 900"
SUGGESTION_TEXT = f"运行分段转写续跑（预算 900 秒/次）：`{RESUME_COMMAND}`"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_inbox_note(inbox: Path, name: str, fields: dict[str, str]) -> Path:
    lines = ["---", "类别: 收件箱", f'标题: "{name}"']
    for key, value in fields.items():
        lines.append(f"{key}: {value}" if value else f"{key}:")
    lines.extend(["---", "", f"# {name}", ""])
    path = inbox / f"{name}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def make_collect_args(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        run_dir=str(root / "85_运行记录"),
        flow_dir=str(root / "05_流转区"),
        inbox=str(root / "00_收件箱"),
        automations_dir=str(root / "automations"),
        push_state=str(root / "85_运行记录" / "前台推送状态.json"),
        feedback_queue=str(root / "85_运行记录" / "前台反馈队列.jsonl"),
        confirmation_queue=str(root / "85_运行记录" / "待确认事项.jsonl"),
    )


class TranscriptQueueStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = load_module(BACKEND, "test_transcript_queue_stats_backend")

    def test_queue_counts_statuses_and_latest_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = Path(temp_dir) / "00_收件箱"
            inbox.mkdir()
            (inbox / "目录说明.md").write_text("---\n转写状态: 进行中\n---\n", encoding="utf-8")
            write_inbox_note(inbox, "2026-11-18 抖音 - 普通图文笔记", {"处理状态": "已分拣"})
            write_inbox_note(inbox, "2026-11-18 抖音 - 长视频甲", {"转写状态": "未开始"})
            write_inbox_note(inbox, "2026-11-18 抖音 - 长视频乙", {"转写状态": "未开始", "转写覆盖": "0/1800秒"})
            write_inbox_note(inbox, "2026-11-18 抖音 - 长视频丙", {"转写状态": "进行中", "转写覆盖": "300/2400秒", "转写备注": "首次续跑完成前 2 块"})
            latest = write_inbox_note(
                inbox,
                "2026-11-18 抖音 - 长视频丁",
                {"转写状态": "进行中", "转写覆盖": "720/1800秒", "转写备注": "第7块 faster-whisper 超时，下次续跑"},
            )
            write_inbox_note(inbox, "2026-11-18 抖音 - 长视频戊", {"转写状态": "已完成", "转写覆盖": "1800/1800秒"})
            write_inbox_note(inbox, "2026-11-18 抖音 - 长视频己", {"转写状态": "失败", "转写备注": "媒体缓存丢失，重取失败"})
            older = inbox / "2026-11-18 抖音 - 长视频丙.md"
            os.utime(older, (1_700_000_000, 1_700_000_000))
            os.utime(latest, (1_700_000_100, 1_700_000_100))

            queue = self.backend.inspect_transcript_queue(inbox)

            self.assertEqual(6, queue["total"])
            self.assertEqual(2, queue["not_started"])
            self.assertEqual(2, queue["in_progress"])
            self.assertEqual(1, queue["failed"])
            self.assertEqual(1, queue["completed"])
            self.assertEqual(4, queue["pending_count"])
            self.assertEqual(RESUME_COMMAND, queue["resume_command"])
            self.assertEqual(900, queue["budget_seconds"])

            latest_item = queue["latest_in_progress"]
            self.assertIsNotNone(latest_item)
            assert latest_item is not None
            self.assertIn("长视频丁", latest_item["path"])
            self.assertEqual("720/1800秒", latest_item["coverage"])
            self.assertEqual("第7块 faster-whisper 超时，下次续跑", latest_item["note"])

            self.assertEqual(1, queue["failed_count"])
            self.assertEqual(1, len(queue["failed_items"]))
            self.assertIn("长视频己", queue["failed_items"][0]["path"])
            self.assertEqual("媒体缓存丢失，重取失败", queue["failed_items"][0]["note"])

    def test_notes_without_transcript_status_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = Path(temp_dir) / "00_收件箱"
            inbox.mkdir()
            write_inbox_note(inbox, "2026-11-18 小红书 - 图文", {"处理状态": "已分拣", "解析状态": "已解析"})
            write_inbox_note(inbox, "2026-11-18 前沿情报 - 例行", {})

            queue = self.backend.inspect_transcript_queue(inbox)

            self.assertEqual(0, queue["total"])
            self.assertEqual(0, queue["pending_count"])
            self.assertEqual(0, queue["failed"])
            self.assertIsNone(queue["latest_in_progress"])
            self.assertEqual([], queue["failed_items"])


class TranscriptQueueDashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = load_module(BACKEND, "test_transcript_queue_dashboard_backend")

    def test_dashboard_suggests_resume_command_and_lists_risks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "00_收件箱"
            inbox.mkdir(parents=True)
            write_inbox_note(inbox, "2026-11-18 抖音 - 待转写长视频", {"转写状态": "未开始"})
            write_inbox_note(
                inbox,
                "2026-11-18 抖音 - 转写中长视频",
                {"转写状态": "进行中", "转写覆盖": "720/1800秒", "转写备注": "第7块 faster-whisper 超时，下次续跑"},
            )
            write_inbox_note(inbox, "2026-11-18 抖音 - 转写失败长视频", {"转写状态": "失败", "转写备注": "媒体缓存丢失，重取失败"})

            data = self.backend.collect(make_collect_args(root))
            dashboard = self.backend.build_dashboard(data)

            self.assertEqual(2, data["transcript_queue"]["pending_count"])
            self.assertIn(SUGGESTION_TEXT, dashboard)
            self.assertIn("分段转写队列待续跑 2 条（未开始 1，进行中 1）", dashboard)
            self.assertIn("- 分段转写队列：待续跑 2 条（未开始 1 / 进行中 1），已完成 0 条，失败 1 条", dashboard)
            self.assertIn("覆盖 720/1800秒", dashboard)
            self.assertIn("第7块 faster-whisper 超时，下次续跑", dashboard)
            self.assertIn("分段转写失败 1 条，需人工核验", dashboard)
            self.assertEqual("黄色", data["digest"]["level"])
            self.assertTrue(any("segmented_transcribe.py" in item for item in data["digest"]["codex_items"]))

            report = self.backend.build_report(data)
            self.assertIn(SUGGESTION_TEXT, report)
            self.assertIn("转写状态：{\"未开始\": 1, \"进行中\": 1, \"失败\": 1}", report)
            self.assertIn("媒体缓存丢失，重取失败", report)

    def test_dashboard_has_no_resume_suggestion_when_queue_settled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "00_收件箱"
            inbox.mkdir(parents=True)
            write_inbox_note(inbox, "2026-11-18 抖音 - 已完成长视频", {"转写状态": "已完成", "转写覆盖": "1800/1800秒"})
            write_inbox_note(inbox, "2026-11-18 抖音 - 普通笔记", {"处理状态": "已分拣"})

            data = self.backend.collect(make_collect_args(root))
            dashboard = self.backend.build_dashboard(data)

            self.assertEqual(0, data["transcript_queue"]["pending_count"])
            self.assertEqual(1, data["transcript_queue"]["completed"])
            self.assertNotIn("segmented_transcribe", dashboard)
            self.assertNotIn("分段转写失败", dashboard)
            self.assertNotIn("运行分段转写续跑", dashboard)
            self.assertIn("- 分段转写队列：待续跑 0 条（未开始 0 / 进行中 0），已完成 1 条，失败 0 条", dashboard)

    def test_failed_only_queue_goes_to_risk_without_resume_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "00_收件箱"
            inbox.mkdir(parents=True)
            write_inbox_note(inbox, "2026-11-18 抖音 - 失败长视频", {"转写状态": "失败", "转写备注": "连续 2 次零进展"})

            data = self.backend.collect(make_collect_args(root))
            dashboard = self.backend.build_dashboard(data)

            self.assertEqual(0, data["transcript_queue"]["pending_count"])
            self.assertNotIn("运行分段转写续跑", dashboard)
            self.assertIn("分段转写失败 1 条，需人工核验", dashboard)
            self.assertTrue(any("分段转写失败 1 条" in item for item in data["digest"]["risks"]))
            self.assertEqual("黄色", data["digest"]["level"])


class TranscriptQueueNoExecutionTest(unittest.TestCase):
    def test_health_check_never_executes_segmented_transcribe(self) -> None:
        backend = load_module(BACKEND, "test_transcript_no_exec_backend")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "00_收件箱"
            inbox.mkdir(parents=True)
            note = write_inbox_note(
                inbox,
                "2026-11-18 抖音 - 待续跑长视频",
                {"转写状态": "进行中", "转写覆盖": "720/1800秒", "转写备注": "等待续跑"},
            )
            original = note.read_text(encoding="utf-8")
            commands: list[list[str]] = []

            def fake_run(command, *args, **kwargs):
                commands.append([str(part) for part in command])
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run):
                data = backend.collect(make_collect_args(root))
                dashboard = backend.build_dashboard(data)
                report = backend.build_report(data)
                backend.build_openclaw_brief(data)
                backend.build_dashboard_tables(data)

            self.assertEqual(1, data["transcript_queue"]["pending_count"])
            self.assertIn(SUGGESTION_TEXT, dashboard)
            self.assertIn(SUGGESTION_TEXT, report)
            for command in commands:
                self.assertNotIn("segmented_transcribe.py", " ".join(command))
            self.assertEqual(original, note.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
