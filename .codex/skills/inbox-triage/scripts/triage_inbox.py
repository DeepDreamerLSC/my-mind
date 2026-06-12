#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

PROJECT_DESTINATION = "10_项目/个人数据资产系统/"
KNOWLEDGE_DESTINATION = "20_资料库/"
ATOMIC_DESTINATION = "30_原子笔记/"
PROMPT_DESTINATION = "75_提示词库/"
SIGNAL_DESTINATION = "60_行业情报/"
INSIGHT_DESTINATION = "65_洞察/候选洞察/"
KEEP_DESTINATION = "保持待分拣"

PROJECT_KEYWORDS = {
    "my-mind",
    "数据资产",
    "个人数据资产",
    "Codex",
    "skill",
    "Skill",
    "提示词",
}

PROMPT_KEYWORDS = {
    "提示词",
    "prompt",
    "Prompt",
    "skill",
    "Skill",
    "Codex",
}

SIGNAL_KEYWORDS = {
    "NVIDIA",
    "Jensen",
    "Huang",
    "AI Revolution",
    "AI scaling laws",
    "CUDA",
    "TSMC",
    "HBM",
    "供应链",
    "数据中心",
    "公司",
    "产业",
    "趋势",
}


@dataclass
class InboxNote:
    path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("标题") or self.path.stem)

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("处理状态") or "")

    @property
    def platform(self) -> str:
        return str(self.frontmatter.get("来源平台") or "")


@dataclass
class TriageResult:
    note: InboxNote
    priority: str
    destinations: list[str]
    value: str
    next_step: str
    risks: list[str]
    score: int


READING_FEEDBACK_PROMPT = "阅读后请反馈：你认同/不认同的点、与已有知识的连接、想沉淀到哪里、以及是否现在开始沉淀；也可以让我把这些反馈写回“阅读思考”章节。"


def clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value.strip()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text

    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in lines[1:end_index]:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(clean_scalar(line[4:]))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = clean_scalar(raw_value)
        current_key = key
        if value:
            data[key] = value
        else:
            data[key] = []
    body = "\n".join(lines[end_index + 1 :]).strip()
    return data, body


def extract_section(body: str, heading: str) -> str:
    pattern = re.compile(rf"^##+\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^##\s+", body[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(body)
    return body[start:end].strip()


def load_notes(inbox: Path, include_all: bool = False) -> list[InboxNote]:
    notes: list[InboxNote] = []
    for path in sorted(inbox.glob("*.md")):
        if path.name == "目录说明.md":
            continue
        text = path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(text)
        note = InboxNote(path=path, frontmatter=frontmatter, body=body)
        if include_all or note.status == "待分拣":
            notes.append(note)
    return notes


def load_notes_by_status(inbox: Path, statuses: set[str]) -> list[InboxNote]:
    return [note for note in load_notes(inbox, include_all=True) if note.status in statuses]


def has_any(text: str, keywords: set[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def strip_management_sections(body: str) -> str:
    management_headings = [
        "为什么保存",
        "初步想法",
        "阅读思考",
        "后续处理建议",
        "分拣记录",
        "沉淀记录",
        "原始链接",
    ]
    cleaned = body
    for heading in management_headings:
        pattern = re.compile(rf"\n##\s+{re.escape(heading)}\s*\n.*?(?=\n##\s+|\Z)", re.DOTALL)
        cleaned = pattern.sub("\n", cleaned)
    return cleaned.strip()


def normalize_feedback_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(("-", "*")):
            line = line[1:].strip()
        if not line:
            continue
        if line.startswith("可记录："):
            continue
        if line in {"待补充。", "待补充", "暂无。", "暂无"}:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def has_meaningful_reading_feedback(note: InboxNote) -> bool:
    reading_notes = extract_section(note.body, "阅读思考")
    return bool(normalize_feedback_text(reading_notes))


def repo_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def classify_candidate_target(path_text: str) -> str:
    if path_text.startswith("75_提示词库/"):
        return PROMPT_DESTINATION
    if path_text.startswith("20_资料库/"):
        return KNOWLEDGE_DESTINATION
    if path_text.startswith("30_原子笔记/"):
        return ATOMIC_DESTINATION
    if path_text.startswith("10_项目/"):
        return PROJECT_DESTINATION
    if path_text.startswith("60_行业情报/"):
        return SIGNAL_DESTINATION
    if path_text.startswith("65_洞察/"):
        return INSIGHT_DESTINATION
    return "其他候选"


def extract_candidate_targets(note: InboxNote) -> list[tuple[str, str]]:
    record = extract_section(note.body, "沉淀记录")
    if not record:
        return []

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, raw_link in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", record):
        link = unquote(raw_link.strip())
        if not link or "://" in link:
            continue
        target = (note.path.parent / link).resolve()
        rel = repo_relative_path(target)
        if rel in seen:
            continue
        seen.add(rel)
        entries.append((rel, classify_candidate_target(rel)))
    return entries


def summarize_pending_stage(note: InboxNote) -> tuple[str, list[str], str]:
    candidate_targets = extract_candidate_targets(note)
    candidate_paths = [path for path, _ in candidate_targets]
    candidate_types = [candidate_type for _, candidate_type in candidate_targets]
    has_feedback = has_meaningful_reading_feedback(note)

    if candidate_targets:
        phase = "已有候选待确认"
    elif has_feedback:
        phase = "已阅读待生成候选"
    else:
        phase = "等待阅读反馈"

    if PROMPT_DESTINATION in candidate_types:
        strategy = "保持提示词候选，不直接视为正式方法论；先在真实任务里试跑 1 到 2 次，确认高频复用后再考虑升级到项目流程或 `.codex/skills/`。"
    elif KNOWLEDGE_DESTINATION in candidate_types and INSIGHT_DESTINATION in candidate_types:
        strategy = "保持资料候选，先抽出关键事实、观察问题和可验证判断；只有形成跨来源结论后，再考虑升级到洞察或原子笔记。"
    elif KNOWLEDGE_DESTINATION in candidate_types:
        strategy = "保持资料整理稿，不直接晋升长期知识；先补关键事实核验，再决定是否提炼成原子笔记或行业情报。"
    elif phase == "已阅读待生成候选":
        strategy = "已具备继续沉淀条件；先根据阅读思考选择一个主目标目录生成候选草稿，再决定是否需要扩展到项目任务或提示词库。"
    elif PROMPT_DESTINATION in candidate_types or PROJECT_DESTINATION in candidate_types:
        strategy = "先保留为工作流候选，再用一次真实任务验证是否真能减少重复劳动。"
    else:
        strategy = "先阅读并把判断写回“阅读思考”，暂不直接沉淀；确认继续后，只生成候选草稿，不自动确认长期知识。"

    return phase, candidate_paths, strategy


def is_prompt_material(text: str) -> bool:
    lowered = text.lower()
    explicit = any(keyword.lower() in lowered for keyword in PROMPT_KEYWORDS)
    workflow_context = any(keyword in text for keyword in {"工作流", "自动化", "Agent", "agent", "workflow"})
    codex_context = "codex" in lowered or "skill" in lowered or "提示词" in text
    return explicit or (workflow_context and codex_context)


def is_project_material(text: str) -> bool:
    lowered = text.lower()
    direct = has_any(text, PROJECT_KEYWORDS)
    inbox_context = "收件箱" in text and any(keyword in text for keyword in {"分拣", "入箱", "录入", "沉淀", "自动化"})
    workflow_context = "工作流" in text and "codex" in lowered
    return direct or inbox_context or workflow_context


def int_field(note: InboxNote, field: str) -> int:
    value = str(note.frontmatter.get(field) or "").strip()
    try:
        return int(re.sub(r"\D+", "", value) or "0")
    except ValueError:
        return 0


def summarize_value(note: InboxNote, text: str, destinations: list[str]) -> str:
    if PROMPT_DESTINATION in destinations and PROJECT_DESTINATION in destinations:
        return "与 Codex、提示词、skill 或自动化直接相关，适合转成可复用工作流，并反哺个人数据资产系统。"
    if SIGNAL_DESTINATION in destinations:
        return "包含 AI 产业、公司或技术趋势信息，适合作为行业信号和资料库素材。"
    if note.frontmatter.get("外部转录链接"):
        return "存在可追溯转录来源，适合先整理为资料库条目，再提炼观点和事实。"
    if note.frontmatter.get("内容摘录来源"):
        return "已经有公开视频转写摘录，适合先做资料库草稿和重点提取。"
    if note.platform in {"抖音", "小红书", "X"}:
        return "社媒内容已保留平台文案和互动数据，适合快速判断是否值得进一步沉淀。"
    return "已捕获基础信息，可作为资料库候选等待进一步整理。"


def suggest_next_step(note: InboxNote, destinations: list[str], risks: list[str]) -> str:
    title = note.title
    if PROMPT_DESTINATION in destinations:
        return f"从《{title}》中提取可复用提示词或工作流，先写入提示词库草稿，并把对 inbox/skill 的改进点加入项目任务候选。"
    if SIGNAL_DESTINATION in destinations:
        return f"先整理《{title}》的来源、关键事实和可验证判断，放入资料库；涉及趋势判断的部分再进入行业情报或候选洞察。"
    if note.frontmatter.get("外部转录链接"):
        return "优先基于官方转录来源做结构化摘要，避免依赖不完整的视频抓取结果。"
    if "转写质量需要校对" in "；".join(risks):
        return "先用更高质量转写或人工校对修正摘要，再决定是否晋升资料库或原子笔记。"
    return "保留为资料库候选，人工查看正文后决定是否继续沉淀。"


def collect_risks(note: InboxNote) -> list[str]:
    risks: list[str] = []
    parse_status = str(note.frontmatter.get("解析状态") or "")
    if parse_status and parse_status != "已解析":
        risks.append(f"解析状态为「{parse_status}」，需要确认基础信息是否完整。")
    if note.platform in {"抖音", "小红书", "X", "TikTok"}:
        risks.append("来源为社媒公开页，字段可能随平台页面结构变化而不完整。")
    if note.frontmatter.get("外部转录链接"):
        risks.append("已有外部转录来源，应优先使用可追溯转录做正式整理。")
    if note.frontmatter.get("内容摘录后端") == "faster-whisper" and "模型：tiny" in note.body:
        risks.append("转写质量需要校对：tiny 模型容易把英文产品名和术语识别成中文谐音。")
    if int_field(note, "内容摘录字数") > 5000:
        risks.append("内容较长，直接沉淀前需要先抽章节和主题。")
    if has_any(note.title + "\n" + note.body[:2000], SIGNAL_KEYWORDS):
        risks.append("涉及产品、公司、模型或产业判断，晋升洞察前需要事实核验。")
    return risks or ["暂无明显风险，但仍需人工确认后再晋升长期知识。"]


def triage_note(note: InboxNote) -> TriageResult:
    tags = note.frontmatter.get("标签") or []
    tag_text = " ".join(tags) if isinstance(tags, list) else str(tags)
    source_body = strip_management_sections(note.body)
    text = "\n".join(
        [
            note.title,
            note.platform,
            str(note.frontmatter.get("作者或频道") or ""),
            tag_text,
            source_body[:4000],
        ]
    )

    destinations: list[str] = []
    score = 0

    if is_prompt_material(text):
        destinations.extend([PROMPT_DESTINATION, PROJECT_DESTINATION])
        score += 3
    if is_project_material(text):
        if PROJECT_DESTINATION not in destinations:
            destinations.append(PROJECT_DESTINATION)
        score += 2
    if has_any(text, SIGNAL_KEYWORDS):
        destinations.extend([KNOWLEDGE_DESTINATION, SIGNAL_DESTINATION])
        score += 2
    if note.frontmatter.get("外部转录链接"):
        if KNOWLEDGE_DESTINATION not in destinations:
            destinations.append(KNOWLEDGE_DESTINATION)
        score += 2
    if note.frontmatter.get("内容摘录来源"):
        if KNOWLEDGE_DESTINATION not in destinations:
            destinations.append(KNOWLEDGE_DESTINATION)
        score += 1
    if int_field(note, "内容摘录字数") > 5000:
        if INSIGHT_DESTINATION not in destinations:
            destinations.append(INSIGHT_DESTINATION)
        score += 1
    if not destinations:
        destinations.append(KNOWLEDGE_DESTINATION if note.frontmatter.get("解析状态") == "已解析" else KEEP_DESTINATION)

    deduped: list[str] = []
    for destination in destinations:
        if destination not in deduped:
            deduped.append(destination)

    if score >= 5:
        priority = "高"
    elif score >= 2:
        priority = "中"
    else:
        priority = "低"

    risks = collect_risks(note)
    value = summarize_value(note, text, deduped)
    next_step = suggest_next_step(note, deduped, risks)
    return TriageResult(note, priority, deduped, value, next_step, risks, score)


def render_result_block(result: TriageResult, index: int, include_reading_feedback: bool = False) -> list[str]:
    note = result.note
    lines = [
        "",
        f"### {index}. {note.title}",
        "",
        f"- 优先级：{result.priority}",
        f"- 建议去向：{'、'.join(result.destinations)}",
        f"- 核心价值：{result.value}",
        f"- 下一步：{result.next_step}",
        f"- 来源平台：{note.platform or '未知'}",
        f"- 解析状态：{note.frontmatter.get('解析状态') or '未知'}",
        f"- 来源文件：`{note.path}`",
    ]
    if include_reading_feedback:
        phase, candidate_paths, strategy = summarize_pending_stage(note)
        lines.append(f"- 当前阶段：{phase}")
        lines.append(f"- 已有候选：{'、'.join(f'`{path}`' for path in candidate_paths) if candidate_paths else '暂无'}")
        lines.append(f"- 建议处理策略：{strategy}")
        lines.append(f"- 阅读后反馈：{READING_FEEDBACK_PROMPT}")
    lines.append("- 风险：")
    lines.extend(f"  - {risk}" for risk in result.risks)
    return lines


def render_report(results: list[TriageResult], reading_queue: list[TriageResult], inbox: Path, mark_sorted: bool = False) -> str:
    now = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    counts = {"高": 0, "中": 0, "低": 0}
    for result in results:
        counts[result.priority] += 1
    write_strategy = "自动分拣可回写已分拣状态和分拣记录，但不会生成沉淀草稿。" if mark_sorted else "只生成建议，不修改收件箱源文件。"

    lines = [
        "# 收件箱分拣巡检",
        "",
        "## 总览",
        "",
        f"- 巡检时间：{now}",
        f"- 收件箱：`{inbox}`",
        f"- 待分拣数量：{len(results)}",
        f"- 高优先级：{counts['高']}",
        f"- 中优先级：{counts['中']}",
        f"- 低优先级：{counts['低']}",
        f"- 待沉淀数量：{len(reading_queue)}",
        f"- 写入策略：{write_strategy}",
        "",
        "## 待分拣建议",
    ]

    if not results:
        lines.extend(["", "当前没有待分拣条目。"])
    else:
        priority_order = {"高": 0, "中": 1, "低": 2}
        ordered = sorted(results, key=lambda item: (priority_order[item.priority], -item.score, item.note.title))
        for index, result in enumerate(ordered, start=1):
            lines.extend(render_result_block(result, index))

    lines.extend(["", "## 待沉淀队列"])
    if not reading_queue:
        lines.extend(["", "当前没有等待用户阅读反馈或后续确认的待沉淀条目。"])
        return "\n".join(lines).rstrip() + "\n"

    priority_order = {"高": 0, "中": 1, "低": 2}
    ordered_queue = sorted(reading_queue, key=lambda item: (priority_order[item.priority], -item.score, item.note.title))
    for index, result in enumerate(ordered_queue, start=1):
        lines.extend(render_result_block(result, index, include_reading_feedback=True))
    return "\n".join(lines).rstrip() + "\n"


def yaml_scalar(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if any(ch in text for ch in [":", "#", "[", "]", "{", "}", ",", "\"", "'"]) or text.startswith(("-", "@", "`")):
        import json

        return json.dumps(text, ensure_ascii=False)
    return text


def set_scalar_field(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}: {value}"
            return lines
    insert_at = 1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            insert_at = index
            break
    lines.insert(insert_at, f"{key}: {value}")
    return lines


def existing_list(frontmatter: dict[str, Any], key: str) -> list[str]:
    value = frontmatter.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip() not in {"", "[]"}]
    if value:
        if str(value).strip() == "[]":
            return []
        return [str(value)]
    return []


def set_list_field(lines: list[str], key: str, values: list[str]) -> list[str]:
    prefix = f"{key}:"
    start = None
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            start = index
            break
    rendered = [f"{key}:"] + [f"  - {yaml_scalar(value)}" for value in values]
    if start is None:
        insert_at = 1
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                insert_at = index
                break
        return lines[:insert_at] + rendered + lines[insert_at:]

    end = start + 1
    while end < len(lines) and lines[end].startswith("  - "):
        end += 1
    return lines[:start] + rendered + lines[end:]


def merge_unique(values: list[str], additions: list[str]) -> list[str]:
    result = list(values)
    for item in additions:
        if item and item not in result:
            result.append(item)
    return result


def build_sorted_source(note: InboxNote, result: TriageResult) -> str:
    lines = note.path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"来源笔记缺少 frontmatter：{note.path}")
    lines = set_scalar_field(lines, "处理状态", "已分拣")
    if PROJECT_DESTINATION in result.destinations:
        projects = merge_unique(existing_list(note.frontmatter, "关联项目"), ["个人数据资产系统"])
        lines = set_list_field(lines, "关联项目", projects)
    updated = "\n".join(lines).rstrip() + "\n"

    today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    summary = f"- {today}：已自动分拣。建议去向：{'、'.join(result.destinations)}。核心价值：{result.value}。下一步：先阅读原文，再在对话中反馈是否继续沉淀。"
    if summary in updated:
        return updated
    if "## 分拣记录" in updated:
        updated = re.sub(r"(## 分拣记录\n\n)", rf"\1{summary}\n", updated, count=1)
    elif "## 沉淀记录" in updated:
        updated = updated.replace("## 沉淀记录", f"## 分拣记录\n\n{summary}\n\n## 沉淀记录", 1)
    elif "## 原始链接" in updated:
        updated = updated.replace("## 原始链接", f"## 分拣记录\n\n{summary}\n\n## 原始链接", 1)
    else:
        updated = updated.rstrip() + f"\n\n## 分拣记录\n\n{summary}\n"
    return updated.rstrip() + "\n"


def write_back_sorted(results: list[TriageResult]) -> None:
    for result in results:
        updated = build_sorted_source(result.note, result)
        result.note.path.write_text(updated, encoding="utf-8")


def write_report(report: str, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f"收件箱分拣巡检-{dt.datetime.now(TZ).strftime('%Y-%m-%d-%H%M')}.md"
    path = report_dir / filename
    path.write_text(report, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Chinese triage report for my-mind inbox notes.")
    parser.add_argument("--inbox", default="00_收件箱", help="Inbox directory to scan.")
    parser.add_argument("--all", action="store_true", help="Include notes that are not marked 待分拣.")
    parser.add_argument("--write", action="store_true", help="Write the report to --report-dir.")
    parser.add_argument("--mark-sorted", action="store_true", help="Mark current 待分拣 notes as 已分拣 and append 分拣记录.")
    parser.add_argument("--report-dir", default="85_运行记录", help="Directory for written reports.")
    args = parser.parse_args()

    inbox = Path(args.inbox)
    if args.mark_sorted:
        notes = load_notes_by_status(inbox, {"待分拣"})
    elif args.all:
        notes = load_notes(inbox, include_all=True)
    else:
        notes = load_notes_by_status(inbox, {"待分拣"})
    results = [triage_note(note) for note in notes]
    if args.mark_sorted and results:
        write_back_sorted(results)
        reading_notes = load_notes_by_status(inbox, {"已分拣"})
    else:
        reading_notes = load_notes_by_status(inbox, {"已分拣"})
        if args.mark_sorted:
            pending_paths = {result.note.path for result in results}
            existing_paths = {note.path for note in reading_notes}
            for result in results:
                if result.note.path in pending_paths and result.note.path not in existing_paths:
                    reading_notes.append(result.note)
    reading_queue = [triage_note(note) for note in reading_notes]
    report = render_report(results, reading_queue, inbox, mark_sorted=args.mark_sorted)
    print(report, end="")
    if args.write:
        path = write_report(report, Path(args.report_dir))
        print(f"\n已写入：{path}")
    if args.mark_sorted and results:
        print(f"\n已自动分拣：{len(results)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
