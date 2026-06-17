#!/usr/bin/env python3
"""Repair low-quality parsed inbox notes and refresh the verification queue."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")

INBOX_DIR = ROOT / "00_收件箱"
FLOW_DIR = ROOT / "05_流转区"
VERIFY_DIR = FLOW_DIR / "40_待核验"
RUN_DIR = ROOT / "85_运行记录"
REPAIR_QUEUE_MD = VERIFY_DIR / "解析质量修复队列.md"
TRIAGE_SCRIPT = ROOT / ".codex/skills/inbox-triage/scripts/triage_inbox.py"

TERMINAL_STATUSES = {"已处理", "已晋升", "已归档", "可丢弃"}
LOW_QUALITY_VALUES = {"需核验", "需继续解析", "解析失败", "部分解析"}
TRUNCATED_MARKERS = ("摘录已截断", "仍包含截断", "[…]", "...", "…")
SECTION_HEADINGS = [
    "中文摘要",
    "摘要",
    "关键点",
    "文案摘录",
    "视频内容摘录",
    "字幕摘录",
    "图片文字 OCR",
    "原文摘录",
    "阅读思考",
]

TERM_REPLACEMENTS = [
    (r"扣袋子", "Codex"),
    (r"扣得克斯", "Codex"),
    (r"口袋子", "Codex"),
    (r"Code\s*X", "Codex"),
    (r"\bcodex\b", "Codex"),
    (r"克劳德\s*Code", "Claude Code"),
    (r"克劳德代码", "Claude Code"),
    (r"\bclaude\s+code\b", "Claude Code"),
    (r"\banthropic\b", "Anthropic"),
    (r"\bopen\s*ai\b", "OpenAI"),
    (r"\bopenclaw\b", "OpenClaw"),
    (r"\bopen\s*claw\b", "OpenClaw"),
    (r"\bm\s*c\s*p\b", "MCP"),
]

TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "後": "后",
        "裏": "里",
        "裡": "里",
        "麼": "么",
        "這": "这",
        "個": "个",
        "為": "为",
        "與": "与",
        "對": "对",
        "會": "会",
        "開": "开",
        "關": "关",
        "學": "学",
        "習": "习",
        "時": "时",
        "間": "间",
        "還": "还",
        "讓": "让",
        "從": "从",
        "轉": "转",
        "寫": "写",
        "讀": "读",
        "現": "现",
        "實": "实",
        "應": "应",
        "產": "产",
        "業": "业",
        "體": "体",
        "驗": "验",
        "證": "证",
        "質": "质",
        "優": "优",
        "風": "风",
        "險": "险",
        "術": "术",
        "語": "语",
        "標": "标",
        "題": "题",
        "內": "内",
        "容": "容",
        "錄": "录",
        "圖": "图",
        "視": "视",
        "頻": "频",
        "頻": "频",
        "點": "点",
        "劃": "划",
        "歸": "归",
        "類": "类",
        "態": "态",
        "狀": "状",
        "將": "将",
        "長": "长",
        "期": "期",
        "資": "资",
        "庫": "库",
        "飛": "飞",
        "書": "书",
        "鏈": "链",
        "接": "接",
    }
)


@dataclass
class Note:
    path: Path
    text: str
    frontmatter: dict[str, Any]
    body: str

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("标题") or self.path.stem)

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("处理状态") or "")

    @property
    def quality(self) -> str:
        return str(self.frontmatter.get("内容质量") or "")

    @property
    def parse_status(self) -> str:
        return str(self.frontmatter.get("解析状态") or "")


@dataclass
class RepairResult:
    note: Note
    actions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    debt_category: str = ""
    debt_action: str = ""
    changed_text: str = ""
    final_quality: str = ""
    final_parse_status: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.changed_text and self.changed_text != self.note.text)

    @property
    def resolved(self) -> bool:
        return not self.blockers and self.final_quality == "可推送"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


triage = load_module(TRIAGE_SCRIPT, "my_mind_parse_quality_triage")


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    return triage.parse_frontmatter(text)


def load_note(path: Path) -> Note:
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = parse_frontmatter(text)
    return Note(path=path, text=text, frontmatter=frontmatter, body=body)


def extract_section(body: str, heading: str) -> str:
    return triage.extract_section(body, heading)


def source_excerpt(note: Note, limit: int = 3000) -> str:
    parts: list[str] = []
    for heading in SECTION_HEADINGS:
        section = extract_section(note.body, heading)
        if section:
            parts.append(section)
    text = "\n\n".join(parts).strip() or note.body
    return re.sub(r"\s+", " ", text).strip()[:limit]


def has_truncated_or_summary_only_source(note: Note) -> bool:
    source_kind = str(note.frontmatter.get("内容摘录来源") or "")
    body = note.body
    original_excerpt = extract_section(body, "原文摘录")
    has_rss_summary = "RSS/Atom" in source_kind
    has_full_video = bool(extract_section(body, "视频内容摘录") and "转写摘录" in extract_section(body, "视频内容摘录"))
    has_ocr = bool(extract_section(body, "图片文字 OCR") and str(note.frontmatter.get("图片OCR字数") or "").strip())
    has_copy = len(extract_section(body, "文案摘录")) >= 180
    has_article = len(extract_section(body, "正文") or extract_section(body, "文章正文")) >= 500
    has_truncated_marker = any(marker in original_excerpt for marker in TRUNCATED_MARKERS) or any(
        marker in body for marker in ("摘录已截断", "仍包含截断")
    )
    if has_rss_summary and not (has_full_video or has_ocr or has_copy or has_article):
        return True
    if has_truncated_marker and not (has_full_video or has_ocr or has_copy or has_article):
        return True
    return False


def normalize_text(text: str) -> tuple[str, list[str]]:
    updated = text.translate(TRADITIONAL_TO_SIMPLIFIED)
    actions: list[str] = []
    if updated != text:
        actions.append("繁体字初步转为简体中文")
    for pattern, replacement in TERM_REPLACEMENTS:
        next_text = re.sub(pattern, replacement, updated, flags=re.I)
        if next_text != updated:
            actions.append(f"修正术语：{replacement}")
            updated = next_text
    return updated, sorted(set(actions))


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


def note_needs_repair(note: Note, include_terminal: bool) -> bool:
    if note.status in TERMINAL_STATUSES and not include_terminal:
        return False
    combined = f"{note.text}\n{note.quality}\n{note.parse_status}"
    if note.quality in LOW_QUALITY_VALUES or note.parse_status in LOW_QUALITY_VALUES:
        return True
    if has_truncated_or_summary_only_source(note):
        return True
    if "tiny 模型" in combined or "small 模型" in combined:
        return True
    if any(re.search(pattern, combined, flags=re.I) for pattern, _ in TERM_REPLACEMENTS):
        return True
    return False


def iter_notes(inbox: Path, sources: list[str], include_terminal: bool) -> list[Note]:
    if sources:
        paths = [Path(source) if Path(source).is_absolute() else ROOT / source for source in sources]
    else:
        paths = sorted(path for path in inbox.glob("*.md") if path.name != "目录说明.md")
    notes = [load_note(path) for path in paths if path.exists()]
    return [note for note in notes if note_needs_repair(note, include_terminal)]


def assess_blockers(note: Note, text: str) -> list[str]:
    blockers: list[str] = []
    excerpt = source_excerpt(Note(path=note.path, text=text, frontmatter=note.frontmatter, body=parse_frontmatter(text)[1]), 3000)
    if len(excerpt) < 80:
        blockers.append("正文、摘录、OCR 或转写内容过短，需要补抓或人工补充。")
    if note.parse_status in {"解析失败", "部分解析"}:
        blockers.append(f"解析状态为「{note.parse_status}」，需要补抓来源或补充正文证据。")
    if note.quality == "需继续解析":
        blockers.append("内容质量为「需继续解析」，不能自动标记为可推送。")
    if has_truncated_or_summary_only_source(note):
        blockers.append("来源仍是 RSS/Atom 摘要或截断摘录，需要补全文、字幕、OCR 或转写。")
    if "内容摘录来源: \"\"" in text or "内容摘录来源: ''" in text:
        blockers.append("内容摘录来源为空，需要确认是否真的完成视频/正文解析。")
    if re.search(r"待补充[。.]|暂无[。.]|解析失败", excerpt):
        blockers.append("摘录仍包含占位或失败文本，需要继续解析。")
    return sorted(set(blockers))


def classify_debt(note: Note, text: str, blockers: list[str]) -> tuple[str, str]:
    if not blockers:
        return "已修复", "无需进入解析债务队列，可按质量门禁进入后续流程。"
    combined = f"{note.text}\n{text}\n{' '.join(blockers)}"
    source_kind = str(note.frontmatter.get("内容摘录来源") or "")
    urlish = f"{note.frontmatter.get('原始链接') or ''} {note.frontmatter.get('分享链接') or ''}"
    if "RSS/Atom" in source_kind or "RSS/Atom" in combined or "截断" in combined:
        return "可自动补全文", "优先尝试网页正文抓取、RSS 原文链接解析或重新入箱补全文；成功后再重跑解析质量修复。"
    if any(keyword in combined + urlish for keyword in ["抖音", "小红书", "YouTube", "youtube", "youtu.be", "视频", "转写", "字幕"]):
        return "需重抓转写", "重新抓取公开视频、字幕、音频或平台文案；必要时提高转写上限后重跑。"
    if any(keyword in combined for keyword in ["OCR", "图片", "图文", "截图"]):
        return "需补 OCR", "重新执行图文 OCR 或改用质量更高的 OCR 后端；确认图片文字足够再推送。"
    if note.parse_status in {"解析失败", "部分解析"} or "解析状态为" in combined:
        return "需重新解析", "重新执行入箱解析，补齐正文、摘录来源和解析状态。"
    if any("过短" in blocker or "不足" in blocker for blocker in blockers):
        return "证据过短", "补充原文、摘要、人工备注或来源截图；否则只保留为待核验。"
    return "需人工判断", "需要人工判断是否值得继续补抓、归档或跳过。"


def repair_note(note: Note) -> RepairResult:
    updated, actions = normalize_text(note.text)
    blockers = assess_blockers(note, updated)
    debt_category, debt_action = classify_debt(note, updated, blockers)
    final_quality = note.quality
    final_parse_status = note.parse_status or "已解析"

    if blockers:
        current_meta = parse_frontmatter(updated)[0]
        if str(current_meta.get("内容质量") or "") not in {"需继续解析", "需核验"}:
            final_quality = "需核验"
            updated = set_scalar_field(updated, "内容质量", final_quality)
            actions.append("将内容质量标记为需核验")
        gate = "；".join(blockers)
        if str(current_meta.get("质量门禁") or "") != gate:
            updated = set_scalar_field(updated, "质量门禁", gate)
            actions.append("更新质量门禁说明")
    else:
        excerpt = source_excerpt(Note(path=note.path, text=updated, frontmatter=note.frontmatter, body=parse_frontmatter(updated)[1]), 3000)
        if len(excerpt) >= 180:
            current_meta = parse_frontmatter(updated)[0]
            final_quality = "可推送"
            final_parse_status = "已解析"
            gate = "parse-quality-repair 已完成低风险术语和简体中文初校，可进入前台阅读；正式沉淀仍需转正门禁。"
            if str(current_meta.get("解析状态") or "") != final_parse_status:
                updated = set_scalar_field(updated, "解析状态", final_parse_status)
                actions.append("解析状态更新为已解析")
            if str(current_meta.get("内容质量") or "") != final_quality:
                updated = set_scalar_field(updated, "内容质量", final_quality)
                actions.append("质量标签更新为可推送")
            if str(current_meta.get("质量门禁") or "") != gate:
                updated = set_scalar_field(updated, "质量门禁", gate)
                actions.append("更新质量门禁说明")
        else:
            blockers.append("可读摘录不足 180 字，仅适合保留待核验。")
            final_quality = "需核验"
            current_meta = parse_frontmatter(updated)[0]
            if str(current_meta.get("内容质量") or "") != final_quality:
                updated = set_scalar_field(updated, "内容质量", final_quality)
                actions.append("将内容质量标记为需核验")

    if actions or blockers:
        entry_parts = [f"- {now_datetime()}：`parse-quality-repair`"]
        if actions:
            entry_parts.append(f"已处理：{'；'.join(sorted(set(actions)))}。")
        if blockers:
            entry_parts.append(f"仍需核验：{'；'.join(blockers)}")
        updated = append_to_section(updated, "解析修复记录", " ".join(entry_parts).rstrip())

    return RepairResult(
        note=note,
        actions=sorted(set(actions)),
        blockers=blockers,
        debt_category=debt_category,
        debt_action=debt_action,
        changed_text=updated,
        final_quality=final_quality,
        final_parse_status=final_parse_status,
    )


def render_repair_queue(results: list[RepairResult]) -> str:
    unresolved = [result for result in results if result.blockers]
    category_counter = Counter(result.debt_category or "未分类" for result in unresolved)
    lines = [
        "# 解析质量修复队列",
        "",
        "这里记录 `parse-quality-repair` 无法自动完成、仍需补抓、补 OCR、补字幕、重转写或人工判断的条目。",
        "",
        "## 总览",
        "",
        f"- 更新时间：{now_datetime()}",
        f"- 待核验数量：{len(unresolved)}",
        f"- 债务分型：{dict(category_counter)}",
        "",
        "## 队列",
        "",
    ]
    if not unresolved:
        lines.append("当前没有新的解析质量修复待办。")
        return "\n".join(lines).rstrip() + "\n"
    for index, result in enumerate(unresolved, start=1):
        lines.extend(
            [
                f"### {index}. {result.note.title}",
                "",
                f"- 来源文件：`{repo_relative(result.note.path)}`",
                f"- 当前质量：{result.final_quality or result.note.quality or '未知'}",
                f"- 解析债务：{result.debt_category or '未分类'}",
                f"- 建议动作：{result.debt_action or '先补全解析证据；修复前不要进入前台精选、候选转正或飞书长期精选。'}",
                "- 待处理：",
            ]
        )
        lines.extend(f"  - {blocker}" for blocker in result.blockers)
        lines.extend(["", "- 门禁：修复前不要进入前台精选、候选转正或飞书长期精选。", ""])
    return "\n".join(lines).rstrip() + "\n"


def render_report(results: list[RepairResult], mode: str, flow_paths: list[Path]) -> str:
    changed = [result for result in results if result.changed]
    unresolved = [result for result in results if result.blockers]
    resolved = [result for result in results if result.resolved]
    lines = [
        "# 解析质量修复报告",
        "",
        "## 总览",
        "",
        f"- 时间：{now_datetime()}",
        f"- 模式：{mode}",
        f"- 扫描命中：{len(results)}",
        f"- 有写入变化：{len(changed)}",
        f"- 修复后可推送：{len(resolved)}",
        f"- 仍需核验：{len(unresolved)}",
        "",
        "## 解析债务分型",
        "",
    ]
    debt_counter = Counter(result.debt_category or "未分类" for result in unresolved)
    if debt_counter:
        lines.extend(f"- {category}：{count} 条" for category, count in debt_counter.items())
    else:
        lines.append("- 暂无解析债务。")
    lines.extend(
        [
            "",
            "## 处理明细",
            "",
        ]
    )
    if not results:
        lines.append("- 本轮没有命中需要解析质量修复的条目。")
    for result in results:
        lines.extend(
            [
                f"### {result.note.title}",
                "",
                f"- 来源：`{repo_relative(result.note.path)}`",
                f"- 结果质量：{result.final_quality or result.note.quality or '未知'}",
            ]
        )
        if result.actions:
            lines.append(f"- 自动处理：{'；'.join(result.actions)}")
        if result.blockers:
            lines.append(f"- 解析债务：{result.debt_category or '未分类'}")
            lines.append(f"- 建议动作：{result.debt_action or '补全解析证据后重跑门禁。'}")
            lines.append("- 仍需核验：")
            lines.extend(f"  - {blocker}" for blocker in result.blockers)
        if not result.actions and not result.blockers:
            lines.append("- 未发现可自动修复的问题。")
        lines.append("")
    if flow_paths:
        lines.extend(["## 已刷新流转区", ""])
        lines.extend(f"- `{repo_relative(path)}`" for path in flow_paths)
        lines.append("")
    lines.extend(
        [
            "## 输出",
            "",
            f"- 待核验修复队列：`{repo_relative(REPAIR_QUEUE_MD)}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def refresh_flow_views() -> list[Path]:
    reading_notes = triage.load_notes_by_status(INBOX_DIR, {"已分拣"})
    reading_queue = [triage.triage_note(note) for note in reading_notes]
    return triage.write_flow_views(reading_queue, FLOW_DIR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair low-quality parsed my-mind inbox notes.")
    parser.add_argument("--inbox", default=str(INBOX_DIR), help="Inbox directory.")
    parser.add_argument("--source", action="append", default=[], help="Specific source file to repair. Repeatable.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum matched notes to process. 0 means all.")
    parser.add_argument("--include-terminal", action="store_true", help="Also inspect terminal statuses such as 已归档/已处理.")
    parser.add_argument("--no-flow", action="store_true", help="Do not refresh 05_流转区 when writing.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write repairs, queue, flow views, and report.")
    mode.add_argument("--dry-run", action="store_true", help="Preview only. This is the default.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inbox = Path(args.inbox)
    if not inbox.is_absolute():
        inbox = ROOT / inbox
    notes = iter_notes(inbox, args.source, args.include_terminal)
    if args.limit > 0:
        notes = notes[: args.limit]
    results = [repair_note(note) for note in notes]

    write = bool(args.write)
    flow_paths: list[Path] = []
    if write:
        for result in results:
            if result.changed:
                result.note.path.write_text(result.changed_text, encoding="utf-8")
        VERIFY_DIR.mkdir(parents=True, exist_ok=True)
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        REPAIR_QUEUE_MD.write_text(render_repair_queue(results), encoding="utf-8")
        if not args.no_flow:
            flow_paths = refresh_flow_views()
        report_path = RUN_DIR / f"解析质量修复-{now_filename()}.md"
        report_path.write_text(render_report(results, "写入", flow_paths), encoding="utf-8")
        print(repo_relative(report_path))
    else:
        print(render_report(results, "dry-run", flow_paths), end="")
        print("\n未写入。加 --write 后会修复低风险问题并刷新待核验队列。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
