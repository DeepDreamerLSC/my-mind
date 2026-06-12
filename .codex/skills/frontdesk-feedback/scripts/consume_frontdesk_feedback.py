#!/usr/bin/env python3
"""Consume OpenClaw frontdesk feedback and write it back to source inbox notes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RUN_DIR = ROOT / "85_运行记录"
DEFAULT_QUEUE = DEFAULT_RUN_DIR / "前台反馈队列.jsonl"
DEFAULT_PUSH_STATE = DEFAULT_RUN_DIR / "前台推送状态.json"
DISTILL_SCRIPT = ROOT / ".codex" / "skills" / "inbox-distill" / "scripts" / "distill_inbox_note.py"
TERMINAL_STATUSES = {"已处理", "已晋升", "可丢弃", "已归档"}


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_date() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            records.append({"处理状态": "解析失败", "处理结果": f"第 {index} 行不是合法 JSON", "原始行": line})
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n", encoding="utf-8")


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    lines = text.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}
    data: dict[str, Any] = {}
    for raw_line in lines[1:end_index]:
        if ":" not in raw_line or raw_line.startswith("  "):
            continue
        key, value = raw_line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def set_scalar_field(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}: {value}"
            return "\n".join(lines).rstrip() + "\n"
    insert_at = 1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            insert_at = index
            break
    lines.insert(insert_at, f"{key}: {value}")
    return "\n".join(lines).rstrip() + "\n"


def append_to_section(text: str, heading: str, entry: str) -> str:
    if entry in text:
        return text
    pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*\n)", flags=re.M)
    match = pattern.search(text)
    if match:
        insert_at = match.end()
        return text[:insert_at] + "\n" + entry.rstrip() + "\n" + text[insert_at:].lstrip("\n")
    insert_before = re.search(r"^## 原始链接\s*$", text, flags=re.M)
    block = f"\n## {heading}\n\n{entry.rstrip()}\n"
    if insert_before:
        return text[: insert_before.start()].rstrip() + "\n" + block + "\n" + text[insert_before.start() :]
    return text.rstrip() + block


def feedback_entry(record: dict[str, Any]) -> str:
    action = str(record.get("动作") or "反馈")
    source = str(record.get("来源") or "OpenClaw")
    channel = str(record.get("渠道") or "")
    content = str(record.get("内容") or record.get("原始回复") or "").strip()
    if not content:
        if action == "沉淀":
            content = f"用户要求继续沉淀为{record.get('目标类型') or '候选内容'}。"
        elif action == "继续解析":
            content = "用户要求继续解析。"
        elif action == "跳过":
            content = "用户选择跳过本条。"
        else:
            content = "用户已读。"
    channel_text = f"/{channel}" if channel else ""
    return f"- {now_date()}（{source}{channel_text}，{action}）：{content}"


def process_record(record: dict[str, Any], *, write: bool, distill: bool, force_distill: bool) -> tuple[dict[str, Any], str]:
    if str(record.get("处理状态") or "") != "待处理":
        return record, "跳过：不是待处理反馈"
    source = str(record.get("来源文件") or "").strip()
    if not source:
        record["处理状态"] = "处理失败"
        record["处理结果"] = "缺少来源文件，无法回写"
        record["处理时间"] = now_datetime()
        return record, "失败：缺少来源文件"
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    if not source_path.exists():
        record["处理状态"] = "处理失败"
        record["处理结果"] = f"来源文件不存在：{source}"
        record["处理时间"] = now_datetime()
        return record, f"失败：来源文件不存在 {source}"

    raw = source_path.read_text(encoding="utf-8", errors="ignore")
    meta = parse_frontmatter(raw)
    action = str(record.get("动作") or "")
    updated = raw
    entry = feedback_entry(record)

    if action in {"已读", "补充想法", "沉淀"}:
        updated = append_to_section(updated, "阅读思考", entry)
    if action == "跳过":
        updated = append_to_section(updated, "前台反馈处理记录", entry)
        updated = set_scalar_field(updated, "处理状态", "已归档")
    elif action == "继续解析":
        updated = append_to_section(updated, "前台反馈处理记录", entry)
        if str(meta.get("处理状态") or "") not in TERMINAL_STATUSES:
            updated = set_scalar_field(updated, "处理状态", "待分拣")
    elif str(meta.get("处理状态") or "") not in TERMINAL_STATUSES:
        updated = set_scalar_field(updated, "处理状态", "已分拣")

    if write:
        source_path.write_text(updated, encoding="utf-8")

    distill_result = ""
    target_type = str(record.get("目标类型") or "")
    if action == "沉淀" and distill and ("提示词" in target_type or "提示词" in str(record.get("原始回复") or "")):
        command = [sys.executable, str(DISTILL_SCRIPT), "--source", repo_rel(source_path), "--target", "prompt", "--write"]
        if force_distill:
            command.append("--force")
        if write:
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            distill_result = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                record["处理状态"] = "处理失败"
                record["处理结果"] = "阅读思考已准备回写，但候选提示词生成失败：" + distill_result[-500:]
                record["处理时间"] = now_datetime()
                return record, f"失败：候选提示词生成失败 {source}"
        else:
            distill_result = "dry-run：将调用 inbox-distill 生成候选提示词"

    record["处理状态"] = "已处理"
    record["处理时间"] = now_datetime()
    if action == "沉淀" and distill_result:
        record["处理结果"] = "已回写阅读思考并生成候选提示词"
    elif action == "沉淀":
        record["处理结果"] = "已回写阅读思考并记录沉淀请求"
    else:
        record["处理结果"] = "已回写阅读思考" if action in {"已读", "补充想法"} else "已记录前台处理动作"
    if distill_result:
        record["沉淀结果"] = distill_result[-1000:]
    return record, f"完成：{action} -> {source}"


def update_push_state(path: Path, records: list[dict[str, Any]], *, write: bool) -> None:
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    items = state.get("items") if isinstance(state, dict) else None
    if not isinstance(items, dict):
        return
    changed = False
    for record in records:
        source = str(record.get("来源文件") or "")
        if not source or source not in items:
            continue
        item = items[source]
        if not isinstance(item, dict):
            continue
        item["feedback_status"] = str(record.get("动作") or "已反馈")
        item["last_feedback_at"] = str(record.get("时间") or now_datetime())
        item["last_feedback_content"] = str(record.get("内容") or record.get("原始回复") or "")
        changed = True
    if changed:
        state["updated_at"] = dt.datetime.now(TZ).isoformat(timespec="seconds")
        if write:
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_report(results: list[str], processed: list[dict[str, Any]], dry_run: bool) -> str:
    lines = [
        "# 前台反馈消费报告",
        "",
        f"- 时间：{now_datetime()}",
        f"- 模式：{'dry-run' if dry_run else '写入'}",
        f"- 本轮处理：{len(processed)}",
        "",
        "## 结果",
        "",
    ]
    lines.extend(f"- {result}" for result in results)
    if processed:
        lines.extend(["", "## 已处理记录", ""])
        for record in processed:
            lines.append(
                f"- {record.get('动作', '未知动作')}：`{record.get('来源文件', '未知来源')}`，状态：{record.get('处理状态')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consume my-mind frontdesk feedback queue.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Feedback JSONL queue.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Run record directory.")
    parser.add_argument("--push-state", default=str(DEFAULT_PUSH_STATE), help="Frontdesk push state JSON.")
    parser.add_argument("--write", action="store_true", help="Write source updates and queue status. Default is dry-run.")
    parser.add_argument("--no-distill", action="store_true", help="Do not call inbox-distill for 沉淀成提示词 feedback.")
    parser.add_argument("--force-distill", action="store_true", help="Pass --force to inbox-distill when writing prompt candidates.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue = Path(args.queue)
    run_dir = Path(args.run_dir)
    push_state = Path(args.push_state)
    if not queue.is_absolute():
        queue = ROOT / queue
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if not push_state.is_absolute():
        push_state = ROOT / push_state

    records = read_jsonl(queue)
    updated_records: list[dict[str, Any]] = []
    processed_records: list[dict[str, Any]] = []
    results: list[str] = []
    for record in records:
        updated, result = process_record(
            record,
            write=args.write,
            distill=not args.no_distill,
            force_distill=args.force_distill,
        )
        updated_records.append(updated)
        results.append(result)
        if result.startswith(("完成", "失败")):
            processed_records.append(updated)

    report = build_report(results, processed_records, dry_run=not args.write)
    if args.write:
        write_jsonl(queue, updated_records)
        update_push_state(push_state, processed_records, write=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / f"反馈消费-{now_filename()}.md"
        report_path.write_text(report, encoding="utf-8")
        print(repo_rel(report_path))
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
