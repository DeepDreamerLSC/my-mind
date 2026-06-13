#!/usr/bin/env python3
"""Append an OpenClaw frontdesk reply into the my-mind feedback queue."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RUN_DIR = ROOT / "85_运行记录"
QUEUE_NAME = "前台反馈队列.jsonl"
CONFIRM_QUEUE_MD = ROOT / "05_流转区/50_待确认/待确认队列.md"


ACTION_ALIASES = [
    ("确认转正", ["确认转正", "确认晋升", "转正", "转为长期知识", "长期知识", "确认入库"]),
    ("继续核验", ["继续核验", "先核验", "补核验", "待核验"]),
    ("调整分类", ["调整分类", "改成提示词", "改成资料库", "改成资料", "改成洞察", "分类"]),
    ("继续解析", ["继续解析", "补解析", "补抓", "ocr", "OCR", "转写", "字幕"]),
    (
        "跳过",
        [
            "跳过",
            "略过",
            "先不处理",
            "不用管",
            "不沉淀",
            "不要沉淀",
            "不用沉淀",
            "不需要沉淀",
            "不必沉淀",
        ],
    ),
    ("沉淀", ["沉淀", "生成提示词", "成提示词", "资料库", "原子笔记"]),
    ("补充想法", ["补充", "想法", "备注"]),
    ("已读", ["已读", "读完", "看完", "看了"]),
]

CONFIRM_ACTIONS = {"确认转正", "继续核验", "调整分类"}


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def latest_push_file(run_dir: Path) -> Path | None:
    files = sorted(run_dir.glob("前台推送-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def default_context_file(run_dir: Path, action: str) -> Path | None:
    if action in CONFIRM_ACTIONS and CONFIRM_QUEUE_MD.exists():
        return CONFIRM_QUEUE_MD
    return latest_push_file(run_dir)


def parse_push_targets(path: Path | None) -> dict[str, dict[str, object]]:
    if path is None or not path.exists():
        return {}
    targets: dict[str, dict[str, object]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        heading = re.match(r"^###\s+(\d+)\.\s+(.+)", line)
        if heading:
            current = heading.group(1)
            targets[current] = {"目标标题": heading.group(2).strip()}
            continue
        if current is None:
            continue
        source = re.match(r"^- 来源文件：`(.+)`", line)
        if source:
            targets[current]["来源文件"] = source.group(1).strip()
        candidate = re.match(r"^- 候选文件：(.+)", line)
        if candidate:
            paths = re.findall(r"`([^`]+)`", candidate.group(1))
            if paths:
                targets[current]["候选文件"] = paths[0]
                targets[current]["候选文件列表"] = paths
    return targets


def infer_action(reply: str) -> str:
    stripped = reply.strip()
    prefix_tail = r"[\s:：,，;；.!！?？-]*"
    for action, aliases in ACTION_ALIASES:
        for alias in aliases:
            if re.match(rf"^{re.escape(alias)}{prefix_tail}", stripped, flags=re.I):
                return action
    for action, aliases in ACTION_ALIASES:
        if any(alias in reply for alias in aliases):
            return action
    return "补充想法"


def infer_target_type(reply: str, action: str) -> str:
    if "提示词" in reply:
        return "提示词"
    if "资料" in reply or "资料库" in reply:
        return "资料库"
    if "原子" in reply:
        return "原子笔记"
    if "洞察" in reply:
        return "洞察"
    if action == "确认转正":
        return "长期知识"
    if action == "继续解析":
        if "ocr" in reply.lower():
            return "OCR"
        if "转写" in reply or "字幕" in reply:
            return "转写或字幕"
        return "补解析"
    return ""


def strip_action_prefix(text: str, target: str, action: str) -> str:
    cleaned = text.strip()
    prefix_tail = r"[\s:：,，;；.!！?？-]*"
    if target:
        cleaned = re.sub(rf"^\s*{re.escape(target)}[\.\)、:：\s-]*", "", cleaned).strip()
    for _, aliases in ACTION_ALIASES:
        for alias in aliases:
            cleaned = re.sub(rf"^{re.escape(alias)}{prefix_tail}", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(rf"^{re.escape(action)}{prefix_tail}", "", cleaned).strip()
    return cleaned


def parse_reply(reply: str) -> tuple[str, str, str, str]:
    text = reply.strip()
    target_match = re.match(r"^(\d+)(?:[\.\)、:：\s-]+)?(.*)$", text)
    target = target_match.group(1) if target_match else ""
    rest = target_match.group(2).strip() if target_match else text
    action = infer_action(rest or text)
    target_type = infer_target_type(rest or text, action)
    content = strip_action_prefix(rest or text, target, action)
    return target, action, target_type, content


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append OpenClaw frontdesk feedback into JSONL queue.")
    parser.add_argument("reply", help="Raw OpenClaw/user reply, e.g. '1 已读：我的想法'.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Run record directory.")
    parser.add_argument("--queue", default="", help="Feedback queue path. Defaults to --run-dir/前台反馈队列.jsonl.")
    parser.add_argument("--push-file", default="", help="Frontdesk push file used to resolve target number.")
    parser.add_argument("--source", default="OpenClaw", help="Feedback source.")
    parser.add_argument("--channel", default="", help="OpenClaw channel, such as 微信 or Telegram.")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON record instead of appending.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    queue_path = Path(args.queue) if args.queue else run_dir / QUEUE_NAME
    if not queue_path.is_absolute():
        queue_path = ROOT / queue_path
    target, action, target_type, content = parse_reply(args.reply)
    push_path = Path(args.push_file) if args.push_file else default_context_file(run_dir, action)
    if push_path and not push_path.is_absolute():
        push_path = ROOT / push_path

    targets = parse_push_targets(push_path)
    target_info = targets.get(target, {})
    record = {
        "时间": now_datetime(),
        "来源": args.source,
        "渠道": args.channel,
        "推送文件": str(push_path.relative_to(ROOT) if push_path and push_path.is_relative_to(ROOT) else push_path or ""),
        "目标序号": target,
        "目标标题": target_info.get("目标标题", ""),
        "来源文件": target_info.get("来源文件", ""),
        "候选文件": target_info.get("候选文件", ""),
        "候选文件列表": target_info.get("候选文件列表", []),
        "动作": action,
        "目标类型": target_type,
        "内容": content,
        "原始回复": args.reply,
        "处理状态": "待处理",
    }
    line = json.dumps(record, ensure_ascii=False)
    if args.dry_run:
        print(line)
        return 0

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("a", encoding="utf-8") as file:
        file.write(line + "\n")
    print(queue_path.relative_to(ROOT) if queue_path.is_relative_to(ROOT) else queue_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
