from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / ".codex/skills/backend-control/scripts/backend_health_check.py"
FRONTIER = ROOT / ".codex/skills/frontier-watch/scripts/frontier_watch.py"
FRONTIER_FIXTURE = ROOT / "tests/fixtures/redacted_frontier_report.md"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TodayActionAndBacklogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = load_module(BACKEND, "test_backend_health")
        cls.frontier = load_module(FRONTIER, "test_frontier_watch")

    def test_today_action_uses_one_action_type_dedupes_and_caps_three(self) -> None:
        data = {
            "feedback": {"pending": [], "pending_count": 0},
            "confirmations": {
                "pending_count": 5,
                "pending": [
                    {"title": "A", "source": "source-a", "candidate": "candidate-a", "priority": "高"},
                    {"title": "A duplicate", "source": "source-a", "candidate": "candidate-a2", "priority": "高"},
                    {"title": "B", "source": "source-b", "candidate": "candidate-b", "priority": "中"},
                    {"title": "C", "source": "source-c", "candidate": "candidate-c", "priority": "中"},
                    {"title": "D", "source": "source-d", "candidate": "candidate-d", "priority": "低"},
                ],
            },
            "inbox": {
                "low_quality": [{"title": "A repair", "path": "source-a"}],
                "unread": [{"title": "Unread", "path": "source-e", "is_auto": False}],
            },
            "push_state": {"waiting": []},
        }

        payload = self.backend.build_today_action(data, limit=3)

        self.assertEqual("确认", payload["action_type"])
        self.assertEqual(3, len(payload["items"]))
        self.assertEqual(3, len({item["source"] for item in payload["items"]}))
        self.assertTrue(all(item["action"] == "确认" for item in payload["items"]))

    def test_today_action_fixed_views_are_content_idempotent(self) -> None:
        payload = {"version": 1, "action_type": "暂无动作", "items": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            json_path = root / "today.json"
            markdown_path = root / "today.md"

            first = self.backend.write_today_action_views(payload, json_path=json_path, markdown_path=markdown_path)
            json_bytes = json_path.read_bytes()
            markdown_bytes = markdown_path.read_bytes()
            second = self.backend.write_today_action_views(payload, json_path=json_path, markdown_path=markdown_path)

            self.assertEqual(2, first)
            self.assertEqual(0, second)
            self.assertEqual(json_bytes, json_path.read_bytes())
            self.assertEqual(markdown_bytes, markdown_path.read_bytes())

    def test_backend_write_only_refreshes_two_noop_safe_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            today_json = root / "dashboard/today.json"
            today_markdown = root / "dashboard/today.md"
            command = [
                sys.executable,
                str(BACKEND),
                "--inbox",
                str(root / "inbox"),
                "--flow-dir",
                str(root / "flow"),
                "--run-dir",
                str(root / "run"),
                "--automations-dir",
                str(root / "automations"),
                "--push-state",
                str(root / "state.json"),
                "--feedback-queue",
                str(root / "feedback.jsonl"),
                "--confirmation-queue",
                str(root / "confirmations.jsonl"),
                "--today-action-json",
                str(today_json),
                "--today-action-markdown",
                str(today_markdown),
                "--write",
            ]

            first = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            first_json = today_json.read_bytes()
            first_markdown = today_markdown.read_bytes()
            self.assertEqual(
                {"dashboard/today.json", "dashboard/today.md"},
                {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()},
            )

            second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(first_json, today_json.read_bytes())
            self.assertEqual(first_markdown, today_markdown.read_bytes())

    def test_dashboard_export_requires_diagnostics_write_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(BACKEND),
                    "--inbox",
                    str(root / "inbox"),
                    "--flow-dir",
                    str(root / "flow"),
                    "--run-dir",
                    str(root / "run"),
                    "--automations-dir",
                    str(root / "automations"),
                    "--push-state",
                    str(root / "state.json"),
                    "--feedback-queue",
                    str(root / "feedback.jsonl"),
                    "--confirmation-queue",
                    str(root / "confirmations.jsonl"),
                    "--dashboard-data-file",
                    str(root / "dashboard/data.json"),
                    "--dashboard-csv-dir",
                    str(root / "dashboard/csv"),
                    "--export-dashboard-data",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("requires --write-diagnostics", completed.stderr)
            self.assertFalse(any(path.is_file() for path in root.rglob("*")))

    def test_legacy_delivery_is_not_counted_as_sent_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            items = {
                f"legacy-{index}": {
                    "last_pushed_at": "2026-06-01T08:00:00+08:00",
                    "feedback_status": "待反馈",
                }
                for index in range(90)
            }
            items["sent-with-evidence"] = {
                "delivery_stage": "sent",
                "sent_at": "2026-07-01T08:00:00+08:00",
                "title": "Sent item",
            }
            state_path.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False), encoding="utf-8")

            inspected = self.backend.inspect_push_state(state_path)

            self.assertEqual(90, inspected["legacy_unknown"])
            self.assertEqual(1, inspected["waiting_feedback"])
            self.assertEqual(1, len(inspected["waiting"]))

    def test_frontier_inbox_is_blocked_by_inventory_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            run_dir = root / "run"
            inbox.mkdir()
            run_dir.mkdir()
            for index in range(11):
                (inbox / f"unread-{index}.md").write_text(
                    "---\n阅读状态: 未读\n处理状态: 待分拣\n---\n\n# Redacted\n",
                    encoding="utf-8",
                )
            (run_dir / "前台推送状态.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": {
                            f"source-{index}": {"delivery_stage": "sent", "sent_at": "2026-07-01T08:00:00+08:00"}
                            for index in range(4)
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "待确认事项.jsonl").write_text(
                "".join(
                    json.dumps({"标题": f"Confirm {index}", "处理状态": "待确认"}, ensure_ascii=False) + "\n"
                    for index in range(6)
                ),
                encoding="utf-8",
            )

            gate = self.frontier.inventory_gate(
                inbox,
                run_dir / "前台推送状态.json",
                run_dir / "待确认事项.jsonl",
            )
            self.assertTrue(gate["blocked"])
            self.assertEqual(11, gate["unread"])
            self.assertEqual(4, gate["sent_waiting_feedback"])
            self.assertEqual(6, gate["pending_confirmations"])

            completed = subprocess.run(
                [
                    sys.executable,
                    str(FRONTIER),
                    "--from-report",
                    str(FRONTIER_FIXTURE),
                    "--inbox",
                    str(inbox),
                    "--run-dir",
                    str(run_dir),
                    "--write-inbox",
                    "--no-translate-summaries",
                    "--dry-run",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn("库存门禁", completed.stdout)
            self.assertEqual(11, len(list(inbox.glob("*.md"))))


if __name__ == "__main__":
    unittest.main()
