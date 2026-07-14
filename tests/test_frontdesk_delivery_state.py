from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUSH_SCRIPT = ROOT / ".codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py"
DELIVERY_LIB = ROOT / ".codex/lib/frontdesk_delivery.py"
ACK_SCRIPT = ROOT / ".codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py"
PUSH_FIXTURE = ROOT / "tests/fixtures/redacted_frontdesk_push.md"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FrontdeskDeliveryStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.push = load_module(PUSH_SCRIPT, "test_generate_frontdesk_push")

    def make_note(self, path: Path):
        return self.push.InboxNote(
            path=path,
            item_id=path.as_posix(),
            content_hash="redacted-content-hash",
            title="Redacted note",
            platform="fixture",
            author="fixture",
            source_url="https://example.invalid/source",
            original_url="",
            transcript_url="",
            process_status="已分拣",
            reading_status="未读",
            parse_status="已解析",
            content_quality="可推送",
            quality_gate="fixture",
            summary="Redacted summary",
            reading_excerpt="Redacted excerpt",
            quality_note="fixture",
            reading_questions=[],
            distill_direction="待判断",
            score=1,
            flow_rank=1,
            value="fixture",
            action="阅读",
        )

    def test_generating_push_only_marks_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note = self.make_note(root / "redacted.md")
            state = {"version": 1, "items": {}}

            self.push.update_push_state(state, [note], root / "frontdesk-push.md")

            item = state["items"][note.item_id]
            self.assertEqual("prepared", item.get("delivery_stage"))
            self.assertTrue(item.get("prepared_at"))
            self.assertNotIn("sent_at", item)
            self.assertNotEqual("待反馈", item.get("feedback_status"))

    def test_legacy_records_remain_delivery_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": {
                            "00_收件箱/legacy.md": {
                                "title": "Legacy item",
                                "last_pushed_at": "2026-06-01T08:00:00+08:00",
                                "feedback_status": "待反馈",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            state = self.push.load_push_state(state_path)
            item = state["items"]["00_收件箱/legacy.md"]

            self.assertFalse(item.get("delivery_stage"))
            self.assertEqual("unknown", item.get("legacy_delivery"))

    def test_transition_order_and_idempotent_send_ack(self) -> None:
        delivery = load_module(DELIVERY_LIB, "test_frontdesk_delivery")
        source = "00_收件箱/redacted-workflow-note.md"
        state = delivery.empty_state()
        at = "2026-07-01T08:00:00+08:00"

        delivery.mark_prepared(
            state,
            [{"source_file": source, "title": "Redacted", "content_hash": "hash"}],
            push_file="85_运行记录/redacted-push.md",
            at=at,
        )
        with self.assertRaises(ValueError):
            delivery.mark_applied(state, source, at=at, result="invalid jump")
        with self.assertRaises(ValueError):
            delivery.mark_feedback_received(state, source, at=at, action="已读", content="invalid jump")

        delivery.mark_published(state, [source], push_file="85_运行记录/redacted-push.md", at=at)
        with self.assertRaises(ValueError):
            delivery.mark_feedback_received(state, source, at=at, action="已读", content="invalid jump")
        changed = delivery.ack_sent(
            state,
            [source],
            channel="redacted-channel",
            at=at,
            message_id="msg_redacted_001",
            idempotency_key="send_redacted_001",
        )
        snapshot = json.dumps(state, ensure_ascii=False, sort_keys=True)
        repeated = delivery.ack_sent(
            state,
            [source],
            channel="redacted-channel",
            at=at,
            message_id="msg_redacted_001",
            idempotency_key="send_redacted_001",
        )

        self.assertTrue(changed)
        self.assertFalse(repeated)
        self.assertEqual(snapshot, json.dumps(state, ensure_ascii=False, sort_keys=True))
        self.assertEqual(1, delivery.waiting_for_feedback_count(state))
        with self.assertRaises(ValueError):
            delivery.ack_sent(
                state,
                [source],
                channel="redacted-channel",
                at=at,
                message_id="msg_redacted_conflict",
                idempotency_key="send_redacted_conflict",
            )

        delivery.mark_feedback_received(state, source, at=at, action="已读", content="redacted")
        self.assertEqual(0, delivery.waiting_for_feedback_count(state))
        delivery.mark_applied(state, source, at=at, result="feedback applied")
        self.assertEqual("applied", state["items"][source]["delivery_stage"])

    def test_legacy_feedback_preserves_unknown_delivery(self) -> None:
        delivery = load_module(DELIVERY_LIB, "test_frontdesk_delivery_legacy_feedback")
        source = "00_收件箱/legacy.md"
        state = {
            "items": {
                source: {
                    "last_pushed_at": "2026-06-01T08:00:00+08:00",
                    "feedback_status": "待反馈",
                }
            }
        }
        delivery.normalize_state(state)

        delivery.mark_feedback_received(
            state,
            source,
            at="2026-07-01T08:00:00+08:00",
            action="已读",
            content="legacy feedback",
        )

        self.assertFalse(state["items"][source]["delivery_stage"])
        self.assertEqual("unknown", state["items"][source]["legacy_delivery"])
        self.assertIn("legacy_feedback_received_at", state["items"][source])

    def test_send_ack_requires_channel_and_external_evidence(self) -> None:
        delivery = load_module(DELIVERY_LIB, "test_frontdesk_delivery_requirements")
        source = "00_收件箱/redacted-review-note.md"
        state = delivery.empty_state()
        delivery.mark_prepared(state, [{"source_file": source}], push_file="redacted.md", at="2026-07-01T08:00:00+08:00")
        delivery.mark_published(state, [source], push_file="redacted.md", at="2026-07-01T08:00:00+08:00")

        with self.assertRaises(ValueError):
            delivery.ack_sent(state, [source], channel="", at="2026-07-01T08:00:00+08:00", message_id="msg")
        with self.assertRaises(ValueError):
            delivery.ack_sent(state, [source], channel="redacted", at="2026-07-01T08:00:00+08:00")
        with self.assertRaises(ValueError):
            delivery.ack_sent(state, [source], channel="redacted", at="not-a-time", message_id="msg")

    def test_corrupt_or_invalid_state_fails_closed(self) -> None:
        delivery = load_module(DELIVERY_LIB, "test_frontdesk_delivery_invalid")
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            original = b"{not-json"
            state_path.write_bytes(original)
            with self.assertRaises(ValueError):
                delivery.load_state(state_path)
            self.assertEqual(original, state_path.read_bytes())
        with self.assertRaises(ValueError):
            delivery.normalize_state({"items": {"source": {"delivery_stage": "snet"}}})

    def test_explicit_ack_cli_is_local_and_idempotent(self) -> None:
        delivery = load_module(DELIVERY_LIB, "test_frontdesk_delivery_cli")
        sources = delivery.source_files_from_push(PUSH_FIXTURE)
        at = "2026-07-01T08:00:00+08:00"
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = delivery.empty_state()
            delivery.mark_prepared(
                state,
                [{"source_file": source, "title": "Redacted"} for source in sources],
                push_file=PUSH_FIXTURE.as_posix(),
                at=at,
            )
            delivery.mark_published(state, sources, push_file=PUSH_FIXTURE.as_posix(), at=at)
            delivery.save_state(state_path, state)

            command = [
                sys.executable,
                str(ACK_SCRIPT),
                "--ack-sent",
                "--push-file",
                str(PUSH_FIXTURE),
                "--push-state",
                str(state_path),
                "--channel",
                "redacted-channel",
                "--message-id",
                "msg_redacted_cli",
                "--sent-at",
                at,
            ]
            first = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            first_bytes = state_path.read_bytes()
            second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(first_bytes, state_path.read_bytes())
            final = delivery.load_state(state_path)
            self.assertTrue(all(final["items"][source]["delivery_stage"] == "sent" for source in sources))


if __name__ == "__main__":
    unittest.main()
