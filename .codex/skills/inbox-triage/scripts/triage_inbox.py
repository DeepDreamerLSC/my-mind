#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_FLOW_DIR = Path("05_流转区")
PROJECT_DESTINATION = "10_项目/个人数据资产系统/"
KNOWLEDGE_DESTINATION = "20_资料库/"
ATOMIC_DESTINATION = "30_原子笔记/"
PROMPT_DESTINATION = "75_提示词库/"
SIGNAL_DESTINATION = "60_行业情报/"
INSIGHT_DESTINATION = "65_洞察/候选洞察/"
KEEP_DESTINATION = "保持待分拣"
KNOWLEDGE_AI_INDUSTRY_DESTINATION = "20_资料库/人工智能产业/"
KNOWLEDGE_AI_TOOLS_DESTINATION = "20_资料库/AI产品与工具/"
KNOWLEDGE_WORKFLOW_DESTINATION = "20_资料库/工作流与自动化/"
KNOWLEDGE_VISUAL_DESTINATION = "20_资料库/设计与视觉/"
KNOWLEDGE_MANAGEMENT_DESTINATION = "20_资料库/管理与组织/"
KNOWLEDGE_WRITING_DESTINATION = "20_资料库/写作与表达/"
PROMPT_CAPTURE_DESTINATION = "75_提示词库/收集与录入/"
PROMPT_DISTILL_DESTINATION = "75_提示词库/萃取与整理/"
PROMPT_ANALYSIS_DESTINATION = "75_提示词库/分析与研究/"
PROMPT_PROJECT_DESTINATION = "75_提示词库/项目推进/"
PROMPT_WRITING_DESTINATION = "75_提示词库/写作与表达/"
PROMPT_VISUAL_DESTINATION = "75_提示词库/前端与视觉/"
PROMPT_CODEX_DESTINATION = "75_提示词库/Codex工作流/"
SIGNAL_MODEL_DESTINATION = "60_行业情报/模型与公司/"
SIGNAL_INFRA_DESTINATION = "60_行业情报/基础设施与算力/"
SIGNAL_MARKET_DESTINATION = "60_行业情报/市场与商业化/"

PROJECT_KEYWORDS = {
    "my-mind",
    "数据资产",
    "个人数据资产",
    "Codex",
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
BROAD_SIGNAL_KEYWORDS = {"公司", "产业", "趋势"}
VISUAL_KEYWORDS = {"视觉", "高级感", "网页", "PPT", "排版", "配色"}
INGEST_KEYWORDS = {"收件箱", "入箱", "录入", "capture", "剪藏"}
DISTILL_KEYWORDS = {"萃取", "提炼", "整理", "总结", "结构化", "对话"}
MANAGEMENT_KEYWORDS = {"管理", "团队", "组织", "领导", "管理者", "人才"}
WRITING_KEYWORDS = {"写作", "表达", "文章", "叙事"}
INFRA_KEYWORDS = {"NVIDIA", "Jensen", "Huang", "CUDA", "TSMC", "HBM", "数据中心", "算力", "芯片", "供应链", "电力"}
MARKET_KEYWORDS = {"商业", "市场", "客户", "定价", "收入", "营收", "adoption", "pricing"}
AI_TOOL_KEYWORDS = {"OpenAI", "Anthropic", "Claude", "Codex", "Agent", "ChatGPT", "MCP", "API"}
GENERIC_TOOL_KEYWORDS = {"产品", "工具", "模型"}


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


def has_destination(destinations: list[str], prefix: str) -> bool:
    return any(destination == prefix or destination.startswith(prefix) for destination in destinations)


def has_visual_material(text: str) -> bool:
    return any(keyword in text for keyword in ["视觉", "高级感", "网页", "排版", "配色"]) or bool(
        re.search(r"\b(html|ppt)\b", text.lower())
    )


def has_signal_material(text: str) -> bool:
    specific_keywords = SIGNAL_KEYWORDS - BROAD_SIGNAL_KEYWORDS
    if has_any(text, specific_keywords):
        return True
    return has_any(text, BROAD_SIGNAL_KEYWORDS) and not has_any(text, MANAGEMENT_KEYWORDS)


def has_ai_tool_material(text: str) -> bool:
    lowered = text.lower()
    if has_any(text, AI_TOOL_KEYWORDS):
        return True
    ai_context = bool(re.search(r"(?<![a-z])ai(?![a-z])", lowered)) or "人工智能" in text or "智能体" in text
    return ai_context and has_any(text, GENERIC_TOOL_KEYWORDS)


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
        if line.startswith("如果和 Codex 交流过"):
            continue
        if line.startswith("如果和 Codex 交流过，"):
            continue
        if line in {
            "待补充。",
            "待补充",
            "暂无。",
            "暂无",
            "待阅读后补充。",
            "待阅读后补充",
            "待阅读后再补充。",
            "待阅读后再补充",
            "待阅读后填写。",
            "待阅读后填写",
        }:
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


def markdown_relative_link(target: Path, base_dir: Path) -> str:
    rel = Path(target).resolve()
    try:
        link = rel.relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        import os

        link = Path(os.path.relpath(rel, start=base_dir.resolve())).as_posix()
    return quote(link, safe="/%")


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
    if has_destination(destinations, PROMPT_CODEX_DESTINATION) and has_destination(destinations, PROJECT_DESTINATION):
        return "与 Codex 工作流、提示词或自动化直接相关，适合先进入 `75_提示词库/Codex工作流/`，再反哺个人数据资产系统。"
    if has_destination(destinations, PROMPT_VISUAL_DESTINATION):
        return "包含可复用的视觉约束或界面生成经验，适合沉淀到 `75_提示词库/前端与视觉/`。"
    if has_destination(destinations, SIGNAL_DESTINATION):
        return "包含 AI 产业、公司、模型或技术趋势信息，适合作为行业信号和资料库素材。"
    if has_destination(destinations, KNOWLEDGE_MANAGEMENT_DESTINATION):
        return "属于管理和组织方法素材，适合先进入 `20_资料库/管理与组织/`，再判断是否拆成原子笔记。"
    if has_destination(destinations, KNOWLEDGE_WORKFLOW_DESTINATION):
        return "和流程设计、自动化方法或协作闭环相关，适合先进入 `20_资料库/工作流与自动化/`。"
    if note.frontmatter.get("外部转录链接"):
        return "存在可追溯转录来源，适合先整理为资料库条目，再提炼观点和事实。"
    if note.frontmatter.get("内容摘录来源"):
        return "已经有公开视频转写摘录，适合先做资料库草稿和重点提取。"
    if note.platform in {"抖音", "小红书", "X"}:
        return "社媒内容已保留平台文案和互动数据，适合快速判断是否值得进一步沉淀。"
    return "已捕获基础信息，可作为资料库候选等待进一步整理。"


def suggest_next_step(note: InboxNote, destinations: list[str], risks: list[str]) -> str:
    title = note.title
    if has_destination(destinations, PROMPT_VISUAL_DESTINATION):
        return f"从《{title}》中提取可复用视觉约束或界面生成提示词，先写入 `75_提示词库/前端与视觉/` 候选，再决定是否补 `20_资料库/设计与视觉/`。"
    if has_destination(destinations, PROMPT_CODEX_DESTINATION):
        return f"从《{title}》中提取可复用提示词或协作流程，先写入 `75_提示词库/Codex工作流/` 草稿，并把对 inbox/skill 的改进点加入项目任务候选。"
    if has_destination(destinations, PROMPT_CAPTURE_DESTINATION):
        return f"先把《{title}》中的录入或入箱方法整理到 `75_提示词库/收集与录入/`，再判断是否需要回写项目流程。"
    if has_destination(destinations, PROMPT_DISTILL_DESTINATION):
        return f"先把《{title}》中的萃取或整理方法整理到 `75_提示词库/萃取与整理/`，再判断是否需要扩展到项目流程。"
    if has_destination(destinations, SIGNAL_DESTINATION):
        return f"先整理《{title}》的来源、关键事实和可验证判断，放入 `20_资料库/人工智能产业/`；涉及趋势判断的部分再进入对应的行业情报子目录或候选洞察。"
    if has_destination(destinations, KNOWLEDGE_MANAGEMENT_DESTINATION):
        return f"先整理《{title}》的核心观点到 `20_资料库/管理与组织/`，确认长期可复用后再拆成 `30_原子笔记/`。"
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
    if has_signal_material(note.title + "\n" + note.body[:2000]):
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
        if has_visual_material(text):
            destinations.extend([PROMPT_VISUAL_DESTINATION, KNOWLEDGE_VISUAL_DESTINATION])
        elif has_any(text, INGEST_KEYWORDS):
            destinations.extend([PROMPT_CAPTURE_DESTINATION, PROJECT_DESTINATION])
        elif has_any(text, DISTILL_KEYWORDS):
            destinations.extend([PROMPT_DISTILL_DESTINATION, PROJECT_DESTINATION])
        elif has_any(text, WRITING_KEYWORDS):
            destinations.extend([PROMPT_WRITING_DESTINATION, KNOWLEDGE_WRITING_DESTINATION])
        else:
            destinations.extend([PROMPT_CODEX_DESTINATION, PROJECT_DESTINATION, KNOWLEDGE_WORKFLOW_DESTINATION])
        score += 3
    if is_project_material(text):
        if PROJECT_DESTINATION not in destinations:
            destinations.append(PROJECT_DESTINATION)
        score += 2
    signal_material = has_signal_material(text)

    if has_any(text, MANAGEMENT_KEYWORDS):
        destinations.extend([KNOWLEDGE_MANAGEMENT_DESTINATION, ATOMIC_DESTINATION])
        score += 2
    if has_ai_tool_material(text) and not signal_material and not is_prompt_material(text):
        destinations.append(KNOWLEDGE_AI_TOOLS_DESTINATION)
        score += 1
    if signal_material:
        destinations.append(KNOWLEDGE_AI_INDUSTRY_DESTINATION)
        if has_any(text, INFRA_KEYWORDS):
            destinations.append(SIGNAL_INFRA_DESTINATION)
        elif has_any(text, MARKET_KEYWORDS):
            destinations.append(SIGNAL_MARKET_DESTINATION)
        else:
            destinations.append(SIGNAL_MODEL_DESTINATION)
        score += 2
    if note.frontmatter.get("外部转录链接"):
        if KNOWLEDGE_AI_INDUSTRY_DESTINATION not in destinations:
            destinations.append(KNOWLEDGE_AI_INDUSTRY_DESTINATION)
        score += 2
    if note.frontmatter.get("内容摘录来源"):
        if KNOWLEDGE_AI_INDUSTRY_DESTINATION not in destinations and KNOWLEDGE_AI_TOOLS_DESTINATION not in destinations:
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
        f"- 已分拣流转数量：{len(reading_queue)}",
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

    lines.extend(["", "## 已分拣流转队列"])
    if not reading_queue:
        lines.extend(["", "当前没有等待用户阅读反馈或后续确认的已分拣条目。"])
        return "\n".join(lines).rstrip() + "\n"

    priority_order = {"高": 0, "中": 1, "低": 2}
    ordered_queue = sorted(reading_queue, key=lambda item: (priority_order[item.priority], -item.score, item.note.title))
    for index, result in enumerate(ordered_queue, start=1):
        lines.extend(render_result_block(result, index, include_reading_feedback=True))
    return "\n".join(lines).rstrip() + "\n"


def source_url(note: InboxNote) -> str:
    for field in ["来源链接", "原始链接", "外部转录链接"]:
        value = str(note.frontmatter.get(field) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    match = re.search(r"https?://[^\s)>\"]+", note.body)
    return match.group(0).rstrip("。，,；;") if match else ""


def needs_verification(result: TriageResult) -> bool:
    risk_text = "\n".join(result.risks)
    return any(
        keyword in risk_text
        for keyword in [
            "需要确认",
            "需要校对",
            "事实核验",
            "转写质量",
            "字段可能",
            "官方转录",
            "内容较长",
            "产业判断",
        ]
    )


def render_flow_item(result: TriageResult, index: int, target_path: Path) -> list[str]:
    note = result.note
    phase, candidate_paths, strategy = summarize_pending_stage(note)
    source = source_url(note)
    note_link = markdown_relative_link(note.path, target_path.parent)
    lines = [
        f"### {index}. {note.title}",
        "",
        f"- 优先级：{result.priority}",
        f"- 当前阶段：{phase}",
        f"- 建议去向：{'、'.join(result.destinations)}",
        f"- 核心价值：{result.value}",
        f"- 下一步：{result.next_step}",
        f"- 建议处理策略：{strategy}",
        f"- 来源文件：[{repo_relative_path(note.path)}]({note_link})",
        f"- 原文链接：{source or '暂无'}",
        f"- 已有候选：{'、'.join(f'`{path}`' for path in candidate_paths) if candidate_paths else '暂无'}",
        f"- 读后反馈：{READING_FEEDBACK_PROMPT}",
        "- 风险：",
    ]
    lines.extend(f"  - {risk}" for risk in result.risks)
    lines.append("")
    return lines


def render_flow_view(title: str, description: str, items: list[TriageResult], target_path: Path) -> str:
    generated_at = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    lines = [
        f"# {title}",
        "",
        description,
        "",
        "## 使用边界",
        "",
        "- 这是流转视图，不是长期知识正文。",
        "- 文件可由 `inbox-triage` 自动覆盖；真正的来源事实仍以收件箱原文和原始链接为准。",
        "- 用户读完后的判断应回写到来源笔记的 `阅读思考`，再触发候选沉淀。",
        "",
        "## 总览",
        "",
        f"- 更新时间：{generated_at}",
        f"- 条目数量：{len(items)}",
        "",
        "## 队列",
        "",
    ]
    if not items:
        lines.append("当前没有条目。")
        return "\n".join(lines).rstrip() + "\n"

    priority_order = {"高": 0, "中": 1, "低": 2}
    ordered = sorted(items, key=lambda item: (priority_order[item.priority], -item.score, item.note.title))
    for index, result in enumerate(ordered, start=1):
        lines.extend(render_flow_item(result, index, target_path))
    return "\n".join(lines).rstrip() + "\n"


def render_flow_overview(
    reading_items: list[TriageResult],
    distill_items: list[TriageResult],
    verification_items: list[TriageResult],
    flow_dir: Path,
) -> str:
    generated_at = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    return "\n".join(
        [
            "# 当前流转总览",
            "",
            "这里是 `my-mind` 的短期行动面板，用来承接收件箱和长期知识之间的中间状态。",
            "",
            "## 当前队列",
            "",
            f"- 更新时间：{generated_at}",
            f"- 待读：{len(reading_items)}",
            f"- 待沉淀：{len(distill_items)}",
            f"- 待核验：{len(verification_items)}",
            "",
            "## 队列入口",
            "",
            f"- [收件箱待读队列]({markdown_relative_link(flow_dir / '10_待读' / '收件箱待读队列.md', flow_dir)})",
            f"- [收件箱待沉淀队列]({markdown_relative_link(flow_dir / '30_待沉淀' / '收件箱待沉淀队列.md', flow_dir)})",
            f"- [收件箱待核验队列]({markdown_relative_link(flow_dir / '40_待核验' / '收件箱待核验队列.md', flow_dir)})",
            "",
            "## 规则",
            "",
            "- 收件箱保存原始捕获，不因为进入流转区而移动或删除。",
            "- 流转区只保存行动视图，可以被自动刷新。",
            "- 长期知识仍进入资料库、原子笔记、行业情报、洞察、提示词库或项目目录。",
        ]
    ).rstrip() + "\n"


def write_flow_views(reading_queue: list[TriageResult], flow_dir: Path) -> list[Path]:
    reading_items: list[TriageResult] = []
    distill_items: list[TriageResult] = []
    verification_items: list[TriageResult] = []

    for result in reading_queue:
        phase, _, _ = summarize_pending_stage(result.note)
        if phase in {"已阅读待生成候选", "已有候选待确认"}:
            distill_items.append(result)
        else:
            reading_items.append(result)
        if needs_verification(result):
            verification_items.append(result)

    paths = [
        flow_dir / "当前流转总览.md",
        flow_dir / "10_待读" / "收件箱待读队列.md",
        flow_dir / "30_待沉淀" / "收件箱待沉淀队列.md",
        flow_dir / "40_待核验" / "收件箱待核验队列.md",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)

    paths[0].write_text(render_flow_overview(reading_items, distill_items, verification_items, flow_dir), encoding="utf-8")
    paths[1].write_text(
        render_flow_view("收件箱待读队列", "已完成分拣、等待用户阅读和反馈的资料。", reading_items, paths[1]),
        encoding="utf-8",
    )
    paths[2].write_text(
        render_flow_view("收件箱待沉淀队列", "已有阅读反馈或候选草稿、等待继续沉淀或确认的资料。", distill_items, paths[2]),
        encoding="utf-8",
    )
    paths[3].write_text(
        render_flow_view("收件箱待核验队列", "存在解析、转写、来源或事实风险，正式沉淀前需要校对的资料。", verification_items, paths[3]),
        encoding="utf-8",
    )
    return paths


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
    value = result.value.rstrip("。；; ")
    summary = f"- {today}：已自动分拣。建议去向：{'、'.join(result.destinations)}。核心价值：{value}。下一步：先阅读原文，再在对话中反馈是否继续沉淀。"
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
    parser.add_argument("--flow-dir", default=str(DEFAULT_FLOW_DIR), help="Directory for refreshed flow views.")
    parser.add_argument("--no-flow", action="store_true", help="Do not refresh 05_流转区 views when --write is used.")
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
        if not args.no_flow:
            flow_paths = write_flow_views(reading_queue, Path(args.flow_dir))
            print("\n已更新流转区：")
            for flow_path in flow_paths:
                print(f"- {flow_path}")
    if args.mark_sorted and results:
        print(f"\n已自动分拣：{len(results)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
