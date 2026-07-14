#!/usr/bin/env python3
"""Prepare either a Feishu bundle message or the canonical OpenClaw daily action."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
LIB_DIR = ROOT / ".codex/lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import frontdesk_delivery as delivery


TZ = ZoneInfo("Asia/Shanghai")
RUN_DIR = ROOT / "85_运行记录"
PUSH_STATE = RUN_DIR / "前台推送状态.json"
TODAY_ACTION_STATE = RUN_DIR / "后台总览" / "今日动作发送状态.json"
PUBLISH_SCRIPT = ROOT / ".codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py"
MESSAGE_SCRIPT = ROOT / ".codex/skills/feishu-publish/scripts/build_openclaw_feishu_message.py"


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_path(value: str | Path, *, base: Path = ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def latest_push(run_dir: Path) -> Path:
    candidates = sorted(run_dir.glob("前台推送-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"没有找到前台推送文件：{repo_relative(run_dir)}/前台推送-*.md")
    return candidates[0]


def run_python(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True, check=False)


def now_iso() -> str:
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def load_today_action(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"没有找到今日动作：{repo_relative(path)}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"今日动作不是合法 JSON：{repo_relative(path)}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"今日动作必须是对象：{repo_relative(path)}")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError(f"今日动作缺少 items 列表：{repo_relative(path)}")
    return payload


def normalized_today_action(payload: dict[str, object]) -> dict[str, object]:
    items = payload.get("items")
    normalized_items: list[dict[str, str]] = []
    if isinstance(items, list):
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            item = {key: str(raw_item.get(key) or "").strip() for key in ("source", "title", "action", "reason", "target")}
            if item["title"]:
                normalized_items.append(item)
    return {
        "action_type": str(payload.get("action_type") or "暂无动作").strip(),
        "items": normalized_items,
    }


def today_action_fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(normalized_today_action(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_today_action_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "actions": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"今日动作发送状态不是合法 JSON：{repo_relative(path)}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("actions"), dict):
        raise ValueError(f"今日动作发送状态格式无效：{repo_relative(path)}")
    return state


def save_today_action_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def reply_options(action: str) -> str:
    if action == "确认":
        return "确认转正 / 继续核验 / 调整分类 / 跳过"
    if "核验" in action or "判断" in action:
        return "继续核验 / 补充判断 / 跳过"
    return "已读：你的想法 / 跳过"


def render_today_action_message(payload: dict[str, object]) -> str:
    normalized = normalized_today_action(payload)
    action_type = str(normalized["action_type"])
    items = normalized["items"]
    if not isinstance(items, list) or not items:
        return ""
    heading = {
        "确认": "今天需要你确认的事项",
        "阅读": "今天值得看的内容",
        "补充判断/核验": "今天需要补充判断的事项",
    }.get(action_type, "今天的待办事项")
    lines = [heading]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "未命名事项")
        action = str(item.get("action") or action_type)
        reason = str(item.get("reason") or "")
        target = str(item.get("target") or "")
        lines.extend(["", f"{index}. {title}"])
        if target:
            lines.append(f"候选：`{target}`")
        if reason:
            lines.append(f"原因：{reason}")
        lines.append(f"回复：{index} {reply_options(action)}")
    return "\n".join(lines).strip()


def validate_sent_at(value: str) -> str:
    timestamp = value or now_iso()
    try:
        parsed = dt.datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError("发送时间必须是带时区的 ISO-8601 时间") from exc
    if parsed.tzinfo is None:
        raise ValueError("发送时间必须带时区")
    return timestamp


def acknowledge_today_action_sent(args: argparse.Namespace, action_path: Path, state_path: Path) -> int:
    if not args.channel.strip():
        raise ValueError("--ack-today-action-sent 需要 --channel")
    if not args.message_id.strip() and not args.idempotency_key.strip():
        raise ValueError("--ack-today-action-sent 需要 --message-id 或 --idempotency-key")
    payload = load_today_action(action_path)
    fingerprint = today_action_fingerprint(payload)
    state = load_today_action_state(state_path)
    actions = state["actions"]
    assert isinstance(actions, dict)
    evidence = args.message_id.strip() or args.idempotency_key.strip()
    existing = actions.get(fingerprint)
    if isinstance(existing, dict):
        existing_evidence = str(existing.get("message_id") or existing.get("idempotency_key") or "")
        if existing.get("stage") == "sent" and existing_evidence == evidence:
            print("今日动作发送回执已存在，状态未变化")
            return 0
        if existing.get("stage") == "sent":
            raise ValueError("同一今日动作已存在不同发送回执")
    entry: dict[str, str] = {
        "stage": "sent",
        "sent_at": validate_sent_at(args.sent_at),
        "channel": args.channel.strip(),
        "action_type": str(payload.get("action_type") or "暂无动作"),
    }
    if args.message_id.strip():
        entry["message_id"] = args.message_id.strip()
    else:
        entry["idempotency_key"] = args.idempotency_key.strip()
    actions[fingerprint] = entry
    save_today_action_state(state_path, state)
    print("今日动作发送回执已记录")
    return 0


def prepare_today_action_message(args: argparse.Namespace, action_path: Path, state_path: Path) -> int:
    payload = load_today_action(action_path)
    fingerprint = today_action_fingerprint(payload)
    state = load_today_action_state(state_path)
    actions = state.get("actions")
    previous = actions.get(fingerprint) if isinstance(actions, dict) else None
    already_sent = isinstance(previous, dict) and previous.get("stage") == "sent"
    message = "" if already_sent else render_today_action_message(payload)
    if args.json:
        print(
            json.dumps(
                {
                    "mode": "today_action",
                    "should_send": bool(message),
                    "fingerprint": fingerprint,
                    "message": message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif message:
        print(message)
    return 0


def mark_push_published(push_path: Path, state_path: Path) -> bool:
    if not state_path.exists():
        return False
    state = delivery.load_state(state_path)
    sources = delivery.source_files_from_push(push_path)
    items = state.get("items", {})
    eligible = [source for source in sources if isinstance(items.get(source), dict) and items[source].get("delivery_stage") in {"prepared", "published", "sent", "feedback_received", "applied"}]
    if not eligible:
        return False
    changed = delivery.mark_published(state, eligible, push_file=repo_relative(push_path), at=now_iso())
    if changed:
        delivery.save_state(state_path, state)
    return changed


def acknowledge_sent(args: argparse.Namespace, push_path: Path, state_path: Path) -> int:
    if not state_path.exists():
        raise FileNotFoundError(f"没有找到前台推送状态：{repo_relative(state_path)}")
    sources = delivery.source_files_from_push(push_path)
    state = delivery.load_state(state_path)
    changed = delivery.ack_sent(
        state,
        sources,
        channel=args.channel,
        at=args.sent_at or now_iso(),
        message_id=args.message_id,
        idempotency_key=args.idempotency_key,
    )
    if changed:
        delivery.save_state(state_path, state)
    print("发送回执已记录" if changed else "发送回执已存在，状态未变化")
    return 0


def print_completed(completed: subprocess.CompletedProcess[str], *, stream: object = sys.stderr) -> None:
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if stdout:
        print(stdout, file=stream)
    if stderr:
        print(stderr, file=stream)


def build_message_args(args: argparse.Namespace, push_file: str) -> list[str]:
    command = [
        str(MESSAGE_SCRIPT),
        "--push-file",
        push_file,
        "--max-items",
        str(max(0, args.message_max_items)),
        "--summary-chars",
        str(max(0, args.summary_chars)),
        "--confirmation-max-items",
        str(max(0, args.confirmation_max_items)),
    ]
    if args.confirmation_file:
        command.extend(["--confirmation-file", args.confirmation_file])
    if args.chunk_size > 0:
        command.extend(["--chunk-size", str(args.chunk_size)])
    if args.json:
        command.append("--json")
    return command


def publish_args(args: argparse.Namespace, push_file: str, *, dry_run: bool) -> list[str]:
    command = [str(PUBLISH_SCRIPT), "--push-file", push_file]
    command.append("--dry-run" if dry_run else "--publish")
    if args.force:
        command.append("--force")
    if args.publish_max_items > 0:
        command.extend(["--max-items", str(args.publish_max_items)])
    passthrough = {
        "--publish-command": args.publish_command,
        "--update-command": args.update_command,
        "--wiki-move-space-id": args.wiki_move_space_id,
        "--wiki-move-parent-node-token": args.wiki_move_parent_node_token,
        "--item-wiki-parent-node-token": args.item_wiki_parent_node_token,
        "--item-parent-map": args.item_parent_map,
        "--wiki-move-command": args.wiki_move_command,
    }
    for key, value in passthrough.items():
        if value:
            command.extend([key, value])
    if args.no_auto_item_parent_map:
        command.append("--no-auto-item-parent-map")
    if args.include_incomplete:
        command.append("--include-incomplete")
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish the latest my-mind frontdesk push if needed, then output the OpenClaw Feishu message."
    )
    parser.add_argument("--push-file", default="", help="Frontdesk push markdown path. Defaults to latest.")
    parser.add_argument("--run-dir", default=str(RUN_DIR), help="Run-record directory.")
    parser.add_argument("--push-state", default=str(PUSH_STATE), help="Frontdesk delivery state JSON.")
    parser.add_argument("--today-action", default="", help="Render only this canonical 今日动作 JSON; never read frontdesk pushes or legacy reminders.")
    parser.add_argument("--today-action-state", default=str(TODAY_ACTION_STATE), help="Local send-receipt state for --today-action mode.")
    parser.add_argument("--ack-sent", action="store_true", help="Record an explicit OpenClaw send acknowledgement without publishing or sending.")
    parser.add_argument("--ack-today-action-sent", action="store_true", help="Record an explicit send acknowledgement for --today-action mode.")
    parser.add_argument("--channel", default="", help="Required channel for --ack-sent.")
    parser.add_argument("--message-id", default="", help="External message ID for --ack-sent.")
    parser.add_argument("--idempotency-key", default="", help="Explicit idempotency key when no message ID is available.")
    parser.add_argument("--sent-at", default="", help="Acknowledged send time in ISO-8601. Defaults to now.")
    parser.add_argument("--skip-publish", action="store_true", help="Only build the message; fail if no published bundle exists.")
    parser.add_argument("--dry-run", action="store_true", help="Preview the publish step when no matching bundle exists; do not publish.")
    parser.add_argument("--force", action="store_true", help="Force publish instead of reusing/updating existing records.")
    parser.add_argument("--publish-max-items", type=int, default=0, help="Maximum items to publish. 0 means all.")
    parser.add_argument("--message-max-items", type=int, default=3, help="Maximum item highlights in OpenClaw message.")
    parser.add_argument("--summary-chars", type=int, default=90, help="Maximum summary characters per highlighted item.")
    parser.add_argument("--confirmation-file", default="", help="Legacy explicit reminder markdown that contains 待确认候选.")
    parser.add_argument("--confirmation-max-items", type=int, default=0, help="Maximum legacy confirmation items appended to a bundle message. 0 disables.")
    parser.add_argument("--chunk-size", type=int, default=0, help="Split final message into chunks. 0 disables splitting.")
    parser.add_argument("--json", action="store_true", help="Output final OpenClaw message as structured JSON.")
    parser.add_argument("--publish-command", default="", help="Override Feishu create command.")
    parser.add_argument("--update-command", default="", help="Override Feishu update command.")
    parser.add_argument("--wiki-move-space-id", default="", help="Target Feishu wiki space id.")
    parser.add_argument("--wiki-move-parent-node-token", default="", help="Target parent wiki node token for index page.")
    parser.add_argument("--item-wiki-parent-node-token", default="", help="Target parent wiki node token for item pages.")
    parser.add_argument("--item-parent-map", default="", help="Optional JSON map for per-item target wiki directories.")
    parser.add_argument("--no-auto-item-parent-map", action="store_true", help="Disable per-item target wiki directory inference.")
    parser.add_argument("--include-incomplete", action="store_true", help="Allow publishing items blocked by the Feishu completeness gate.")
    parser.add_argument("--wiki-move-command", default="", help="Override wiki move command.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.today_action:
        if args.ack_sent:
            raise ValueError("--today-action 不能与 --ack-sent 同时使用")
        action_path = resolve_path(args.today_action)
        action_state_path = resolve_path(args.today_action_state)
        if args.ack_today_action_sent:
            return acknowledge_today_action_sent(args, action_path, action_state_path)
        return prepare_today_action_message(args, action_path, action_state_path)
    if args.ack_today_action_sent:
        raise ValueError("--ack-today-action-sent 需要 --today-action")
    run_dir = resolve_path(args.run_dir)
    push_path = resolve_path(args.push_file) if args.push_file else latest_push(run_dir)
    state_path = resolve_path(args.push_state)
    push_file = repo_relative(push_path)

    if args.ack_sent:
        return acknowledge_sent(args, push_path, state_path)

    build = run_python(build_message_args(args, push_file))
    if build.returncode == 0:
        mark_push_published(push_path, state_path)
        print(build.stdout.rstrip())
        return 0

    if args.skip_publish:
        print_completed(build)
        return build.returncode

    if args.dry_run:
        print_completed(build)
        print("", file=sys.stderr)
        print("将先发布飞书精选 bundle，再生成 OpenClaw 消息；dry-run 模式仅预览发布动作。", file=sys.stderr)
        preview = run_python(publish_args(args, push_file, dry_run=True))
        print_completed(preview, stream=sys.stdout)
        return preview.returncode

    print_completed(build)
    print("", file=sys.stderr)
    print(f"正在发布飞书精选 bundle：{push_file}", file=sys.stderr)
    published = run_python(publish_args(args, push_file, dry_run=False))
    if published.returncode != 0:
        print_completed(published)
        return published.returncode
    print_completed(published)

    final = run_python(build_message_args(args, push_file))
    if final.returncode != 0:
        print_completed(final)
        return final.returncode
    mark_push_published(push_path, state_path)
    print(final.stdout.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
