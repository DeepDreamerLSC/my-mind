#!/usr/bin/env python3
"""Inspect my-mind backend automation health and suggest safe next actions."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
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
DEFAULT_CONFIRMATION_QUEUE = DEFAULT_RUN_DIR / "待确认事项.jsonl"
DEFAULT_DASHBOARD_DIR = DEFAULT_RUN_DIR / "后台总览"
DEFAULT_DASHBOARD_DATA_FILE = DEFAULT_DASHBOARD_DIR / "飞书仪表盘数据.json"
DEFAULT_DASHBOARD_CSV_DIR = DEFAULT_DASHBOARD_DIR / "飞书仪表盘数据"
EXPECTED_PAUSED_AUTOMATIONS = {
    "收件箱待分拣巡检": "已并入 my-mind 后台总控日更",
    "前沿情报每日入箱": "已并入 my-mind 后台总控日更",
    "前台反馈与待确认消费": "已并入 my-mind 后台总控日更",
    "飞书仪表盘每日同步": "已并入 my-mind 后台总控日更",
}
WORKTREE_CATEGORY_LABELS = {
    "skill_code": "技能代码与规则",
    "inbox_state": "收件箱状态",
    "flow_views": "流转区视图",
    "dashboard_views": "后台固定视图",
    "timestamp_records": "时间戳运行记录",
    "feishu_pages": "飞书精选页",
    "run_state": "运行状态文件",
    "project_views": "项目管理视图",
    "index_views": "索引视图",
    "docs": "文档",
    "other": "其他",
}
WORKTREE_CATEGORY_ACTIONS = {
    "skill_code": "先验证，单独提交代码批次",
    "inbox_state": "作为入箱/解析状态证据批量提交",
    "flow_views": "作为可覆盖的流转视图随运行状态提交",
    "dashboard_views": "作为固定看板快照随运行状态提交",
    "timestamp_records": "作为当日运行证据批量提交，过旧重复草稿可归档",
    "feishu_pages": "作为手机阅读发布证据提交，重复草稿需归档",
    "run_state": "作为运行状态证据提交，注意检查本地配置和敏感字段",
    "project_views": "按项目管理批次提交",
    "index_views": "随对应知识/项目批次提交",
    "docs": "随设计或使用说明批次提交",
    "other": "人工判断归属后再提交",
}


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
    inactive_low_quality: list[dict[str, str]] = []
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
            item = {
                "path": repo_rel(path),
                "title": str(meta.get("标题") or path.stem),
                "status": status,
                "parse_status": parse_status,
                "quality": quality,
                "gate": str(meta.get("质量门禁") or ""),
            }
            gate = str(meta.get("质量门禁") or "")
            inactive = status in {"已归档", "可丢弃"} and any(keyword in gate for keyword in ["重复", "不再进入前台", "不再继续解析"])
            if inactive:
                inactive_low_quality.append(item)
            else:
                low_quality.append(item)
    return {
        "total": total,
        "status": dict(status_counter),
        "parse_status": dict(parse_counter),
        "quality": dict(quality_counter),
        "low_quality": low_quality[:12],
        "low_quality_count": len(low_quality),
        "inactive_low_quality_count": len(inactive_low_quality),
        "inactive_low_quality": inactive_low_quality[:12],
    }


def flow_count(path: Path) -> int:
    if not path.exists():
        return 0
    match = re.search(r"^- 条目数量：(\d+)", read_text(path), flags=re.M)
    if match:
        return int(match.group(1))
    return len(re.findall(r"^###\s+\d+\.", read_text(path), flags=re.M))


def split_queue_items(text: str) -> list[str]:
    parts = re.split(r"(?m)^###\s+\d+\.\s+", text)
    return [part.strip() for part in parts[1:] if part.strip()]


def field_value(block: str, field: str) -> str:
    match = re.search(rf"^- {re.escape(field)}：(.+)$", block, flags=re.M)
    return match.group(1).strip() if match else ""


def inspect_pending_distill_flow(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"total": 0, "ready": 0, "existing_candidate": 0, "deferred": 0, "stages": {}}

    text = read_text(path)
    total = flow_count(path)
    stages: Counter[str] = Counter()
    ready = 0
    existing_candidate = 0
    deferred = 0
    for block in split_queue_items(text):
        stage = field_value(block, "当前阶段") or "未知"
        stages[stage] += 1
        if "已有候选" in stage or "待确认" in stage:
            existing_candidate += 1
        elif any(keyword in stage for keyword in ["待补", "需核验", "待核验", "解析"]):
            deferred += 1
        else:
            ready += 1
    if not stages and total:
        ready = total
    return {
        "total": total,
        "ready": ready,
        "existing_candidate": existing_candidate,
        "deferred": deferred,
        "stages": dict(stages),
    }


def inspect_flow(flow_dir: Path) -> dict[str, Any]:
    pending_distill = inspect_pending_distill_flow(flow_dir / "30_待沉淀" / "收件箱待沉淀队列.md")
    return {
        "待读": flow_count(flow_dir / "10_待读" / "收件箱待读队列.md"),
        "待沉淀": pending_distill["total"],
        "待沉淀待消费": pending_distill["ready"],
        "已有候选待确认": pending_distill["existing_candidate"],
        "待补判断": pending_distill["deferred"],
        "待沉淀阶段分布": pending_distill["stages"],
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


def inspect_confirmations(queue_path: Path) -> dict[str, Any]:
    records = load_jsonl(queue_path)
    by_status = Counter(str(record.get("处理状态") or record.get("状态") or "未知") for record in records)
    pending: list[dict[str, Any]] = []
    pending_statuses = {"待确认", "待处理", "需要确认", "已有候选待确认"}
    for record in records:
        status = str(record.get("处理状态") or record.get("状态") or "")
        if status not in pending_statuses and not status.endswith("待确认"):
            continue
        candidate = record.get("候选文件") or ""
        existing = record.get("已有候选")
        if not candidate and isinstance(existing, list) and existing:
            candidate = existing[0]
        pending.append(
            {
                "title": record.get("标题") or "未命名待确认事项",
                "priority": record.get("优先级") or "中",
                "type": record.get("类型") or "未分类",
                "status": status,
                "source": record.get("来源文件") or "",
                "candidate": candidate,
                "replies": record.get("可回复") or [],
            }
        )
    return {
        "total": len(records),
        "by_status": dict(by_status),
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
        "latest_parse_repair": latest_file("解析质量修复-*.md", run_dir),
        "latest_push": latest_file("前台推送-*.md", run_dir),
        "latest_feedback": latest_file("反馈消费-*.md", run_dir),
        "latest_feishu_publish": latest_file("飞书精选页/索引/*.md", run_dir) or latest_file("飞书阅读页/飞书阅读页-*.md", run_dir),
        "latest_feishu_sync": latest_file("飞书知识库同步页/*.md", run_dir),
    }


def inspect_git() -> dict[str, Any]:
    try:
        result = subprocess.run(["git", "status", "--porcelain=v1", "-z"], cwd=ROOT, text=True, capture_output=True, check=False)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "dirty_count": 0, "entries": [], "categories": {}, "category_samples": [], "recommended_batches": []}
    parsed_entries = parse_git_status_z(result.stdout)
    categories = summarize_worktree_entries(parsed_entries)
    display_entries = [f"{entry['status']} {entry['path']}" for entry in parsed_entries]
    return {
        "dirty_count": len(parsed_entries),
        "entries": display_entries[:12],
        "raw_entries": parsed_entries,
        **categories,
    }


def parse_git_status_z(raw_output: str) -> list[dict[str, str]]:
    chunks = [chunk for chunk in raw_output.split("\0") if chunk]
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        if len(chunk) < 4:
            index += 1
            continue
        status = chunk[:2]
        path = chunk[3:]
        entry = {"status": status, "path": path, "category": classify_worktree_path(path)}
        entries.append(entry)
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            index += 2
        else:
            index += 1
    return entries


def classify_worktree_path(path: str) -> str:
    if path.startswith(".codex/skills/"):
        return "skill_code"
    if path.startswith("00_收件箱/"):
        return "inbox_state"
    if path.startswith("05_流转区/"):
        return "flow_views"
    if path.startswith("85_运行记录/后台总览/"):
        return "dashboard_views"
    if path.startswith("85_运行记录/飞书精选页/"):
        return "feishu_pages"
    if path.startswith("85_运行记录/") and re.search(r"-\d{4}-\d{2}-\d{2}", path):
        return "timestamp_records"
    if path.startswith("85_运行记录/"):
        return "run_state"
    if path.startswith("10_项目/"):
        return "project_views"
    if path.startswith("15_索引/"):
        return "index_views"
    if path == "README.md" or path.startswith("design/") or path.endswith("/SKILL.md"):
        return "docs"
    return "other"


def summarize_worktree_entries(entries: list[dict[str, str]]) -> dict[str, Any]:
    category_counter = Counter(entry["category"] for entry in entries)
    samples_by_category: dict[str, list[str]] = {}
    for entry in entries:
        samples_by_category.setdefault(entry["category"], [])
        if len(samples_by_category[entry["category"]]) < 3:
            samples_by_category[entry["category"]].append(entry["path"])
    category_samples = [
        {
            "category": category,
            "label": WORKTREE_CATEGORY_LABELS.get(category, category),
            "count": count,
            "samples": samples_by_category.get(category, []),
            "action": WORKTREE_CATEGORY_ACTIONS.get(category, WORKTREE_CATEGORY_ACTIONS["other"]),
        }
        for category, count in sorted(category_counter.items(), key=lambda item: (-item[1], item[0]))
    ]
    recommended_batches = [
        f"{sample['label']}：{sample['count']} 项，{sample['action']}"
        for sample in category_samples
    ]
    return {
        "categories": dict(category_counter),
        "category_samples": category_samples,
        "recommended_batches": recommended_batches,
    }


def automation_name(automation: dict[str, Any]) -> str:
    return str(automation.get("name") or automation.get("id") or "未命名自动化")


def expected_pause_reason(automation: dict[str, Any]) -> str:
    status = str(automation.get("status") or "").upper()
    if status == "ACTIVE":
        return ""
    return EXPECTED_PAUSED_AUTOMATIONS.get(automation_name(automation), "")


def record_age_hours(record: dict[str, Any] | None) -> float | None:
    if not record:
        return None
    raw = str(record.get("modified") or "")
    try:
        modified = dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
    return (dt.datetime.now(TZ) - modified).total_seconds() / 3600


def build_digest(data: dict[str, Any]) -> dict[str, Any]:
    inbox = data["inbox"]
    flow = data["flow"]
    feedback = data["feedback"]
    confirmations = data["confirmations"]
    push_state = data["push_state"]
    automations = data["automations"]
    run_records = data["run_records"]
    git = data["git"]

    automation_errors = [automation for automation in automations if automation.get("error")]
    inactive_automations = [automation for automation in automations if str(automation.get("status") or "").upper() != "ACTIVE"]
    planned_paused_automations = [automation for automation in inactive_automations if expected_pause_reason(automation)]
    unexpected_inactive_automations = [automation for automation in inactive_automations if not expected_pause_reason(automation)]
    stale_runs: list[str] = []
    stale_thresholds = {
        "latest_triage": 8,
        "latest_parse_repair": 8,
        "latest_feedback": 8,
        "latest_push": 30,
        "latest_feishu_publish": 30,
    }
    for key, threshold in stale_thresholds.items():
        age = record_age_hours(run_records.get(key))
        if age is None:
            stale_runs.append(key.replace("latest_", ""))
        elif age > threshold:
            stale_runs.append(f"{key.replace('latest_', '')} 已 {age:.1f} 小时未刷新")

    user_items: list[str] = []
    codex_items: list[str] = []
    openclaw_items: list[str] = []
    risks: list[str] = []

    if confirmations["pending_count"]:
        user_items.append(f"{confirmations['pending_count']} 条候选知识等待确认转正、继续核验、调整分类或跳过。")
        openclaw_items.append(f"提醒用户处理 {confirmations['pending_count']} 条待确认候选，优先展示标题、类型、候选文件和可回复动作。")
    if push_state.get("waiting_feedback"):
        user_items.append(f"{push_state.get('waiting_feedback', 0)} 条前台内容已推送但仍未反馈，可由 OpenClaw 分批展示，不要求一次处理完。")
        openclaw_items.append("用飞书精选链接或待确认队列做前台触达，不要让用户翻自动化日志。")
    if not user_items:
        user_items.append("暂无必须由你立即处理的后台事项。")

    if feedback["pending_count"]:
        codex_items.append(f"消费 {feedback['pending_count']} 条前台反馈队列，回写阅读思考或执行待确认动作。")
    if inbox["low_quality_count"]:
        codex_items.append(f"继续处理 {inbox['low_quality_count']} 条低质量/需核验解析，优先运行解析质量修复。")
    if flow.get("待沉淀待消费", 0):
        codex_items.append(f"待沉淀队列有 {flow['待沉淀待消费']} 条可继续自动生成候选草稿并进入待确认。")
    elif flow.get("已有候选待确认", 0):
        codex_items.append(f"待沉淀队列中 {flow['已有候选待确认']} 条已有候选，下一步是 OpenClaw 提醒确认，不再重复消费。")
    if flow.get("待补判断", 0):
        codex_items.append(f"待沉淀队列中 {flow['待补判断']} 条仍需补解析或补判断，先走核验门禁。")
    if flow.get("待核验", 0):
        codex_items.append(f"待核验队列有 {flow['待核验']} 条，避免直接进入前台精选。")
    if git.get("dirty_count", 0):
        first_batch = ""
        if git.get("recommended_batches"):
            first_batch = f"优先看：{git['recommended_batches'][0]}"
        codex_items.append(f"按运行产物治理分组收敛 {git['dirty_count']} 个未提交改动。{first_batch}".rstrip())
    if not codex_items:
        codex_items.append("后台暂无需要立即自动处理的阻塞项。")

    if automation_errors:
        risks.append(f"{len(automation_errors)} 个自动化配置读取失败。")
    if unexpected_inactive_automations:
        names = "、".join(automation_name(item) for item in unexpected_inactive_automations[:4])
        risks.append(f"{len(unexpected_inactive_automations)} 个自动化意外未激活：{names}")
    if push_state.get("error"):
        risks.append(str(push_state["error"]))
    if stale_runs:
        risks.append("最近运行记录可能过期：" + "；".join(stale_runs[:5]))
    if git.get("dirty_count", 0):
        category_text = "；".join(
            f"{sample['label']} {sample['count']}"
            for sample in git.get("category_samples", [])[:4]
        )
        detail = f"：{category_text}" if category_text else ""
        risks.append(f"工作区有 {git.get('dirty_count', 0)} 个未提交改动{detail}。")
    if inbox["low_quality_count"]:
        risks.append(f"{inbox['low_quality_count']} 条内容质量仍需核验，推送和转正前要继续门禁。")
    if not risks:
        risks.append("未发现明显异常。")

    if automation_errors or push_state.get("error"):
        level = "红色"
        summary = "存在配置或状态文件异常，需要 Codex 优先排查。"
    elif confirmations["pending_count"] or feedback["pending_count"] or inbox["low_quality_count"] or unexpected_inactive_automations:
        level = "黄色"
        summary = "后台链路可运行，但存在待确认、待核验或待消费事项。"
    else:
        level = "绿色"
        summary = "后台链路运行平稳，暂无明显阻塞。"

    return {
        "level": level,
        "summary": summary,
        "user_items": user_items,
        "codex_items": codex_items,
        "openclaw_items": openclaw_items or ["无需主动打扰用户；如用户询问，再展示当前后台状态摘要。"],
        "risks": risks,
        "automation": {
            "total": len(automations),
            "active": sum(1 for automation in automations if str(automation.get("status") or "").upper() == "ACTIVE"),
            "paused": len(inactive_automations),
            "planned_paused": len(planned_paused_automations),
            "unexpected_inactive": len(unexpected_inactive_automations),
            "errors": len(automation_errors),
            "planned_paused_names": [automation_name(item) for item in planned_paused_automations],
        },
    }


def append_list(lines: list[str], values: list[str]) -> None:
    if not values:
        lines.append("- 无")
        return
    lines.extend(f"- {value}" for value in values)


def build_dashboard(data: dict[str, Any]) -> str:
    digest = data["digest"]
    flow = data["flow"]
    inbox = data["inbox"]
    feedback = data["feedback"]
    confirmations = data["confirmations"]
    push_state = data["push_state"]
    git = data["git"]

    lines = [
        "# 当前后台状态",
        "",
        f"- 更新时间：{data['generated_at']}",
        f"- 状态：{digest['level']}，{digest['summary']}",
        f"- 自动化：{digest['automation']['active']} 个启用 / {digest['automation']['total']} 个总数（计划暂停 {digest['automation']['planned_paused']}，异常暂停 {digest['automation']['unexpected_inactive']}）",
        "",
        "## 自动化节奏",
        "",
    ]
    planned_names = digest["automation"].get("planned_paused_names") or []
    if planned_names:
        lines.append(f"- 计划暂停：{'、'.join(planned_names)}，均已并入 `my-mind 后台总控日更`。")
    else:
        lines.append("- 计划暂停：0 个。")
    if digest["automation"]["unexpected_inactive"]:
        lines.append(f"- 异常暂停：{digest['automation']['unexpected_inactive']} 个，需要检查自动化配置。")
    else:
        lines.append("- 异常暂停：0 个。")
    lines.extend([
        "",
        "## 工作区运行产物治理",
        "",
        f"- 未提交改动：{git.get('dirty_count', 0)} 个",
    ])
    if git.get("category_samples"):
        for sample in git["category_samples"][:6]:
            examples = "、".join(f"`{path}`" for path in sample.get("samples", [])[:2])
            suffix = f"；例：{examples}" if examples else ""
            lines.append(f"- {sample['label']}：{sample['count']} 项，{sample['action']}{suffix}")
    else:
        lines.append("- 工作区干净，无需治理。")
    lines.extend([
        "",
        "## 需要你处理",
        "",
    ])
    append_list(lines, digest["user_items"])
    lines.extend(["", "## Codex 后台应处理", ""])
    append_list(lines, digest["codex_items"])
    lines.extend(["", "## OpenClaw 前台应提醒", ""])
    append_list(lines, digest["openclaw_items"])
    lines.extend(["", "## 异常与风险", ""])
    append_list(lines, digest["risks"])
    lines.extend(
        [
            "",
            "## 数字总览",
            "",
            f"- 收件箱：{inbox['total']} 条；低质量/需核验：{inbox['low_quality_count']} 条",
            f"- 流转区：待读 {flow['待读']}，待沉淀 {flow['待沉淀']}（可消费 {flow['待沉淀待消费']}，已有候选 {flow['已有候选待确认']}，待补判断 {flow['待补判断']}），待核验 {flow['待核验']}",
            f"- 待确认候选：{confirmations['pending_count']} 条",
            f"- 前台反馈待消费：{feedback['pending_count']} 条",
            f"- 已推送未反馈：{push_state.get('waiting_feedback', 0)} 条；24 小时冷却中：{push_state.get('cooling', 0)} 条",
            "",
            "## 待确认候选预览",
            "",
        ]
    )
    if confirmations["pending"]:
        for index, item in enumerate(confirmations["pending"][:5], start=1):
            candidate = item.get("candidate") or "未定位候选文件"
            replies = item.get("replies") or ["确认转正", "继续核验", "调整分类", "跳过"]
            if isinstance(replies, list):
                reply_text = " / ".join(str(reply) for reply in replies)
            else:
                reply_text = str(replies)
            lines.append(f"{index}. {item['title']}（{item['type']}，{item['priority']}）")
            lines.append(f"   - 候选：`{candidate}`")
            lines.append(f"   - 可回复：{reply_text}")
    else:
        lines.append("- 暂无待确认候选。")

    lines.extend(["", "## 最近运行", ""])
    for key, value in data["run_records"].items():
        label = key.replace("latest_", "")
        if value:
            lines.append(f"- {label}：`{value['path']}`，{value['modified']}")
        else:
            lines.append(f"- {label}：暂无")
    lines.extend(
        [
            "",
            "## 查看完整证据",
            "",
            "- 详细巡检报告：`85_运行记录/后台总控巡检-*.md`",
            "- 前台提醒稿：`85_运行记录/后台总览/OpenClaw待提醒.md`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_openclaw_brief(data: dict[str, Any]) -> str:
    digest = data["digest"]
    confirmations = data["confirmations"]
    push_state = data["push_state"]
    lines = [
        "# OpenClaw待提醒",
        "",
        f"- 更新时间：{data['generated_at']}",
        f"- 后台状态：{digest['level']}，{digest['summary']}",
        "",
        "## 转发原则",
        "",
        "- 只提醒用户需要处理或确认的事项。",
        "- 不转发自动化日志、脚本输出和完整巡检报告。",
        "- 优先给飞书知识库链接或候选标题；没有链接时再给本地路径。",
        "",
        "## 建议提醒",
        "",
    ]
    append_list(lines, digest["openclaw_items"])
    lines.extend(["", "## 待确认候选", ""])
    if confirmations["pending"]:
        for index, item in enumerate(confirmations["pending"][:5], start=1):
            candidate = item.get("candidate") or "未定位候选文件"
            lines.append(f"{index}. {item['title']}｜{item['type']}｜{item['priority']}")
            lines.append(f"   - 候选：`{candidate}`")
            lines.append("   - 可回复：确认转正 / 继续核验 / 调整分类 / 跳过")
    else:
        lines.append("- 暂无待确认候选。")
    lines.extend(
        [
            "",
            "## 前台反馈状态",
            "",
            f"- 已推送未反馈：{push_state.get('waiting_feedback', 0)} 条",
            f"- 24 小时冷却中：{push_state.get('cooling', 0)} 条",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def dashboard_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def numeric(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def status_score(level: str) -> int:
    return {"绿色": 1, "黄色": 2, "红色": 3}.get(level, 0)


def short_record_key(prefix: str, raw_value: Any) -> str:
    raw_text = dashboard_value(raw_value)
    digest = hashlib.sha1(raw_text.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def compact_rrule(rrule: str) -> str:
    if "FREQ=HOURLY;INTERVAL=6" in rrule:
        return "每 6 小时"
    if "FREQ=DAILY" in rrule:
        hour = re.search(r"BYHOUR=(\d+)", rrule)
        minute = re.search(r"BYMINUTE=(\d+)", rrule)
        if hour:
            return f"每天 {int(hour.group(1)):02d}:{int(minute.group(1)) if minute else 0:02d}"
        return "每天"
    return rrule


def build_dashboard_tables(data: dict[str, Any]) -> dict[str, Any]:
    generated_at = data["generated_at"]
    snapshot_key = dt.datetime.now(TZ).strftime("%Y%m%d%H%M")
    digest = data["digest"]
    inbox = data["inbox"]
    flow = data["flow"]
    feedback = data["feedback"]
    confirmations = data["confirmations"]
    push_state = data["push_state"]
    git = data["git"]
    rows: dict[str, list[dict[str, Any]]] = {
        "metrics": [],
        "metric_history": [],
        "actions": [],
        "automations": [],
        "run_records": [],
        "quality_items": [],
        "confirmations": [],
        "push_items": [],
        "flow": [],
    }

    def add_metric(name: str, value: Any, *, category: str, unit: str = "条", text: str = "", note: str = "") -> None:
        numeric_value = numeric(value)
        metric_row = {
            "记录键": f"metric:{name}",
            "生成时间": generated_at,
            "指标": name,
            "数值": numeric_value,
            "文本值": text or dashboard_value(value),
            "单位": unit,
            "类别": category,
            "状态等级": digest["level"],
            "说明": note,
        }
        rows["metrics"].append(metric_row)
        history_row = dict(metric_row)
        history_row["记录键"] = f"history:{snapshot_key}:{name}"
        history_row["快照键"] = snapshot_key
        rows["metric_history"].append(history_row)

    add_metric("后台状态指数", status_score(digest["level"]), category="系统", unit="级", text=digest["level"], note=digest["summary"])
    add_metric("自动化总数", digest["automation"]["total"], category="自动化")
    add_metric("启用自动化", digest["automation"]["active"], category="自动化")
    add_metric("暂停自动化", digest["automation"]["paused"], category="自动化")
    add_metric("计划暂停自动化", digest["automation"]["planned_paused"], category="自动化")
    add_metric("异常暂停自动化", digest["automation"]["unexpected_inactive"], category="自动化")
    add_metric("自动化错误", digest["automation"]["errors"], category="自动化")
    add_metric("收件箱总数", inbox["total"], category="收件箱")
    add_metric("低质量解析", inbox["low_quality_count"], category="收件箱")
    add_metric("待读", flow["待读"], category="流转区")
    add_metric("待沉淀", flow["待沉淀"], category="流转区")
    add_metric("待沉淀可消费", flow["待沉淀待消费"], category="流转区")
    add_metric("已有候选待确认", flow["已有候选待确认"], category="流转区")
    add_metric("待补判断", flow["待补判断"], category="流转区")
    add_metric("待核验", flow["待核验"], category="流转区")
    add_metric("待确认候选", confirmations["pending_count"], category="确认")
    add_metric("前台反馈待消费", feedback["pending_count"], category="反馈")
    add_metric("已推送未反馈", push_state.get("waiting_feedback", 0), category="前台")
    add_metric("24小时冷却中", push_state.get("cooling", 0), category="前台")
    add_metric("工作区未提交改动", git.get("dirty_count", 0), category="工程")

    for owner, values in [
        ("用户", digest["user_items"]),
        ("Codex", digest["codex_items"]),
        ("OpenClaw", digest["openclaw_items"]),
    ]:
        for index, value in enumerate(values, start=1):
            raw_key = f"{owner}:{index}:{value}"
            rows["actions"].append(
                {
                    "记录键": short_record_key("action", raw_key),
                    "原始键": raw_key,
                    "生成时间": generated_at,
                    "责任方": owner,
                    "事项": value,
                    "状态": digest["level"],
                    "优先级": "高" if owner == "用户" and digest["level"] != "绿色" else "中",
                    "来源": "backend-control",
                }
            )

    for automation in data["automations"]:
        pause_reason = expected_pause_reason(automation)
        status = str(automation.get("status") or "UNKNOWN")
        plan_status = "按计划暂停" if pause_reason else ("启用" if status.upper() == "ACTIVE" else "异常未激活")
        rows["automations"].append(
            {
                "记录键": f"automation:{automation_name(automation)}",
                "生成时间": generated_at,
                "自动化": automation_name(automation),
                "状态": status,
                "计划状态": plan_status,
                "暂停原因": pause_reason,
                "频率": compact_rrule(str(automation.get("rrule") or "")),
                "原始频率": automation.get("rrule") or "",
                "模型": automation.get("model") or "",
                "推理强度": automation.get("reasoning_effort") or "",
                "执行环境": automation.get("execution_environment") or "",
                "配置路径": automation.get("path") or "",
                "错误": automation.get("error") or "",
            }
        )

    for key, value in data["run_records"].items():
        label = key.replace("latest_", "")
        rows["run_records"].append(
            {
                "记录键": f"run:{label}",
                "生成时间": generated_at,
                "记录类型": label,
                "路径": value.get("path") if value else "",
                "修改时间": value.get("modified") if value else "",
                "距现在小时": round(record_age_hours(value) or 0, 2) if value else "",
                "状态": "已找到" if value else "缺失",
            }
        )

    for item in inbox["low_quality"]:
        raw_key = item["path"]
        rows["quality_items"].append(
            {
                "记录键": short_record_key("quality", raw_key),
                "原始键": raw_key,
                "生成时间": generated_at,
                "标题": item.get("title") or "",
                "来源文件": item.get("path") or "",
                "处理状态": item.get("status") or "",
                "解析状态": item.get("parse_status") or "",
                "内容质量": item.get("quality") or "",
                "质量门禁": item.get("gate") or "",
            }
        )

    for item in confirmations["pending"]:
        raw_key = item.get("candidate") or item.get("source") or item.get("title")
        rows["confirmations"].append(
            {
                "记录键": short_record_key("confirm", raw_key),
                "原始键": raw_key,
                "生成时间": generated_at,
                "标题": item.get("title") or "",
                "类型": item.get("type") or "",
                "优先级": item.get("priority") or "",
                "状态": item.get("status") or "",
                "来源文件": item.get("source") or "",
                "候选文件": item.get("candidate") or "",
                "可回复": " / ".join(str(reply) for reply in item.get("replies") or []),
            }
        )

    for item in push_state.get("recent", []):
        raw_key = item.get("item_id") or item.get("title")
        rows["push_items"].append(
            {
                "记录键": short_record_key("push", raw_key),
                "原始键": raw_key,
                "生成时间": generated_at,
                "标题": item.get("title") or "",
                "来源文件": item.get("item_id") or "",
                "最近推送时间": item.get("last_pushed_at") or "",
                "反馈状态": item.get("feedback_status") or "",
            }
        )

    for name, count in flow.items():
        if isinstance(count, dict):
            for stage, stage_count in count.items():
                rows["flow"].append(
                    {
                        "记录键": f"flow:待沉淀阶段:{stage}",
                        "生成时间": generated_at,
                        "队列": "待沉淀阶段",
                        "阶段": stage,
                        "数量": stage_count,
                        "状态等级": digest["level"],
                    }
                )
            continue
        rows["flow"].append(
            {
                "记录键": f"flow:{name}",
                "生成时间": generated_at,
                "队列": name,
                "数量": count,
                "状态等级": digest["level"],
            }
        )

    return {
        "generated_at": generated_at,
        "snapshot_key": snapshot_key,
        "source": "backend-control",
        "tables": {
            "metrics": {"display_name": "后台指标", "primary_key": "记录键", "rows": rows["metrics"]},
            "metric_history": {"display_name": "指标历史", "primary_key": "记录键", "rows": rows["metric_history"]},
            "actions": {"display_name": "行动队列", "primary_key": "记录键", "rows": rows["actions"]},
            "automations": {"display_name": "自动化状态", "primary_key": "记录键", "rows": rows["automations"]},
            "run_records": {"display_name": "运行记录", "primary_key": "记录键", "rows": rows["run_records"]},
            "quality_items": {"display_name": "解析质量", "primary_key": "记录键", "rows": rows["quality_items"]},
            "confirmations": {"display_name": "待确认候选", "primary_key": "记录键", "rows": rows["confirmations"]},
            "push_items": {"display_name": "前台推送", "primary_key": "记录键", "rows": rows["push_items"]},
            "flow": {"display_name": "流转队列", "primary_key": "记录键", "rows": rows["flow"]},
        },
    }


def csv_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = csv_fieldnames(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: dashboard_value(row.get(key, "")) for key in fieldnames})


def write_dashboard_data_exports(data: dict[str, Any], dashboard_data_file: Path, csv_dir: Path) -> list[Path]:
    dashboard_data_file.parent.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)
    payload = build_dashboard_tables(data)
    dashboard_data_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    written = [dashboard_data_file]
    for key, table in payload["tables"].items():
        csv_path = csv_dir / f"{table['display_name']}.csv"
        write_csv(csv_path, list(table.get("rows") or []))
        written.append(csv_path)
    return written


def build_report(data: dict[str, Any]) -> str:
    flow = data["flow"]
    inbox = data["inbox"]
    feedback = data["feedback"]
    confirmations = data["confirmations"]
    push_state = data["push_state"]
    actions: list[str] = []
    if feedback["pending_count"]:
        actions.append("优先消费前台反馈队列，把用户短反馈回写到来源笔记。")
    if inbox["low_quality_count"]:
        actions.append("低质量或未完整解析条目不进入前台推送，先补 OCR、字幕或转写。")
    if push_state.get("waiting_feedback"):
        actions.append("前台推送进入冷却，OpenClaw 不重复催同一批条目，除非用户主动要求。")
    if flow.get("待沉淀待消费", 0):
        actions.append("待沉淀队列已有可消费内容，可自动生成候选草稿。")
    elif flow.get("已有候选待确认", 0):
        actions.append("待沉淀队列主要是已有候选，OpenClaw 负责提醒确认，Codex 不重复生成候选。")
    if flow.get("待补判断", 0):
        actions.append("待补判断条目先补解析或人工判断，不直接进入沉淀消费。")
    if not actions:
        actions.append("后台链路暂无明显阻塞，保持现有自动化频率。")

    lines = [
        "# my-mind 后台总控巡检",
        "",
        "## 结论",
        "",
        f"- 巡检时间：{data['generated_at']}",
        f"- 收件箱总数：{inbox['total']}",
        f"- 流转区：待读 {flow['待读']}，待沉淀 {flow['待沉淀']}（可消费 {flow['待沉淀待消费']}，已有候选 {flow['已有候选待确认']}，待补判断 {flow['待补判断']}），待核验 {flow['待核验']}",
        f"- 待处理前台反馈：{feedback['pending_count']}",
        f"- 待确认候选：{confirmations['pending_count']}",
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
        reason = expected_pause_reason(automation)
        suffix = f"，计划暂停：{reason}" if reason else ""
        lines.append(f"- {name}：{status}，{rrule}{suffix}")
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
    lines.extend(["", "## 待确认候选", ""])
    lines.append(f"- 状态分布：{json.dumps(confirmations['by_status'], ensure_ascii=False)}")
    if confirmations["pending"]:
        lines.extend(["", "### 待确认预览", ""])
        for item in confirmations["pending"]:
            candidate = item.get("candidate") or "未定位候选文件"
            lines.append(f"- {item['title']}：{item['type']} / {item['priority']}，候选 `{candidate}`")
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
    if git.get("category_samples"):
        lines.extend(["", "### 治理分组", ""])
        for sample in git["category_samples"]:
            examples = "、".join(f"`{path}`" for path in sample.get("samples", [])[:3])
            suffix = f"；例：{examples}" if examples else ""
            lines.append(f"- {sample['label']}：{sample['count']} 项。{sample['action']}{suffix}")
    if git.get("recommended_batches"):
        lines.extend(["", "### 建议提交批次", ""])
        for batch in git["recommended_batches"][:8]:
            lines.append(f"- {batch}")
    lines.extend(["", "### 原始状态样例", ""])
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
    confirmation_queue = Path(args.confirmation_queue)
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
    if not confirmation_queue.is_absolute():
        confirmation_queue = ROOT / confirmation_queue
    data = {
        "generated_at": now_datetime(),
        "automations": load_automations(automations_dir),
        "inbox": inspect_inbox(inbox),
        "flow": inspect_flow(flow_dir),
        "feedback": inspect_feedback(feedback_queue),
        "confirmations": inspect_confirmations(confirmation_queue),
        "push_state": inspect_push_state(push_state),
        "run_records": inspect_run_records(run_dir),
        "git": inspect_git(),
    }
    data["digest"] = build_digest(data)
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect my-mind backend automation health.")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX))
    parser.add_argument("--flow-dir", default=str(DEFAULT_FLOW_DIR))
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--automations-dir", default=str(DEFAULT_AUTOMATIONS_DIR))
    parser.add_argument("--push-state", default=str(DEFAULT_PUSH_STATE))
    parser.add_argument("--feedback-queue", default=str(DEFAULT_FEEDBACK_QUEUE))
    parser.add_argument("--confirmation-queue", default=str(DEFAULT_CONFIRMATION_QUEUE))
    parser.add_argument("--dashboard-dir", default=str(DEFAULT_DASHBOARD_DIR))
    parser.add_argument("--dashboard-data-file", default=str(DEFAULT_DASHBOARD_DATA_FILE))
    parser.add_argument("--dashboard-csv-dir", default=str(DEFAULT_DASHBOARD_CSV_DIR))
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--view", choices=["report", "dashboard", "openclaw"], default="report")
    parser.add_argument("--write", action="store_true", help="Write Markdown report and fixed dashboard files into 85_运行记录.")
    parser.add_argument("--export-dashboard-data", action="store_true", help="Write structured JSON and CSV rows for Feishu Base dashboards.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = collect(args)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.export_dashboard_data and not args.write:
        dashboard_data_file = Path(args.dashboard_data_file)
        csv_dir = Path(args.dashboard_csv_dir)
        if not dashboard_data_file.is_absolute():
            dashboard_data_file = ROOT / dashboard_data_file
        if not csv_dir.is_absolute():
            csv_dir = ROOT / csv_dir
        written = write_dashboard_data_exports(data, dashboard_data_file, csv_dir)
        for path in written:
            print(repo_rel(path))
        return 0

    if args.view == "dashboard":
        report = build_dashboard(data)
    elif args.view == "openclaw":
        report = build_openclaw_brief(data)
    else:
        report = build_report(data)
    if args.write:
        run_dir = Path(args.run_dir)
        if not run_dir.is_absolute():
            run_dir = ROOT / run_dir
        dashboard_dir = Path(args.dashboard_dir)
        if not dashboard_dir.is_absolute():
            dashboard_dir = ROOT / dashboard_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        dashboard_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"后台总控巡检-{now_filename()}.md"
        path.write_text(build_report(data), encoding="utf-8")
        dashboard_path = dashboard_dir / "当前后台状态.md"
        openclaw_path = dashboard_dir / "OpenClaw待提醒.md"
        dashboard_path.write_text(build_dashboard(data), encoding="utf-8")
        openclaw_path.write_text(build_openclaw_brief(data), encoding="utf-8")
        dashboard_data_file = Path(args.dashboard_data_file)
        csv_dir = Path(args.dashboard_csv_dir)
        if not dashboard_data_file.is_absolute():
            dashboard_data_file = ROOT / dashboard_data_file
        if not csv_dir.is_absolute():
            csv_dir = ROOT / csv_dir
        data_paths = write_dashboard_data_exports(data, dashboard_data_file, csv_dir)
        print(repo_rel(path))
        print(repo_rel(dashboard_path))
        print(repo_rel(openclaw_path))
        for data_path in data_paths:
            print(repo_rel(data_path))
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
