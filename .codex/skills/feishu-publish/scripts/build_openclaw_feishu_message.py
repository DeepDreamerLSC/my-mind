#!/usr/bin/env python3
"""Build the short OpenClaw message from a published Feishu reading page."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUN_DIR = ROOT / "85_运行记录"
DEFAULT_RECORD_FILE = DEFAULT_RUN_DIR / "飞书发布记录.jsonl"
DEFAULT_CONFIRMATION_FILE = DEFAULT_RUN_DIR / "后台总览" / "OpenClaw待提醒.md"


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def normalize_path(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        return repo_relative(path)
    return path.as_posix().lstrip("./")


def latest_push(run_dir: Path) -> Path:
    candidates = sorted(run_dir.glob("前台推送-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"没有找到前台推送文件：{repo_relative(run_dir)}/前台推送-*.md")
    return candidates[0]


def load_records(record_file: Path) -> list[dict[str, object]]:
    if not record_file.exists():
        return []
    records: list[dict[str, object]] = []
    for line in record_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def published(record: dict[str, object]) -> bool:
    return record.get("status") == "已发布" and bool(record.get("feishu_url"))


def find_record(records: list[dict[str, object]], source_push: str, allow_stale: bool, allow_legacy: bool) -> dict[str, object] | None:
    for record in reversed(records):
        if (
            record.get("record_type") == "frontdesk_bundle_index"
            and published(record)
            and normalize_path(record.get("source_push")) == source_push
        ):
            return record
    if allow_stale:
        for record in reversed(records):
            if record.get("record_type") == "frontdesk_bundle_index" and published(record):
                return record
    if not allow_legacy:
        return None
    for record in reversed(records):
        if published(record) and normalize_path(record.get("source_push")) == source_push:
            return record
    if allow_stale:
        for record in reversed(records):
            if published(record):
                return record
    return None


def reading_url(record: dict[str, object]) -> str:
    wiki_token = str(record.get("wiki_node_token") or "").strip()
    if wiki_token:
        return f"https://my.feishu.cn/wiki/{wiki_token}"
    return str(record.get("feishu_url") or "").strip()


def trim(text: object, max_chars: int) -> str:
    value = " ".join(str(text or "").split())
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def section_between(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    start = text.find("\n", start)
    if start < 0:
        return ""
    next_index = text.find("\n## ", start + 1)
    end = next_index if next_index >= 0 else len(text)
    return text[start:end].strip()


def parse_confirmation_heading(line: str) -> dict[str, str]:
    match = re.match(r"^(?P<index>\d+)\.\s+(?P<title>.+)$", line.strip())
    if not match:
        return {}
    title = match.group("title").strip()
    parts = [part.strip() for part in title.split("｜") if part.strip()]
    parsed = {
        "index": match.group("index"),
        "title": parts[0] if parts else title,
        "type": parts[1] if len(parts) > 1 else "",
        "priority": parts[2] if len(parts) > 2 else "",
    }
    return parsed


def render_confirmation_card(card: dict[str, str]) -> str:
    index = card.get("index") or "?"
    lines = [f"决策卡 {index}｜{card.get('title') or '未命名候选'}"]
    meta = " / ".join(value for value in [card.get("type", ""), card.get("priority", "")] if value)
    if meta:
        lines.append(f"   类型：{meta}")
    if card.get("candidate"):
        lines.append(f"   候选：{card['candidate']}")
    if card.get("source"):
        lines.append(f"   来源：{card['source']}")
    suggestion = card.get("suggestion") or "建议先确认是否转正；不确定就回复继续核验或调整分类。"
    lines.append(f"   建议：{suggestion}")
    replies = card.get("replies") or ""
    if replies and not re.match(r"^\d+\s+", replies.strip()):
        reply_parts = [part.strip() for part in replies.split("/") if part.strip()]
        replies = " / ".join(f"{index} {part}" for part in reply_parts)
    if not replies:
        replies = f"{index} 确认转正 / {index} 继续核验 / {index} 调整分类：资料库 / {index} 跳过"
    lines.append(f"   可回复：{replies}")
    return "\n".join(lines)


def load_confirmation_items(path: Path, max_items: int) -> list[str]:
    if max_items <= 0 or not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    section = section_between(text, "待确认候选")
    if not section:
        return []
    items: list[str] = []
    current: dict[str, str] | None = None
    for raw_line in section.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if re.match(r"^\d+\.\s+", line):
            if current:
                items.append(render_confirmation_card(current))
            current = parse_confirmation_heading(line)
            continue
        if not current:
            continue
        clean = line.strip().lstrip("- ").strip()
        for field, key in [
            ("候选：", "candidate"),
            ("来源：", "source"),
            ("可回复：", "replies"),
            ("建议：", "suggestion"),
        ]:
            if clean.startswith(field):
                current[key] = clean.removeprefix(field).strip()
                break
    if current:
        items.append(render_confirmation_card(current))
    return items[:max_items]


def item_lines(record: dict[str, object], max_items: int, summary_chars: int) -> list[str]:
    raw_items = record.get("items")
    if not isinstance(raw_items, list):
        return []
    items = raw_items if max_items == 0 else raw_items[:max_items]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("index") or "?"
        title = trim(item.get("title"), 80)
        summary = trim(item.get("summary"), summary_chars)
        item_url = str(item.get("wiki_url") or item.get("feishu_url") or "").strip()
        if not title:
            continue
        if item_url:
            title = f"{title}\n   {item_url}"
        if summary:
            lines.append(f"{index}. {title}\n   {summary}")
        else:
            lines.append(f"{index}. {title}")
    return lines


def split_chunks(message: str, chunk_size: int) -> list[str]:
    if chunk_size <= 0 or len(message) <= chunk_size:
        return [message]
    chunks: list[str] = []
    current = ""
    for paragraph in message.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
        else:
            for start in range(0, len(paragraph), chunk_size):
                chunks.append(paragraph[start : start + chunk_size])
            current = ""
    if current:
        chunks.append(current)
    return chunks


def render_message(
    record: dict[str, object],
    max_items: int,
    summary_chars: int,
    confirmation_items: list[str] | None = None,
) -> str:
    title = str(record.get("title") or "my-mind 今日待读").strip()
    url = reading_url(record)
    count = int(record.get("item_count") or 0)
    is_bundle = record.get("record_type") == "frontdesk_bundle_index"
    lines = [
        f"{title} 已发布到飞书知识库：",
        url,
        "",
        f"本次共 {count} 条，完整内容和原文入口都在{'各单篇文章' if is_bundle else '飞书页'}里。",
    ]
    highlights = item_lines(record, max_items=max_items, summary_chars=summary_chars)
    if highlights:
        lines.extend(["", "精选条目：" if is_bundle else "先看这几条："])
        for highlight in highlights:
            lines.extend(["", highlight])
    if confirmation_items:
        lines.extend(["", "需要你确认（决策卡）："])
        for item in confirmation_items:
            lines.extend(["", item])
    lines.extend(
        [
            "",
            "回复格式：",
            "序号 已读：你的想法",
            "序号 沉淀成提示词",
            "序号 跳过",
            "序号 继续解析",
            "序号 确认转正",
            "序号 继续核验",
            "序号 调整分类：资料库",
        ]
    )
    return "\n".join(lines).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an OpenClaw message from Feishu publish records.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Run-record directory.")
    parser.add_argument("--record-file", default=str(DEFAULT_RECORD_FILE), help="Feishu publish JSONL record.")
    parser.add_argument("--push-file", default="", help="Frontdesk push file that must have a matching published Feishu record. Defaults to latest.")
    parser.add_argument("--max-items", type=int, default=3, help="Maximum item highlights. Use 0 for all.")
    parser.add_argument("--summary-chars", type=int, default=90, help="Maximum summary characters per highlighted item.")
    parser.add_argument("--chunk-size", type=int, default=0, help="Split output into chunks no longer than this many characters. 0 disables splitting.")
    parser.add_argument("--confirmation-file", default=str(DEFAULT_CONFIRMATION_FILE), help="OpenClaw reminder markdown that contains 待确认候选.")
    parser.add_argument("--confirmation-max-items", type=int, default=2, help="Maximum confirmation items appended to the message. 0 disables.")
    parser.add_argument("--allow-stale", action="store_true", help="Allow latest published Feishu record even if it does not match the latest push.")
    parser.add_argument("--allow-legacy-reading-page", action="store_true", help="Allow old single-page reading records when no bundle index exists.")
    parser.add_argument("--json", action="store_true", help="Output structured JSON instead of plain text.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    record_file = Path(args.record_file)
    if not record_file.is_absolute():
        record_file = ROOT / record_file
    confirmation_file = Path(args.confirmation_file)
    if not confirmation_file.is_absolute():
        confirmation_file = ROOT / confirmation_file
    push_path = Path(args.push_file) if args.push_file else latest_push(run_dir)
    if not push_path.is_absolute():
        push_path = ROOT / push_path
    source_push = repo_relative(push_path)

    records = load_records(record_file)
    record = find_record(records, source_push, allow_stale=args.allow_stale, allow_legacy=args.allow_legacy_reading_page)
    if not record:
        print(
            "\n".join(
                [
                    f"未找到与最新前台推送匹配的飞书精选索引记录：{source_push}",
                    "OpenClaw 不应退回发送原文链接；请先发布飞书精选 bundle，再重新生成前台消息。",
                    "建议命令：",
                    f"python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish --push-file '{source_push}'",
                ]
            ),
            file=sys.stderr,
        )
        return 2

    confirmation_items = load_confirmation_items(confirmation_file, max(0, args.confirmation_max_items))
    message = render_message(record, max(0, args.max_items), max(0, args.summary_chars), confirmation_items)
    chunks = split_chunks(message, args.chunk_size)
    if args.json:
        payload = {
            "source_push": source_push,
            "record_type": record.get("record_type") or "",
            "title": record.get("title") or "",
            "feishu_url": record.get("feishu_url") or "",
            "wiki_url": reading_url(record),
            "wiki_node_token": record.get("wiki_node_token") or "",
            "item_count": record.get("item_count") or 0,
            "items": record.get("items") or [],
            "confirmation_items": confirmation_items,
            "chunks": chunks,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\n\n---\n\n".join(chunks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
