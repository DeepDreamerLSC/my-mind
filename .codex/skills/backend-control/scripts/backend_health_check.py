#!/usr/bin/env python3
"""Inspect my-mind backend automation health and suggest safe next actions."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_INBOX = ROOT / "00_收件箱"
DEFAULT_FLOW_DIR = ROOT / "05_流转区"
DEFAULT_RUN_DIR = ROOT / "85_运行记录"
DEFAULT_AUTOMATIONS_DIR = Path.home() / ".codex" / "automations"
DEFAULT_PUSH_STATE = DEFAULT_RUN_DIR / "前台推送状态.json"
DEFAULT_FEEDBACK_QUEUE = DEFAULT_RUN_DIR / "前台反馈队列.jsonl"


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


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
    current_key: str | None = None
    for raw_line in lines[1:end_index]:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(line[4:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip().strip('"')
        current_key = key
        data[key] = value if value else []
    return data


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            records.append({"处理状态": "解析失败", "原始行": line})
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def latest_file(pattern: str, directory: Path) -> dict[str, Any] | None:
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        return None
    path = files[0]
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    return {"path": repo_rel(path), "modified": modified}


def load_automations(automations_dir: Path) -> list[dict[str, Any]]:
    automations: list[dict[str, Any]] = []
    for path in sorted(automations_dir.glob("*/automation.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            automations.append({"id": path.parent.name, "name": path.parent.name, "error": str(exc), "path": path.as_posix()})
            continue
        data["path"] = path.as_posix()
        automations.append(data)
    return automations


def inspect_inbox(inbox: Path) -> dict[str, Any]:
    status_counter: Counter[str] = Counter()
    parse_counter: Counter[str] = Counter()
    quality_counter: Counter[str] = Counter()
    low_quality: list[dict[str, str]] = []
    total = 0
    for path in sorted(inbox.glob("*.md")):
        if path.name == "目录说明.md":
            continue
        total += 1
        meta = parse_frontmatter(read_text(path))
        status = str(meta.get("处理状态") or "未知")
        parse_status = str(meta.get("解析状态") or "未知")
        quality = str(meta.get("内容质量") or "未标记")
        status_counter[status] += 1
        parse_counter[parse_status] += 1
        quality_counter[quality] += 1
        if quality in {"需继续解析", "需核验"} or parse_status in {"解析失败", "部分解析"}:
            low_quality.append(
                {
                    "path": repo_rel(path),
                    "title": str(meta.get("标题") or path.stem),
                    "status": status,
                    "parse_status": parse_status,
                    "quality": quality,
                    "gate": str(meta.get("质量门禁") or ""),
                }
            )
    return {
        "total": total,
        "status": dict(status_counter),
        "parse_status": dict(parse_counter),
        "quality": dict(quality_counter),
        "low_quality": low_quality[:12],
        "low_quality_count": len(low_quality),
    }


def flow_count(path: Path) -> int:
    if not path.exists():
        return 0
    match = re.search(r"^- 条目数量：(\d+)", read_text(path), flags=re.M)
    if match:
        return int(match.group(1))
    return len(re.findall(r"^###\s+\d+\.", read_text(path), flags=re.M))


def inspect_flow(flow_dir: Path) -> dict[str, int]:
    return {
        "待读": flow_count(flow_dir / "10_待读" / "收件箱待读队列.md"),
        "待沉淀": flow_count(flow_dir / "30_待沉淀" / "收件箱待沉淀队列.md"),
        "待核验": flow_count(flow_dir / "40_待核验" / "收件箱待核验队列.md"),
    }


def inspect_feedback(queue_path: Path) -> dict[str, Any]:
    records = load_jsonl(queue_path)
    by_status = Counter(str(record.get("处理状态") or "未知") for record in records)
    by_action = Counter(str(record.get("动作") or "未知") for record in records)
    pending = [record for record in records if str(record.get("处理状态") or "") == "待处理"]
    return {
        "total": len(records),
        "by_status": dict(by_status),
        "by_action": dict(by_action),
        "pending_count": len(pending),
        "pending": pending[:8],
    }


def inspect_push_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "items": 0, "waiting_feedback": 0, "cooling": 0, "recent": []}
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return {"exists": True, "error": "前台推送状态 JSON 解析失败", "items": 0, "waiting_feedback": 0, "cooling": 0}
    items = data.get("items") if isinstance(data, dict) else {}
    if not isinstance(items, dict):
        items = {}
    waiting = []
    recent = []
    now = dt.datetime.now(TZ)
    cooling = 0
    for item_id, item in items.items():
        if not isinstance(item, dict):
            continue
        status = str(item.get("feedback_status") or "")
        last_pushed = str(item.get("last_pushed_at") or "")
        if status == "待反馈":
            waiting.append(item)
        try:
            pushed_at = dt.datetime.fromisoformat(last_pushed)
            if now - pushed_at < dt.timedelta(hours=24) and status == "待反馈":
                cooling += 1
        except ValueError:
            pass
        recent.append({"item_id": item_id, "title": item.get("title", ""), "last_pushed_at": last_pushed, "feedback_status": status})
    recent.sort(key=lambda item: str(item.get("last_pushed_at") or ""), reverse=True)
    return {
        "exists": True,
        "items": len(items),
        "waiting_feedback": len(waiting),
        "cooling": cooling,
        "recent": recent[:8],
    }


def inspect_run_records(run_dir: Path) -> dict[str, Any]:
    return {
        "latest_triage": latest_file("收件箱分拣巡检-*.md", run_dir),
        "latest_gate": latest_file("收件箱入箱门禁-*.md", run_dir) or latest_file("收件箱分拣巡检-*.md", run_dir),
        "latest_push": latest_file("前台推送-*.md", run_dir),
        "latest_feedback": latest_file("反馈消费-*.md", run_dir),
        "latest_feishu_publish": latest_file("飞书阅读页/飞书阅读页-*.md", run_dir),
        "latest_feishu_sync": latest_file("飞书知识库同步页/*.md", run_dir),
    }


def inspect_git() -> dict[str, Any]:
    try:
        result = subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "dirty_count": 0, "entries": []}
    entries = [line for line in result.stdout.splitlines() if line.strip()]
    return {"dirty_count": len(entries), "entries": entries[:12]}


def build_report(data: dict[str, Any]) -> str:
    flow = data["flow"]
    inbox = data["inbox"]
    feedback = data["feedback"]
    push_state = data["push_state"]
    actions: list[str] = []
    if feedback["pending_count"]:
        actions.append("优先消费前台反馈队列，把用户短反馈回写到来源笔记。")
    if inbox["low_quality_count"]:
        actions.append("低质量或未完整解析条目不进入前台推送，先补 OCR、字幕或转写。")
    if push_state.get("waiting_feedback"):
        actions.append("前台推送进入冷却，OpenClaw 不重复催同一批条目，除非用户主动要求。")
    if flow.get("待沉淀", 0):
        actions.append("待沉淀队列已有内容，可在用户确认后批量生成候选草稿。")
    if not actions:
        actions.append("后台链路暂无明显阻塞，保持现有自动化频率。")

    lines = [
        "# my-mind 后台总控巡检",
        "",
        "## 结论",
        "",
        f"- 巡检时间：{data['generated_at']}",
        f"- 收件箱总数：{inbox['total']}",
        f"- 流转区：待读 {flow['待读']}，待沉淀 {flow['待沉淀']}，待核验 {flow['待核验']}",
        f"- 待处理前台反馈：{feedback['pending_count']}",
        f"- 已推送未反馈：{push_state.get('waiting_feedback', 0)}",
        f"- 低质量或需核验条目：{inbox['low_quality_count']}",
        "",
        "## 建议动作",
        "",
    ]
    lines.extend(f"- {action}" for action in actions)
    lines.extend(["", "## 自动化", ""])
    for automation in data["automations"]:
        name = automation.get("name") or automation.get("id")
        status = automation.get("status", "UNKNOWN")
        rrule = automation.get("rrule", "")
        lines.append(f"- {name}：{status}，{rrule}")
    lines.extend(["", "## 收件箱状态", ""])
    lines.append(f"- 处理状态：{json.dumps(inbox['status'], ensure_ascii=False)}")
    lines.append(f"- 解析状态：{json.dumps(inbox['parse_status'], ensure_ascii=False)}")
    lines.append(f"- 内容质量：{json.dumps(inbox['quality'], ensure_ascii=False)}")
    if inbox["low_quality"]:
        lines.extend(["", "### 需关注条目", ""])
        for item in inbox["low_quality"][:8]:
            lines.append(f"- `{item['path']}`：{item['quality']} / {item['parse_status']}。{item['gate']}")
    lines.extend(["", "## 反馈队列", ""])
    lines.append(f"- 状态分布：{json.dumps(feedback['by_status'], ensure_ascii=False)}")
    lines.append(f"- 动作分布：{json.dumps(feedback['by_action'], ensure_ascii=False)}")
    if feedback["pending"]:
        lines.extend(["", "### 待处理反馈", ""])
        for record in feedback["pending"]:
            source = record.get("来源文件") or "未知来源"
            action = record.get("动作") or "未知动作"
            content = record.get("内容") or record.get("原始回复") or ""
            lines.append(f"- {action}：`{source}` - {content}")
    lines.extend(["", "## 推送节流", ""])
    if push_state.get("exists"):
        lines.append(f"- 状态条目：{push_state.get('items', 0)}")
        lines.append(f"- 24 小时冷却中：{push_state.get('cooling', 0)}")
    else:
        lines.append("- 尚未生成前台推送状态文件。")
    lines.extend(["", "## 最近运行记录", ""])
    for key, value in data["run_records"].items():
        label = key.replace("latest_", "")
        if value:
            lines.append(f"- {label}：`{value['path']}`，{value['modified']}")
        else:
            lines.append(f"- {label}：暂无")
    lines.extend(["", "## 工作区", ""])
    git = data["git"]
    lines.append(f"- 未提交改动数量：{git.get('dirty_count', 0)}")
    for entry in git.get("entries", [])[:8]:
        lines.append(f"- `{entry}`")
    return "\n".join(lines).rstrip() + "\n"


def collect(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    flow_dir = Path(args.flow_dir)
    inbox = Path(args.inbox)
    automations_dir = Path(args.automations_dir)
    push_state = Path(args.push_state)
    feedback_queue = Path(args.feedback_queue)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if not flow_dir.is_absolute():
        flow_dir = ROOT / flow_dir
    if not inbox.is_absolute():
        inbox = ROOT / inbox
    if not push_state.is_absolute():
        push_state = ROOT / push_state
    if not feedback_queue.is_absolute():
        feedback_queue = ROOT / feedback_queue
    return {
        "generated_at": now_datetime(),
        "automations": load_automations(automations_dir),
        "inbox": inspect_inbox(inbox),
        "flow": inspect_flow(flow_dir),
        "feedback": inspect_feedback(feedback_queue),
        "push_state": inspect_push_state(push_state),
        "run_records": inspect_run_records(run_dir),
        "git": inspect_git(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect my-mind backend automation health.")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX))
    parser.add_argument("--flow-dir", default=str(DEFAULT_FLOW_DIR))
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--automations-dir", default=str(DEFAULT_AUTOMATIONS_DIR))
    parser.add_argument("--push-state", default=str(DEFAULT_PUSH_STATE))
    parser.add_argument("--feedback-queue", default=str(DEFAULT_FEEDBACK_QUEUE))
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--write", action="store_true", help="Write Markdown report into 85_运行记录.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = collect(args)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    report = build_report(data)
    if args.write:
        run_dir = Path(args.run_dir)
        if not run_dir.is_absolute():
            run_dir = ROOT / run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"后台总控巡检-{now_filename()}.md"
        path.write_text(report, encoding="utf-8")
        print(repo_rel(path))
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
