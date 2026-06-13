#!/usr/bin/env python3
"""Consume my-mind pending distillation items into candidates and confirmations."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")

INBOX_DIR = ROOT / "00_收件箱"
FLOW_DIR = ROOT / "05_流转区"
CONFIRM_DIR = FLOW_DIR / "50_待确认"
RUN_DIR = ROOT / "85_运行记录"
CONFIRM_QUEUE_MD = CONFIRM_DIR / "待确认队列.md"
CONFIRM_QUEUE_JSONL = RUN_DIR / "待确认事项.jsonl"

TRIAGE_SCRIPT = ROOT / ".codex/skills/inbox-triage/scripts/triage_inbox.py"
INTAKE_SCRIPT = ROOT / ".codex/skills/knowledge-intake/scripts/knowledge_intake.py"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


triage = load_module(TRIAGE_SCRIPT, "my_mind_pending_distill_triage")
intake = load_module(INTAKE_SCRIPT, "my_mind_pending_distill_intake")


@dataclass
class PendingItem:
    note: Any
    result: Any
    phase: str
    candidate_paths: list[str]
    strategy: str


@dataclass
class ConfirmItem:
    source: Path
    title: str
    status: str
    reason: str
    priority: str
    candidate_kind: str = ""
    candidate_path: Path | None = None
    existing_candidates: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)


@dataclass
class ConsumeRun:
    mode: str
    pending: list[PendingItem] = field(default_factory=list)
    generated: list[ConfirmItem] = field(default_factory=list)
    confirmations: list[ConfirmItem] = field(default_factory=list)
    deferred: list[ConfirmItem] = field(default_factory=list)
    flow_paths: list[Path] = field(default_factory=list)


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_repo_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一文件名：{path}")


def dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def pending_items(inbox: Path, sources: list[str], limit: int) -> list[PendingItem]:
    if sources:
        notes = [intake.load_note(resolve_repo_path(source)) for source in sources]
    else:
        notes = triage.load_notes_by_status(inbox, {"已分拣"})

    items: list[PendingItem] = []
    for note in notes:
        result = triage.triage_note(note)
        phase, candidate_paths, strategy = triage.summarize_pending_stage(note)
        if phase not in {"已阅读待生成候选", "已有候选待确认", "已读待补判断"}:
            continue
        items.append(
            PendingItem(
                note=note,
                result=result,
                phase=phase,
                candidate_paths=candidate_paths,
                strategy=strategy,
            )
        )
    priority_order = {"高": 0, "中": 1, "低": 2}
    items.sort(key=lambda item: (priority_order.get(item.result.priority, 9), -item.result.score, item.note.title))
    return items if limit <= 0 else items[:limit]


def classify_deferred_reason(reason: str) -> str:
    if "已经有候选回链" in reason:
        return "已有候选待确认"
    if "阅读状态不是已读" in reason:
        return "需用户阅读反馈"
    if "内容质量" in reason or "解析状态" in reason:
        return "需继续解析或核验"
    if "敏感状态" in reason:
        return "需人工确认"
    return "需用户判断"


def default_options(status: str, kind: str) -> list[str]:
    if status in {"已生成候选", "已有候选待确认"}:
        options = ["确认转正", "继续核验", "调整分类", "跳过"]
    elif status == "需继续解析或核验":
        options = ["继续解析", "补充判断", "跳过"]
    elif status == "需用户阅读反馈":
        options = ["已读：你的想法", "沉淀成提示词", "放到资料库", "跳过"]
    else:
        options = ["补充判断", "改成提示词", "放到资料库", "跳过"]
    if kind == "prompt" and "改成资料库" not in options:
        options.insert(-1, "改成资料库")
    return dedupe(options)


def kind_from_candidate_path(path: str) -> str:
    normalized = path.strip().lstrip("./")
    if normalized.startswith("75_提示词库/"):
        return "prompt"
    if normalized.startswith("20_资料库/"):
        return "library"
    if normalized.startswith("65_洞察/"):
        return "insight"
    if normalized.startswith("30_原子笔记/"):
        return "atomic"
    return ""


def kind_from_existing_candidates(paths: list[str]) -> str:
    for path in paths:
        kind = kind_from_candidate_path(path)
        if kind:
            return kind
    return ""


def kind_label(kind: str) -> str:
    return {
        "prompt": "提示词",
        "library": "资料库",
        "insight": "洞察",
        "atomic": "原子笔记",
    }.get(kind, kind or "待判断")


def build_confirm_item_from_candidate(item: PendingItem, candidate: Any) -> ConfirmItem:
    if candidate.status == "已生成候选":
        status = "已生成候选"
        reason = candidate.reason
        target = candidate.target
        existing: list[str] = []
        candidate_kind = candidate.kind
    else:
        status = classify_deferred_reason(candidate.reason)
        reason = candidate.reason
        target = None
        existing = item.candidate_paths
        candidate_kind = kind_from_existing_candidates(existing) or candidate.kind
    questions = dedupe(
        [
            *getattr(candidate, "questions", []),
            f"请确认《{item.note.title}》下一步：确认转正 / 继续核验 / 调整分类 / 跳过。",
        ]
    )
    return ConfirmItem(
        source=item.note.path,
        title=candidate.title or item.note.title,
        status=status,
        reason=reason,
        priority=item.result.priority,
        candidate_kind=candidate_kind,
        candidate_path=target,
        existing_candidates=existing,
        questions=questions,
        options=default_options(status, candidate_kind),
    )


def consume_item(item: PendingItem, *, target: str, force: bool, force_unread: bool, write: bool) -> ConfirmItem:
    candidate = intake.make_candidate(item.note, item.result, target, force_unread, force)
    confirm = build_confirm_item_from_candidate(item, candidate)
    if write and candidate.status == "已生成候选":
        candidate.target.parent.mkdir(parents=True, exist_ok=True)
        candidate.target.write_text(candidate.content, encoding="utf-8")
        intake.update_source_note(candidate)
    elif write and candidate.status != "已生成候选" and "来源已经有候选回链" not in candidate.reason:
        # Record the reason on the source so repeated runs can be audited.
        intake.update_source_note(candidate)
    return confirm


def render_confirm_queue(items: list[ConfirmItem]) -> str:
    lines = [
        "# 待确认队列",
        "",
        "这里承接 `pending-distill` 自动消费后仍需要用户判断的事项。",
        "",
        "## 使用边界",
        "",
        "- 这是确认视图，不是长期知识正文。",
        "- OpenClaw 可以读取本页生成“今日待确认”前台消息。",
        "- 用户确认后仍由 Codex 后台执行转正门禁、回写状态和飞书同步。",
        "",
        "## 总览",
        "",
        f"- 更新时间：{now_datetime()}",
        f"- 待确认数量：{len(items)}",
        "",
        "## 队列",
        "",
    ]
    if not items:
        lines.append("当前没有待确认事项。")
        return "\n".join(lines).rstrip() + "\n"

    for index, item in enumerate(items, start=1):
        source_rel = repo_relative(item.source)
        candidate_text = "暂无"
        if item.candidate_path:
            candidate_text = f"`{repo_relative(item.candidate_path)}`"
        elif item.existing_candidates:
            candidate_text = "、".join(f"`{path}`" for path in item.existing_candidates)
        lines.extend(
            [
                f"### {index}. {item.title}",
                "",
                f"- 状态：{item.status}",
                f"- 优先级：{item.priority}",
                f"- 类型：{kind_label(item.candidate_kind)}",
                f"- 来源文件：`{source_rel}`",
                f"- 候选文件：{candidate_text}",
                f"- 原因：{item.reason}",
                f"- 可回复：{' / '.join(item.options)}",
                "- 需要确认：",
            ]
        )
        for question in item.questions or [f"请确认《{item.title}》是否继续沉淀。"]:
            lines.append(f"  - {question}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def confirm_json_records(items: list[ConfirmItem]) -> str:
    records = []
    for index, item in enumerate(items, start=1):
        records.append(
            {
                "序号": index,
                "更新时间": now_datetime(),
                "标题": item.title,
                "状态": item.status,
                "优先级": item.priority,
                "类型": kind_label(item.candidate_kind),
                "类型代码": item.candidate_kind,
                "来源文件": repo_relative(item.source),
                "候选文件": repo_relative(item.candidate_path) if item.candidate_path else "",
                "已有候选": item.existing_candidates,
                "原因": item.reason,
                "确认问题": item.questions,
                "可回复": item.options,
                "处理状态": "待确认",
            }
        )
    return "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + ("\n" if records else "")


def render_report(run: ConsumeRun) -> str:
    created = len(run.generated)
    needs_confirm = len(run.confirmations)
    deferred = len(run.deferred)
    lines = [
        "# 待沉淀消费报告",
        "",
        "## 总览",
        "",
        f"- 时间：{now_datetime()}",
        f"- 模式：{run.mode}",
        f"- 扫描待沉淀：{len(run.pending)}",
        f"- 自动生成候选：{created}",
        f"- 待确认：{needs_confirm}",
        f"- 暂缓：{deferred}",
        "",
        "## 自动生成候选",
        "",
    ]
    if run.generated:
        for item in run.generated:
            lines.extend(
                [
                    f"- 《{item.title}》",
                    f"  - 来源：`{repo_relative(item.source)}`",
                    f"  - 候选：`{repo_relative(item.candidate_path) if item.candidate_path else '未知'}`",
                    f"  - 下一步：进入待确认队列，不自动转正。",
                ]
            )
    else:
        lines.append("- 本轮没有自动生成候选。")

    lines.extend(["", "## 待确认事项", ""])
    if run.confirmations:
        for index, item in enumerate(run.confirmations, start=1):
            lines.extend(
                [
                    f"{index}. 《{item.title}》：{item.status}",
                    f"   - 来源：`{repo_relative(item.source)}`",
                    f"   - 回复选项：{' / '.join(item.options)}",
                ]
            )
    else:
        lines.append("当前无需用户确认。")

    lines.extend(["", "## 暂缓事项", ""])
    if run.deferred:
        for item in run.deferred:
            lines.extend(
                [
                    f"- 《{item.title}》：{item.reason}",
                    f"  - 来源：`{repo_relative(item.source)}`",
                ]
            )
    else:
        lines.append("- 本轮没有暂缓事项。")

    if run.flow_paths:
        lines.extend(["", "## 已刷新流转区", ""])
        lines.extend(f"- `{repo_relative(path)}`" for path in run.flow_paths)
    lines.extend(
        [
            "",
            "## 前台反馈出口",
            "",
            f"- Markdown：`{repo_relative(CONFIRM_QUEUE_MD)}`",
            f"- JSONL：`{repo_relative(CONFIRM_QUEUE_JSONL)}`",
            "- OpenClaw 应把这里发布成“今日待确认”，不要混进普通待读。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(run: ConsumeRun) -> Path:
    all_confirm_items = [*run.confirmations, *run.deferred]
    CONFIRM_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    CONFIRM_QUEUE_MD.write_text(render_confirm_queue(all_confirm_items), encoding="utf-8")
    CONFIRM_QUEUE_JSONL.write_text(confirm_json_records(all_confirm_items), encoding="utf-8")
    report_path = unique_path(RUN_DIR / f"待沉淀消费-{now_filename()}.md")
    report_path.write_text(render_report(run), encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consume my-mind pending distillation queue.")
    parser.add_argument("--inbox", default=str(INBOX_DIR), help="Inbox directory.")
    parser.add_argument("--source", action="append", default=[], help="Process a specific inbox source. Repeatable.")
    parser.add_argument("--target", choices=["auto", "library", "prompt", "insight"], default="auto", help="Candidate target type.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum pending items to consume. 0 means all.")
    parser.add_argument("--force", action="store_true", help="Allow a new candidate even when source already has candidate links.")
    parser.add_argument("--force-unread", action="store_true", help="Allow candidate generation for unread sources.")
    parser.add_argument("--no-flow", action="store_true", help="Do not refresh 05_流转区 when writing.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write candidates, confirmation queue, flow views, and report.")
    mode.add_argument("--dry-run", action="store_true", help="Preview only. This is the default.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inbox = resolve_repo_path(args.inbox)
    write = bool(args.write)
    run = ConsumeRun(mode="写入" if write else "dry-run")
    run.pending = pending_items(inbox, args.source, args.limit)

    for pending in run.pending:
        confirm = consume_item(
            pending,
            target=args.target,
            force=args.force,
            force_unread=args.force_unread,
            write=write,
        )
        if confirm.status == "已生成候选":
            run.generated.append(confirm)
            run.confirmations.append(confirm)
        elif confirm.status == "已有候选待确认":
            run.confirmations.append(confirm)
        else:
            run.deferred.append(confirm)

    if write and not args.no_flow:
        run.flow_paths = intake.refresh_flow_views()

    report = render_report(run)
    print(report, end="")
    if write:
        report_path = write_outputs(run)
        print(f"\n已写入待沉淀消费报告：{repo_relative(report_path)}")
    else:
        print("\n未写入。加 --write 后会生成候选、刷新流转区并更新待确认队列。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
