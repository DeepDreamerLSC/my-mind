#!/usr/bin/env python3
"""Plan safe cleanup batches for my-mind run-state artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
RUN_DIR = ROOT / "85_运行记录"
DASHBOARD_DIR = RUN_DIR / "后台总览"

CATEGORY_LABELS = {
    "skill_code": "技能代码与规则",
    "docs": "文档与设计",
    "inbox_state": "收件箱状态",
    "flow_views": "流转区视图",
    "dashboard_views": "后台固定视图",
    "timestamp_records": "时间戳运行记录",
    "feishu_pages": "飞书精选页",
    "run_state": "运行状态文件",
    "project_views": "项目管理视图",
    "index_views": "索引视图",
    "local_config": "本地配置",
    "other": "其他",
}

CATEGORY_ACTIONS = {
    "skill_code": "先跑脚本验证，再单独提交代码批次。",
    "docs": "随对应规则或设计变更提交。",
    "inbox_state": "作为入箱/解析状态证据批量提交；确认无敏感原文后再提交。",
    "flow_views": "作为可覆盖流转视图随运行状态提交。",
    "dashboard_views": "作为固定看板快照随运行状态提交。",
    "timestamp_records": "作为运行证据批量提交；重复草稿可另行归档但本脚本不删除。",
    "feishu_pages": "作为手机阅读发布证据提交；重复草稿需人工判断。",
    "run_state": "作为运行状态证据提交；先检查是否包含 token、space_id 或本地路径敏感信息。",
    "project_views": "按项目管理批次提交，避免和自动化日志混在一起。",
    "index_views": "随对应知识/项目视图批次提交。",
    "local_config": "默认不提交；只保留在本机。",
    "other": "人工判断归属后再提交。",
}

SENSITIVE_PATTERNS = (
    re.compile(r"\.env($|[./])"),
    re.compile(r"\.local\.json$"),
    re.compile(r"(token|secret|password|credential|cookie)", re.I),
)


@dataclass(frozen=True)
class WorktreeEntry:
    status: str
    path: str
    category: str


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def now_datetime() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return now().strftime("%Y-%m-%d-%H%M")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def run_git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    return result.stdout


def classify_path(path: str) -> str:
    clean = path.strip('"')
    if clean.endswith(".local.json") or "/.local." in clean or clean.startswith(".env"):
        return "local_config"
    if clean.startswith(".codex/skills/"):
        return "skill_code"
    if clean == "README.md" or clean.startswith("design/") or clean.endswith("/SKILL.md"):
        return "docs"
    if clean.startswith("00_收件箱/"):
        return "inbox_state"
    if clean.startswith("05_流转区/"):
        return "flow_views"
    if clean.startswith("85_运行记录/后台总览/"):
        return "dashboard_views"
    if clean.startswith("85_运行记录/飞书精选页/"):
        return "feishu_pages"
    if clean.startswith("85_运行记录/") and re.search(r"-\d{4}-\d{2}-\d{2}", clean):
        return "timestamp_records"
    if clean.startswith("85_运行记录/"):
        return "run_state"
    if clean.startswith("10_项目/"):
        return "project_views"
    if clean.startswith("15_索引/"):
        return "index_views"
    return "other"


def parse_git_status(raw: str) -> list[WorktreeEntry]:
    chunks = [chunk for chunk in raw.split("\0") if chunk]
    entries: list[WorktreeEntry] = []
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        if len(chunk) < 4:
            index += 1
            continue
        status = chunk[:2]
        path = chunk[3:]
        entries.append(WorktreeEntry(status=status, path=path, category=classify_path(path)))
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            index += 2
        else:
            index += 1
    return entries


def is_sensitive(path: str) -> bool:
    return any(pattern.search(path) for pattern in SENSITIVE_PATTERNS)


def short_key(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def build_plan(entries: list[WorktreeEntry], sample_limit: int) -> dict[str, Any]:
    counter = Counter(entry.category for entry in entries)
    samples: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        bucket = samples.setdefault(entry.category, [])
        if len(bucket) < sample_limit:
            bucket.append({"status": entry.status, "path": entry.path})

    groups = []
    for category, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        groups.append(
            {
                "category": category,
                "label": CATEGORY_LABELS.get(category, category),
                "count": count,
                "samples": samples.get(category, []),
                "action": CATEGORY_ACTIONS.get(category, CATEGORY_ACTIONS["other"]),
                "batch_key": f"batch:{category}:{short_key(category)}",
            }
        )

    sensitive = [
        {"status": entry.status, "path": entry.path, "category": entry.category}
        for entry in entries
        if is_sensitive(entry.path)
    ]
    batches = [
        f"{group['label']}：{group['count']} 项，{group['action']}"
        for group in groups
        if group["category"] != "local_config"
    ]
    if any(group["category"] == "local_config" for group in groups):
        batches.append("本地配置：默认不纳入提交，只保留风险提醒。")
    return {
        "generated_at": now_datetime(),
        "dirty_count": len(entries),
        "category_counts": dict(counter),
        "groups": groups,
        "sensitive_candidates": sensitive[:20],
        "recommended_batches": batches,
        "status": "clean" if not entries else ("needs_attention" if sensitive else "dirty"),
    }


def render_report(plan: dict[str, Any], *, mode: str) -> str:
    lines = [
        "# 运行产物治理",
        "",
        "## 总览",
        "",
        f"- 时间：{plan['generated_at']}",
        f"- 模式：{mode}",
        f"- 未提交改动：{plan['dirty_count']} 项",
        f"- 状态：{plan['status']}",
        "",
        "## 分组",
        "",
    ]
    groups = list(plan.get("groups") or [])
    if not groups:
        lines.append("- 工作区干净，无需治理。")
    for group in groups:
        lines.extend(
            [
                f"### {group['label']}",
                "",
                f"- 数量：{group['count']}",
                f"- 建议：{group['action']}",
                f"- 批次键：`{group['batch_key']}`",
                "- 样例：",
            ]
        )
        for sample in group.get("samples") or []:
            lines.append(f"  - `{sample['status']} {sample['path']}`")
        lines.append("")

    lines.extend(["## 建议提交批次", ""])
    batches = list(plan.get("recommended_batches") or [])
    if batches:
        lines.extend(f"- {batch}" for batch in batches)
    else:
        lines.append("- 暂无。")

    lines.extend(["", "## 风险提醒", ""])
    sensitive = list(plan.get("sensitive_candidates") or [])
    if sensitive:
        lines.append("- 发现疑似本地配置、密钥或凭证相关路径，默认不要提交：")
        lines.extend(f"  - `{item['status']} {item['path']}`" for item in sensitive)
    else:
        lines.append("- 未在路径层面发现明显本地配置或密钥风险；提交前仍需检查内容。")

    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告不自动删除、归档、回滚、提交或推送。",
            "- 代码批次和运行状态批次必须分开提交。",
            "- `*.local.json`、`.env` 和凭证类文件默认不进入版本库。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan my-mind run-state cleanup batches.")
    parser.add_argument("--sample-limit", type=int, default=5, help="Maximum samples shown per category.")
    parser.add_argument("--write", action="store_true", help="Write timestamped report and fixed dashboard view.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = parse_git_status(run_git_status())
    plan = build_plan(entries, sample_limit=max(1, args.sample_limit))
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    report = render_report(plan, mode="写入" if args.write else "dry-run")
    if args.write:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
        report_path = RUN_DIR / f"运行产物治理-{now_filename()}.md"
        fixed_path = DASHBOARD_DIR / "运行产物治理.md"
        report_path.write_text(report, encoding="utf-8")
        fixed_path.write_text(report, encoding="utf-8")
        print(repo_relative(report_path))
        print(repo_relative(fixed_path))
    else:
        print(report, end="")
        print("\n未写入。加 --write 后会生成运行产物治理报告。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
