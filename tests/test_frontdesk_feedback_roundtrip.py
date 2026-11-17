from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATE = ROOT / ".codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py"
APPEND = ROOT / ".codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py"
CONSUME = ROOT / ".codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py"
DELIVERY_LIB = ROOT / ".codex/lib/frontdesk_delivery.py"
BACKEND = ROOT / ".codex/skills/backend-control/scripts/backend_health_check.py"


def load_delivery():
    spec = importlib.util.spec_from_file_location("roundtrip_delivery", DELIVERY_LIB)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {DELIVERY_LIB}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_backend():
    spec = importlib.util.spec_from_file_location("roundtrip_backend", BACKEND)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {BACKEND}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def snapshot(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class FrontdeskFeedbackRoundtripTest(unittest.TestCase):
    def test_today_action_reply_resolves_source_and_candidate(self) -> None:
        backend = load_backend()
        payload = {
            "version": 1,
            "action_type": "确认",
            "items": [
                {
                    "source": "00_收件箱/redacted-source.md",
                    "title": "Redacted confirmation",
                    "action": "确认",
                    "reason": "fixture",
                    "target": "75_提示词库/redacted-candidate.md",
                }
            ],
            "inventory": {"unread": 0, "sent_waiting_feedback": 0, "pending_confirmations": 1},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            today_path = run_dir / "后台总览/今日动作.md"
            today_path.parent.mkdir(parents=True)
            today_path.write_text(backend.render_today_action(payload), encoding="utf-8")
            completed = run_command(
                str(APPEND),
                "1 确认转正",
                "--run-dir",
                str(run_dir),
                "--dry-run",
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            record = json.loads(completed.stdout)
            self.assertEqual("Redacted confirmation", record["目标标题"])
            self.assertEqual("00_收件箱/redacted-source.md", record["来源文件"])
            self.assertEqual("75_提示词库/redacted-candidate.md", record["候选文件"])

    def test_reading_reply_prefers_fixed_daily_backlog_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            dashboard = run_dir / "后台总览"
            dashboard.mkdir(parents=True)
            (dashboard / "今日动作.md").write_text(
                "# 今日动作\n\n### 1. Confirmation\n\n- 来源文件：`00_收件箱/confirmation.md`\n",
                encoding="utf-8",
            )
            (dashboard / "今日未读清仓包.md").write_text(
                "# my-mind 前台推送\n\n### 1. Backlog item\n\n- 来源文件：`00_收件箱/backlog.md`\n",
                encoding="utf-8",
            )

            completed = run_command(str(APPEND), "1 已读", "--run-dir", str(run_dir), "--dry-run")

            self.assertEqual(0, completed.returncode, completed.stderr)
            record = json.loads(completed.stdout)
            self.assertEqual("00_收件箱/backlog.md", record["来源文件"])
            self.assertTrue(record["推送文件"].endswith("后台总览/今日未读清仓包.md"))

    def test_reading_reply_prefers_sent_backlog_snapshot_over_replaced_fixed_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            dashboard = run_dir / "后台总览"
            archive = dashboard / "未读清仓历史/未读清仓包-2026-07-16.md"
            archive.parent.mkdir(parents=True)
            archive.write_text(
                "# my-mind 前台推送\n\n### 1. Sent backlog item\n\n- 来源文件：`00_收件箱/sent-backlog.md`\n",
                encoding="utf-8",
            )
            (dashboard / "今日未读清仓包.md").write_text(
                "# my-mind 前台推送\n\n### 1. Replacement item\n\n- 来源文件：`00_收件箱/replacement.md`\n",
                encoding="utf-8",
            )
            (dashboard / "今日未读清仓包最近发送.json").write_text(
                json.dumps({"archive_file": str(archive)}, ensure_ascii=False),
                encoding="utf-8",
            )

            completed = run_command(str(APPEND), "1 已读", "--run-dir", str(run_dir), "--dry-run")

            self.assertEqual(0, completed.returncode, completed.stderr)
            record = json.loads(completed.stdout)
            self.assertEqual("00_收件箱/sent-backlog.md", record["来源文件"])
            self.assertTrue(record["推送文件"].endswith("未读清仓历史/未读清仓包-2026-07-16.md"))

    def test_backlog_pack_requeues_stalled_published_items_with_a_cap(self) -> None:
        delivery = load_delivery()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            flow = root / "flow"
            run_dir = root / "run"
            project = root / "project"
            state_path = run_dir / "state.json"
            pack_path = run_dir / "后台总览/今日未读清仓包.md"
            inbox.mkdir()
            flow.mkdir()
            project.mkdir()
            sources: list[str] = []
            for index in range(3):
                source_path = inbox / f"backlog-{index}.md"
                source_path.write_text(
                    f"---\n标题: Backlog {index}\n解析状态: 已解析\n内容质量: 可推送\n阅读状态: 未读\n处理状态: 已分拣\n---\n\n# Backlog {index}\n\nFixture content.\n",
                    encoding="utf-8",
                )
                sources.append(source_path.as_posix())
            state = delivery.empty_state()
            old_at = "2026-07-01T08:00:00+08:00"
            delivery.mark_prepared(state, [{"source_file": source} for source in sources], push_file="old.md", at=old_at)
            delivery.mark_published(state, sources, push_file="old.md", at=old_at)
            delivery.save_state(state_path, state)

            completed = run_command(
                str(GENERATE),
                "--inbox",
                str(inbox),
                "--flow-dir",
                str(flow),
                "--run-dir",
                str(run_dir),
                "--project-dir",
                str(project),
                "--state-file",
                str(state_path),
                "--ignore-flow",
                "--limit",
                "2",
                "--output-file",
                str(pack_path),
                "--requeue-published-after-hours",
                "24",
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(pack_path, Path(completed.stdout.strip()))
            cards = [line for line in pack_path.read_text(encoding="utf-8").splitlines() if line.startswith("### ")]
            self.assertEqual(2, len(cards))
            final_state = delivery.load_state(state_path)
            self.assertTrue(all(final_state["items"][source]["delivery_stage"] == "prepared" for source in sources))

    def test_corrupt_state_prevents_partial_feedback_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "run"
            run_dir.mkdir()
            source_path = root / "source.md"
            source_path.write_text("---\n处理状态: 已分拣\n---\n\n# Redacted\n", encoding="utf-8")
            today_path = root / "today.md"
            today_path.write_text(
                f"# 今日动作\n\n### 1. Redacted\n\n- 来源文件：`{source_path.as_posix()}`\n",
                encoding="utf-8",
            )
            state_path = run_dir / "state.json"
            state_path.write_bytes(b"{not-json")
            queue_path = run_dir / "feedback.jsonl"

            appended = run_command(
                str(APPEND),
                "1 已读：redacted",
                "--run-dir",
                str(run_dir),
                "--queue",
                str(queue_path),
                "--push-file",
                str(today_path),
                "--push-state",
                str(state_path),
            )
            self.assertNotEqual(0, appended.returncode)
            self.assertFalse(queue_path.exists())
            self.assertEqual(b"{not-json", state_path.read_bytes())

            queue_path.write_text(
                json.dumps(
                    {
                        "时间": "2026-07-01 08:00:00 +0800",
                        "来源文件": source_path.as_posix(),
                        "动作": "已读",
                        "内容": "redacted",
                        "处理状态": "待处理",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            before = snapshot(root)
            consumed = run_command(
                str(CONSUME),
                "--queue",
                str(queue_path),
                "--run-dir",
                str(run_dir),
                "--push-state",
                str(state_path),
                "--write",
                "--no-distill",
            )
            self.assertNotEqual(0, consumed.returncode)
            self.assertEqual(before, snapshot(root))

    def test_force_does_not_reselect_sent_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            source_path = inbox / "redacted-sent.md"
            source_path.write_text(
                "---\n标题: Redacted sent\n解析状态: 已解析\n内容质量: 可推送\n阅读状态: 未读\n处理状态: 已分拣\n---\n\n# Redacted\n",
                encoding="utf-8",
            )
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": {
                            source_path.as_posix(): {
                                "delivery_stage": "sent",
                                "sent_at": "2026-07-01T08:00:00+08:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            completed = run_command(
                str(GENERATE),
                "--inbox",
                str(inbox),
                "--flow-dir",
                str(root / "flow"),
                "--run-dir",
                str(root / "run"),
                "--project-dir",
                str(root / "project"),
                "--state-file",
                str(state_path),
                "--ignore-flow",
                "--force",
                "--dry-run",
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn("候选条目：0", completed.stdout)

    def test_isolated_command_roundtrip(self) -> None:
        delivery = load_delivery()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            flow = root / "flow"
            run_dir = root / "run"
            project = root / "project"
            state_path = run_dir / "frontdesk-state.json"
            queue_path = run_dir / "feedback.jsonl"
            inbox.mkdir()
            flow.mkdir()
            project.mkdir()
            source_path = inbox / "redacted-note.md"
            source_path.write_text(
                """---
标题: Redacted note
来源平台: fixture
作者或频道: fixture
解析状态: 已解析
内容质量: 可推送
阅读状态: 未读
处理状态: 已分拣
---

# Redacted note

## 原文摘录

This is isolated fixture content.
""",
                encoding="utf-8",
            )

            generated = run_command(
                str(GENERATE),
                "--inbox",
                str(inbox),
                "--flow-dir",
                str(flow),
                "--run-dir",
                str(run_dir),
                "--project-dir",
                str(project),
                "--state-file",
                str(state_path),
                "--ignore-flow",
                "--cooldown-hours",
                "0",
                "--limit",
                "1",
            )
            self.assertEqual(0, generated.returncode, generated.stderr)
            push_path = Path(generated.stdout.strip())
            state = delivery.load_state(state_path)
            source = source_path.as_posix()
            self.assertEqual("prepared", state["items"][source]["delivery_stage"])
            self.assertNotIn("sent_at", state["items"][source])

            at = "2026-07-01T08:00:00+08:00"
            delivery.mark_published(state, [source], push_file=push_path.as_posix(), at=at)
            delivery.ack_sent(
                state,
                [source],
                channel="redacted-channel",
                at=at,
                message_id="msg_redacted_roundtrip",
                idempotency_key="send_redacted_roundtrip",
            )
            delivery.save_state(state_path, state)

            before_feedback = snapshot(root)
            appended = run_command(
                str(APPEND),
                "1 已读：redacted feedback",
                "--run-dir",
                str(run_dir),
                "--queue",
                str(queue_path),
                "--push-file",
                str(push_path),
                "--push-state",
                str(state_path),
                "--channel",
                "redacted-channel",
            )
            self.assertEqual(0, appended.returncode, appended.stderr)
            received_state = delivery.load_state(state_path)
            self.assertEqual("feedback_received", received_state["items"][source]["delivery_stage"])
            after_feedback = snapshot(root)
            feedback_changes = {key for key in set(before_feedback) | set(after_feedback) if before_feedback.get(key) != after_feedback.get(key)}
            self.assertLessEqual(len(feedback_changes), 2)

            before_consume = snapshot(root)
            consumed = run_command(
                str(CONSUME),
                "--queue",
                str(queue_path),
                "--run-dir",
                str(run_dir),
                "--push-state",
                str(state_path),
                "--write",
                "--no-distill",
            )
            self.assertEqual(0, consumed.returncode, consumed.stderr)
            final_state = delivery.load_state(state_path)
            self.assertEqual("applied", final_state["items"][source]["delivery_stage"])
            self.assertEqual("msg_redacted_roundtrip", final_state["items"][source]["message_id"])
            queue_record = json.loads(queue_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual("已处理", queue_record["处理状态"])
            after_consume = snapshot(root)
            consume_changes = {key for key in set(before_consume) | set(after_consume) if before_consume.get(key) != after_consume.get(key)}
            self.assertLessEqual(len(consume_changes), 5)


if __name__ == "__main__":
    unittest.main()
