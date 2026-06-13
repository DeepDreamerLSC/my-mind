#!/usr/bin/env python3
"""Ensure a frontdesk push is published to Feishu, then build OpenClaw's message."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
RUN_DIR = ROOT / "85_运行记录"
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
    ]
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
        "--wiki-move-command": args.wiki_move_command,
    }
    for key, value in passthrough.items():
        if value:
            command.extend([key, value])
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish the latest my-mind frontdesk push if needed, then output the OpenClaw Feishu message."
    )
    parser.add_argument("--push-file", default="", help="Frontdesk push markdown path. Defaults to latest.")
    parser.add_argument("--run-dir", default=str(RUN_DIR), help="Run-record directory.")
    parser.add_argument("--skip-publish", action="store_true", help="Only build the message; fail if no published bundle exists.")
    parser.add_argument("--dry-run", action="store_true", help="Preview the publish step when no matching bundle exists; do not publish.")
    parser.add_argument("--force", action="store_true", help="Force publish instead of reusing/updating existing records.")
    parser.add_argument("--publish-max-items", type=int, default=0, help="Maximum items to publish. 0 means all.")
    parser.add_argument("--message-max-items", type=int, default=3, help="Maximum item highlights in OpenClaw message.")
    parser.add_argument("--summary-chars", type=int, default=90, help="Maximum summary characters per highlighted item.")
    parser.add_argument("--chunk-size", type=int, default=0, help="Split final message into chunks. 0 disables splitting.")
    parser.add_argument("--json", action="store_true", help="Output final OpenClaw message as structured JSON.")
    parser.add_argument("--publish-command", default="", help="Override Feishu create command.")
    parser.add_argument("--update-command", default="", help="Override Feishu update command.")
    parser.add_argument("--wiki-move-space-id", default="", help="Target Feishu wiki space id.")
    parser.add_argument("--wiki-move-parent-node-token", default="", help="Target parent wiki node token for index page.")
    parser.add_argument("--item-wiki-parent-node-token", default="", help="Target parent wiki node token for item pages.")
    parser.add_argument("--wiki-move-command", default="", help="Override wiki move command.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = resolve_path(args.run_dir)
    push_path = resolve_path(args.push_file) if args.push_file else latest_push(run_dir)
    push_file = repo_relative(push_path)

    build = run_python(build_message_args(args, push_file))
    if build.returncode == 0:
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
    print(final.stdout.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
