#!/usr/bin/env python3
"""Publish and send the fixed my-mind extra-reading pack with Feishu links."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
LIB_DIR = ROOT / ".codex/lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import frontdesk_delivery as delivery


PREPARE_SCRIPT = ROOT / ".codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py"
DEFAULT_PACK = ROOT / "85_运行记录/后台总览/今日未读清仓包.md"
DEFAULT_PUSH_STATE = ROOT / "85_运行记录/前台推送状态.json"
DEFAULT_ARCHIVE_DIR = ROOT / "85_运行记录/后台总览/未读清仓历史"
DEFAULT_SENT_RECORD = ROOT / "85_运行记录/后台总览/今日未读清仓包最近发送.json"
COMPLETED_STAGES = {"sent", "feedback_received", "applied"}
TZ = ZoneInfo("Asia/Shanghai")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def decode_json_output(text: str) -> object | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for offset, character in enumerate(text):
            if character not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(text[offset:])
            except json.JSONDecodeError:
                continue
            return value
    return None


def extract_message_id(payload: object) -> str:
    """Find a provider-issued message ID without treating arbitrary IDs as receipts."""
    if isinstance(payload, dict):
        for key in ("messageId", "message_id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        for key in ("result", "message", "data", "payload"):
            message_id = extract_message_id(payload.get(key))
            if message_id:
                return message_id
    if isinstance(payload, list):
        for item in payload:
            message_id = extract_message_id(item)
            if message_id:
                return message_id
    return ""


def sent_receipt(pack_path: Path, state_path: Path) -> dict[str, object] | None:
    if not state_path.exists():
        return None
    sources = delivery.source_files_from_push(pack_path)
    if not sources:
        return None
    state = delivery.load_state(state_path)
    items = state.get("items", {})
    if not isinstance(items, dict):
        return None
    entries = [items.get(source) for source in sources]
    if not all(isinstance(entry, dict) and str(entry.get("delivery_stage") or "") in COMPLETED_STAGES for entry in entries):
        return None
    first = entries[0]
    assert isinstance(first, dict)
    sent_at = str(first.get("sent_at") or "")
    message_id = str(first.get("message_id") or first.get("send_idempotency_key") or "")
    if not sent_at or not message_id:
        return None
    return {"sent_at": sent_at, "message_id": message_id, "source_files": sources}


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def archive_sent_pack(
    pack_path: Path,
    state_path: Path,
    *,
    archive_dir: Path = DEFAULT_ARCHIVE_DIR,
    sent_record_path: Path = DEFAULT_SENT_RECORD,
) -> Path | None:
    """Freeze a delivered pack so delayed replies keep their original indexes."""
    receipt = sent_receipt(pack_path, state_path)
    if not receipt or not pack_path.exists():
        return None
    content = pack_path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        sent_time = dt.datetime.fromisoformat(str(receipt["sent_at"]))
        stamp = sent_time.strftime("%Y-%m-%d-%H%M%S")
    except ValueError:
        stamp = "unknown-time"
    archive_path = archive_dir / f"未读清仓包-{stamp}-{content_hash[:12]}.md"
    if not archive_path.exists():
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(content, encoding="utf-8")
    write_json(
        sent_record_path,
        {
            "version": 1,
            "archive_file": repo_relative(archive_path),
            "content_hash": content_hash,
            "message_id": receipt["message_id"],
            "sent_at": receipt["sent_at"],
            "source_files": receipt["source_files"],
        },
    )
    return archive_path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def print_failure(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout.strip():
        print(completed.stdout.strip(), file=sys.stderr)
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish and send the fixed my-mind extra-reading pack through OpenClaw.")
    parser.add_argument("--pack", default=str(DEFAULT_PACK), help="Fixed backlog Markdown pack.")
    parser.add_argument("--push-state", default=str(DEFAULT_PUSH_STATE), help="Frontdesk delivery state JSON.")
    parser.add_argument("--channel", default="feishu", help="OpenClaw channel and delivery receipt channel.")
    parser.add_argument("--target", required=True, help="OpenClaw recipient, for example user:ou_xxx.")
    parser.add_argument("--message-max-items", type=int, default=3, help="Maximum linked item highlights in the OpenClaw message.")
    parser.add_argument("--summary-chars", type=int, default=90, help="Maximum summary characters per linked item highlight.")
    parser.add_argument("--publish-max-items", type=int, default=10, help="Maximum items to publish from the fixed pack.")
    parser.add_argument("--bundle-title", default="", help="Optional Feishu index title. Defaults to today's my-mind extra-reading title.")
    parser.add_argument("--include-incomplete", action="store_true", help="Publish incomplete source notes as clearly labelled reading pages.")
    parser.add_argument("--dry-run", action="store_true", help="Render and invoke OpenClaw dry-run only; do not mark or acknowledge delivery.")
    return parser.parse_args()


def bundle_title(value: str) -> str:
    return value.strip() or f"my-mind 额外待读 {dt.datetime.now(TZ).strftime('%Y-%m-%d')}"


def main() -> int:
    args = parse_args()
    pack_path = resolve_path(args.pack)
    state_path = resolve_path(args.push_state)
    existing_receipt = sent_receipt(pack_path, state_path)
    if existing_receipt and not args.dry_run:
        archive_path = archive_sent_pack(pack_path, state_path)
        if archive_path:
            print(f"额外待读包已有发送回执，未重复发送；反馈快照：{repo_relative(archive_path)}")
        else:
            print("额外待读包已有发送回执，未重复发送")
        return 0
    prepare_command = [
        sys.executable,
        str(PREPARE_SCRIPT),
        "--push-file",
        str(pack_path),
        "--push-state",
        str(state_path),
        "--message-max-items",
        str(max(0, args.message_max_items)),
        "--summary-chars",
        str(max(0, args.summary_chars)),
        "--publish-max-items",
        str(max(0, args.publish_max_items)),
        "--publish-title",
        bundle_title(args.bundle_title),
    ]
    if args.include_incomplete:
        prepare_command.append("--include-incomplete")
    if args.dry_run:
        prepare_command.append("--dry-run")
    prepared = run(prepare_command)
    if prepared.returncode != 0:
        print_failure(prepared)
        return prepared.returncode
    message = prepared.stdout.strip()
    if not message:
        archive_path = None if args.dry_run else archive_sent_pack(pack_path, state_path)
        if archive_path:
            print(f"额外待读包已发送，反馈快照：{repo_relative(archive_path)}")
        else:
            print("额外待读包为空或已发送，未发送消息")
        return 0

    send_command = [
        "openclaw",
        "message",
        "send",
        "--channel",
        args.channel,
        "--target",
        args.target,
        "--message",
        message,
        "--json",
    ]
    if args.dry_run:
        send_command.append("--dry-run")
    sent = run(send_command)
    if sent.returncode != 0:
        print_failure(sent)
        return sent.returncode
    if args.dry_run:
        print(sent.stdout.strip())
        return 0

    message_id = extract_message_id(decode_json_output(sent.stdout))
    if not message_id:
        print("OpenClaw 发送结果缺少外部 messageId，未记录回执。", file=sys.stderr)
        print_failure(sent)
        return 2

    acknowledged = run(
        [
            sys.executable,
            str(PREPARE_SCRIPT),
            "--ack-sent",
            "--push-file",
            str(pack_path),
            "--push-state",
            str(state_path),
            "--channel",
            args.channel,
            "--message-id",
            message_id,
        ]
    )
    if acknowledged.returncode != 0:
        print_failure(acknowledged)
        return acknowledged.returncode
    archive_path = archive_sent_pack(pack_path, state_path)
    if archive_path:
        print(f"额外待读包已发送并记录回执：{message_id}；反馈快照：{repo_relative(archive_path)}")
    else:
        print(f"额外待读包已发送并记录回执：{message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
