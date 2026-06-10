#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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


def has_any(text: str, keywords: set[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


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
    text = "\n".join(
        [
            note.title,
            note.platform,
            str(note.frontmatter.get("作者或频道") or ""),
            tag_text,
            note.body[:4000],
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


def render_report(results: list[TriageResult], inbox: Path) -> str:
    now = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    counts = {"高": 0, "中": 0, "低": 0}
    for result in results:
        counts[result.priority] += 1

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
        "- 写入策略：只生成建议，不修改收件箱源文件。",
        "",
        "## 建议清单",
    ]

    if not results:
        lines.extend(["", "当前没有待分拣条目。"])
        return "\n".join(lines).rstrip() + "\n"

    priority_order = {"高": 0, "中": 1, "低": 2}
    ordered = sorted(results, key=lambda item: (priority_order[item.priority], -item.score, item.note.title))
    for index, result in enumerate(ordered, start=1):
        note = result.note
        lines.extend(
            [
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
                "- 风险：",
            ]
        )
        lines.extend(f"  - {risk}" for risk in result.risks)
    return "\n".join(lines).rstrip() + "\n"


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
    parser.add_argument("--report-dir", default="85_指标", help="Directory for written reports.")
    args = parser.parse_args()

    inbox = Path(args.inbox)
    notes = load_notes(inbox, include_all=args.all)
    results = [triage_note(note) for note in notes]
    report = render_report(results, inbox)
    print(report, end="")
    if args.write:
        path = write_report(report, Path(args.report_dir))
        print(f"\n已写入：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
