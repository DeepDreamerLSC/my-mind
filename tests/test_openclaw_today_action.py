from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / ".codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py"
BACKEND = ROOT / ".codex/skills/backend-control/scripts/backend_health_check.py"
CONSUME = ROOT / ".codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py"
PENDING = ROOT / ".codex/skills/pending-distill/scripts/consume_pending_distill.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_prepare(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(PREPARE), *args], cwd=ROOT, text=True, capture_output=True, check=False)


class OpenClawTodayActionTest(unittest.TestCase):
    def test_today_action_message_uses_only_canonical_items_and_deduplicates_after_ack(self) -> None:
        payload = {
            "version": 1,
            "action_type": "确认",
            "items": [
                {
                    "source": "00_收件箱/fresh.md",
                    "title": "Fresh confirmation",
                    "action": "确认",
                    "reason": "fixture",
                    "target": "75_提示词库/fresh.md",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            action_path = root / "today.json"
            state_path = root / "today-state.json"
            action_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            first = run_prepare("--today-action", str(action_path), "--today-action-state", str(state_path))
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertIn("Fresh confirmation", first.stdout)
            self.assertNotIn("OpenAI团队成员分享", first.stdout)
            self.assertNotIn("精选内容", first.stdout)

            acknowledged = run_prepare(
                "--today-action",
                str(action_path),
                "--today-action-state",
                str(state_path),
                "--ack-today-action-sent",
                "--channel",
                "fixture",
                "--message-id",
                "fixture-message-1",
                "--sent-at",
                "2026-07-14T07:00:00+08:00",
            )
            self.assertEqual(0, acknowledged.returncode, acknowledged.stderr)

            duplicate = run_prepare("--today-action", str(action_path), "--today-action-state", str(state_path))
            self.assertEqual(0, duplicate.returncode, duplicate.stderr)
            self.assertEqual("", duplicate.stdout)

            payload["items"][0]["title"] = "Changed confirmation"
            action_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            changed = run_prepare("--today-action", str(action_path), "--today-action-state", str(state_path))
            self.assertEqual(0, changed.returncode, changed.stderr)
            self.assertIn("Changed confirmation", changed.stdout)


class ConfirmationVerificationRoutingTest(unittest.TestCase):
    def test_gate_failure_moves_item_out_of_user_confirmation(self) -> None:
        consumer = load_module(CONSUME, "test_consumer_verification_routing")
        backend = load_module(BACKEND, "test_backend_verification_routing")
        pending = load_module(PENDING, "test_pending_verification_routing")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate = root / "candidate.md"
            source = root / "source.md"
            queue = root / "confirmation.jsonl"
            candidate.write_text(
                "---\n吸收状态: 待确认\n可信状态: 待核验\n来源文件: source.md\n---\n\n# Candidate\n\n待补充。\n",
                encoding="utf-8",
            )
            source.write_text("---\n处理状态: 已分拣\n---\n\n# Source\n", encoding="utf-8")
            queue.write_text(
                json.dumps(
                    {
                        "序号": 1,
                        "标题": "Blocked candidate",
                        "来源文件": str(source),
                        "候选文件": str(candidate),
                        "处理状态": "待确认",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            consumer.DEFAULT_CONFIRM_QUEUE = queue
            record = {
                "目标序号": "1",
                "目标标题": "Blocked candidate",
                "来源文件": str(source),
                "候选文件": str(candidate),
                "动作": "确认转正",
                "原始回复": "1 确认转正",
            }

            processed, result = consumer.process_confirmation_record(record, write=True, sync_feishu=False)

            self.assertEqual("需继续核验", processed["处理状态"])
            self.assertTrue(result.startswith("待核验"))
            self.assertIn("吸收状态: 待核验", candidate.read_text(encoding="utf-8"))
            self.assertTrue(pending.candidate_needs_verification([str(candidate)]))
            rebuilt = pending.build_confirm_item_from_candidate(
                pending.PendingItem(
                    note=SimpleNamespace(path=source, title="Blocked candidate"),
                    result=SimpleNamespace(priority="中"),
                    phase="已有候选待确认",
                    candidate_paths=[str(candidate)],
                    strategy="fixture",
                ),
                SimpleNamespace(
                    status="已有候选待确认",
                    reason="来源已经有候选回链，避免重复生成。",
                    title="Blocked candidate",
                    target=None,
                    kind="prompt",
                    questions=[],
                ),
            )
            self.assertEqual("需继续核验", rebuilt.status)

            legacy_record = json.loads(queue.read_text(encoding="utf-8"))
            legacy_record["处理状态"] = "待确认"
            queue.write_text(json.dumps(legacy_record, ensure_ascii=False) + "\n", encoding="utf-8")
            inspection = backend.inspect_confirmations(queue)
            self.assertEqual(0, inspection["pending_count"])
            self.assertEqual(1, inspection["verification_count"])
            self.assertEqual(1, inspection["promotion_gate_count"])
            action = backend.build_today_action(
                {
                    "push_state": {"waiting": [], "waiting_feedback": 0},
                    "confirmations": inspection,
                    "inbox": {"unread": [], "unread_count": 0, "low_quality": []},
                }
            )
            self.assertEqual("暂无动作", action["action_type"])
            self.assertEqual([], action["items"])

            confirmation = pending.ConfirmItem(
                source=source,
                title="Blocked candidate",
                status="需继续核验",
                reason="fixture",
                priority="中",
                candidate_path=candidate,
            )
            regenerated = json.loads(pending.confirm_json_records([confirmation]))
            self.assertEqual("需继续核验", regenerated["处理状态"])


if __name__ == "__main__":
    unittest.main()
