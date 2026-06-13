#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")

INBOX_DIR = ROOT / "00_收件箱"
FLOW_DIR = ROOT / "05_流转区"
RUN_DIR = ROOT / "85_运行记录"
PROJECT_ROOT = ROOT / "10_项目"

CAPTURE_SCRIPT = ROOT / ".codex/skills/inbox-capture/scripts/capture_link.py"
TRIAGE_SCRIPT = ROOT / ".codex/skills/inbox-triage/scripts/triage_inbox.py"

LIBRARY_ROOT = ROOT / "20_资料库"
PROMPT_ROOT = ROOT / "75_提示词库"
INSIGHT_ROOT = ROOT / "65_洞察/候选洞察"

DEFAULT_INTAKE_MAX_TRANSCRIBE_SECONDS = 3600

PROMOTION_GATE_ITEMS = [
    "原始来源已保留，来源链接或来源文件可追溯。",
    "解析质量足够，视频转写、OCR 或摘录没有明显空缺。",
    "关键术语已经校对，英文产品名、人名和工具名没有明显误识别。",
    "正文已用简体中文重写，区分来源事实、我的判断和待验证内容。",
    "至少有一条可复用结论、提示词、项目影响或行动建议。",
    "已建立必要的项目、领域或主题关联。",
]


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    overview: Path
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class ProjectImpact:
    project: str
    project_link: str
    strength: str
    reason: str
    suggestions: tuple[str, ...]
    questions: tuple[str, ...]


@dataclass
class Candidate:
    source: Path
    target: Path
    kind: str
    title: str
    status: str
    reason: str
    content: str = ""
    project_impacts: list[ProjectImpact] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


@dataclass
class IntakeRun:
    captured: list[Path] = field(default_factory=list)
    sources: list[Path] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    deferred: list[Candidate] = field(default_factory=list)
    flow_paths: list[Path] = field(default_factory=list)
    capture_output: str = ""


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


triage = load_module(TRIAGE_SCRIPT, "my_mind_inbox_triage")


def now_date() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_repo_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def sanitize_filename(text: str, fallback: str = "未命名") -> str:
    value = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", text or "").strip()
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or fallback)[:80].rstrip()


def yaml_scalar(value: Any) -> str:
    if value is None or value == "":
        return '""'
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if text in {"[]", "{}"}:
        return text
    if any(ch in text for ch in [":", "#", "[", "]", "{", "}", ",", "\"", "'"]) or text.startswith(("-", "@", "`")):
        return json.dumps(text, ensure_ascii=False)
    return text


def is_url(text: str) -> bool:
    return bool(re.match(r"https?://", text.strip()))


def split_materials(raw: list[str]) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    snippets: list[str] = []
    for item in raw:
        value = item.strip()
        if not value:
            continue
        if is_url(value):
            urls.append(value)
        else:
            snippets.append(value)
    return urls, snippets


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(2, 1000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一文件名：{path}")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_section(body: str, heading: str) -> str:
    return triage.extract_section(body, heading)


def normalize_placeholder(text: str) -> str:
    placeholders = {
        "待补充。",
        "待补充",
        "待阅读后补充。",
        "已读，待补充读后判断。",
        "暂无。",
        "暂无",
    }
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        clean = line[1:].strip() if line.startswith(("-", "*")) else line
        if clean in placeholders:
            continue
        lines.append(raw)
    return "\n".join(lines).strip()


def source_text(note: Any, limit: int = 5000) -> str:
    body = note.body
    parts = []
    for heading in [
        "中文摘要",
        "摘要",
        "关键点",
        "文案摘录",
        "视频内容摘录",
        "图片文字 OCR",
        "原文摘录",
        "阅读思考",
        "初步想法",
    ]:
        section = extract_section(body, heading)
        if section:
            parts.append(f"## {heading}\n\n{section}")
    text = "\n\n".join(parts).strip() or body[:limit]
    return text[:limit].rstrip()


def has_enough_content(note: Any) -> bool:
    quality = str(note.frontmatter.get("内容质量") or "")
    parse_status = str(note.frontmatter.get("解析状态") or "")
    if quality == "需继续解析":
        return False
    if parse_status == "解析失败":
        return False
    text = source_text(note, 1200)
    return len(normalize_placeholder(text)) >= 80


def load_note(path: Path) -> Any:
    text = read_text(path)
    frontmatter, body = triage.parse_frontmatter(text)
    return triage.InboxNote(path=path, frontmatter=frontmatter, body=body)


def capture_urls(urls: list[str], *, write: bool, reading_status: str, extra_args: list[str]) -> tuple[list[Path], str]:
    if not urls:
        return [], ""
    command = [
        sys.executable,
        str(CAPTURE_SCRIPT),
        "--reading-status",
        reading_status,
        *extra_args,
    ]
    if not write:
        command.append("--dry-run")
    command.extend(urls)
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"inbox-capture 失败，退出码 {completed.returncode}：{output}")
    paths: list[Path] = []
    if write:
        for line in completed.stdout.splitlines():
            value = line.strip()
            if value.endswith(".md"):
                paths.append(resolve_repo_path(value))
    return paths, output


def render_plain_inbox_note(title: str, text: str, reading_status: str) -> str:
    quality = "可推送" if len(text.strip()) >= 120 else "需核验"
    gate = "用户直接提交的文本，信息量足够，可进入入库候选处理。" if quality == "可推送" else "文本较短，需要补充上下文后再沉淀。"
    title = title or sanitize_filename(text[:40], "文本入库")
    lines = [
        "---",
        "类别: 收件箱",
        "来源类型: 原始文本",
        "来源平台: 手动输入",
        f"标题: {yaml_scalar(title)}",
        f"捕获时间: {now_datetime()}",
        "解析工具: knowledge-intake",
        "解析状态: 已解析",
        f"内容质量: {quality}",
        f"质量门禁: {yaml_scalar(gate)}",
        f"阅读状态: {reading_status}",
        "处理状态: 待分拣",
        "关联项目: []",
        "关联领域: []",
        "主题: []",
        "标签:",
        "  - 入库",
        "  - 手动输入",
        "敏感状态: 未知",
        "---",
        f"# {title}",
        "",
        "## 原始文本",
        "",
        text.strip(),
        "",
        "## 为什么保存",
        "",
        "- 用户主动要求入库，默认视为有潜在沉淀价值。",
        "",
        "## 初步想法",
        "",
        "- 待入库处理生成候选。",
        "",
        "## 阅读思考",
        "",
        "- 已读，待补充读后判断。" if reading_status == "已读" else "- 待阅读后补充。",
        "",
        "## 后续处理建议",
        "",
        "- 先分拣，再按质量门禁生成候选知识或确认问题。",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def capture_snippets(snippets: list[str], *, write: bool, title: str, reading_status: str) -> list[Path]:
    paths: list[Path] = []
    for index, snippet in enumerate(snippets, start=1):
        note_title = title if len(snippets) == 1 and title else f"{title or '文本入库'}-{index}"
        path = INBOX_DIR / f"{now_date()} 手动输入 - {sanitize_filename(note_title)}.md"
        path = unique_path(path)
        paths.append(path)
        if write:
            INBOX_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(render_plain_inbox_note(note_title, snippet, reading_status), encoding="utf-8")
    return paths


def looks_like_prompt_candidate(note: Any) -> bool:
    source_body = triage.strip_management_sections(note.body)
    text = f"{note.title}\n{source_body[:3000]}".lower()
    return any(
        keyword in text
        for keyword in [
            "提示词",
            "prompt",
            "skill",
            "system prompt",
            "提示词库",
            "工作流提示",
        ]
    )


def destination_for_result(note: Any, result: Any, explicit_target: str) -> tuple[str, Path, str]:
    destinations = result.destinations
    if explicit_target == "prompt" or (
        explicit_target == "auto" and looks_like_prompt_candidate(note) and any(dest.startswith("75_提示词库/") for dest in destinations)
    ):
        for dest in destinations:
            if dest.startswith("75_提示词库/"):
                return "prompt", ROOT / dest.rstrip("/"), "分拣建议进入提示词库。"
        return "prompt", PROMPT_ROOT / "分析与研究", "用户显式要求生成提示词候选。"
    if explicit_target == "insight":
        return "insight", INSIGHT_ROOT, "用户显式要求生成候选洞察。"
    if explicit_target == "library":
        for dest in destinations:
            if dest.startswith("20_资料库/"):
                return "library", ROOT / dest.rstrip("/"), "用户显式要求进入资料库，采用分拣资料目录。"
        return "library", LIBRARY_ROOT, "用户显式要求进入资料库。"
    for dest in destinations:
        if dest.startswith("65_洞察/"):
            return "insight", ROOT / dest.rstrip("/"), "分拣建议包含候选洞察。"
    for dest in destinations:
        if dest.startswith("20_资料库/"):
            return "library", ROOT / dest.rstrip("/"), "分拣建议进入资料库。"
    return "library", LIBRARY_ROOT, "默认作为资料库候选保留。"


def source_url(note: Any) -> str:
    for field in ["来源链接", "原始链接", "外部转录链接"]:
        value = str(note.frontmatter.get(field) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    match = re.search(r"https?://[^\s)>\"]+", note.body)
    return match.group(0).rstrip("。，,；;") if match else ""


def list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip() and str(item).strip() != "[]"]
    if value and str(value).strip() != "[]":
        return [str(value)]
    return []


def strip_wikilink(value: str) -> str:
    text = str(value).strip()
    match = re.fullmatch(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", text)
    if not match:
        return text
    return (match.group(2) or Path(match.group(1)).name).strip()


def project_link(project: ProjectInfo) -> str:
    rel = Path(repo_relative(project.overview)).with_suffix("").as_posix()
    return f"[[{rel}|{project.name}]]"


def load_projects() -> list[ProjectInfo]:
    projects: list[ProjectInfo] = []
    if not PROJECT_ROOT.exists():
        return projects
    system_keywords = {
        "个人数据资产系统": [
            "my-mind",
            "Obsidian",
            "知识库",
            "收件箱",
            "流转区",
            "分拣",
            "飞书",
            "OpenClaw",
            "Codex",
            "skill",
            "自动化",
            "前沿情报",
            "项目知识面板",
        ]
    }
    for directory in sorted(path for path in PROJECT_ROOT.iterdir() if path.is_dir()):
        overview = directory / "项目总览.md"
        if not overview.exists():
            continue
        name = directory.name
        keywords = [name, *re.split(r"[-_\s]+", name), *system_keywords.get(name, [])]
        keywords = [item for item in dict.fromkeys(keywords) if item and len(item) >= 2]
        projects.append(ProjectInfo(name=name, overview=overview, keywords=tuple(keywords)))
    return projects


def signal_matches(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    matches = []
    for keyword in keywords:
        if keyword.lower() in lowered:
            matches.append(keyword)
    return list(dict.fromkeys(matches))


def infer_project_impacts(note: Any, result: Any, kind: str) -> list[ProjectImpact]:
    projects = load_projects()
    if not projects:
        return []

    project_values = [strip_wikilink(item) for item in list_values(note.frontmatter.get("关联项目"))]
    project_values.extend(strip_wikilink(item) for item in list_values(note.frontmatter.get("项目")))
    body_for_project = triage.strip_management_sections(note.body)[:5000]
    text = f"{note.title}\n{body_for_project}\n{result.value}".strip()

    impacts: list[ProjectImpact] = []
    for project in projects:
        explicit = any(project.name == value or project.name in value for value in project_values)
        keyword_hits = signal_matches(text, list(project.keywords))
        if not explicit and not keyword_hits:
            continue
        impacts.append(build_project_impact(project, text, kind, explicit=explicit, keyword_hits=keyword_hits))

    if not impacts and len(projects) == 1:
        project = projects[0]
        generic_hits = signal_matches(text, ["项目", "任务", "决策", "风险", "复盘", "管理", "流程", "工作流"])
        strength = "中" if generic_hits else "弱"
        reason = "当前只有一个进行中项目，先作为项目背景资料候选观察。"
        if generic_hits:
            reason = "材料包含项目管理或行动信号，但未明确指向具体项目。"
        impacts.append(build_project_impact(project, text, kind, explicit=False, keyword_hits=generic_hits, fallback_strength=strength, fallback_reason=reason))
    return impacts[:3]


def build_project_impact(
    project: ProjectInfo,
    text: str,
    kind: str,
    *,
    explicit: bool,
    keyword_hits: list[str],
    fallback_strength: str | None = None,
    fallback_reason: str | None = None,
) -> ProjectImpact:
    action_hits = signal_matches(text, ["任务", "下一步", "行动", "落地", "执行", "实现", "优化"])
    decision_hits = signal_matches(text, ["决策", "原则", "取舍", "策略", "方向", "边界"])
    risk_hits = signal_matches(text, ["风险", "问题", "限制", "阻塞", "卡住", "失败", "误判", "隐私", "敏感"])
    prompt_hits = signal_matches(text, ["提示词", "prompt", "Codex", "OpenClaw", "skill", "工作流"])

    if fallback_strength:
        strength = fallback_strength
    elif explicit or len(keyword_hits) >= 2:
        strength = "强"
    elif keyword_hits or action_hits or decision_hits or risk_hits or prompt_hits:
        strength = "中"
    else:
        strength = "弱"

    if fallback_reason:
        reason = fallback_reason
    elif explicit:
        reason = "来源已显式关联该项目。"
    else:
        reason = "命中项目关键词：" + "、".join(keyword_hits[:6])

    suggestions: list[str] = []
    questions: list[str] = []
    if action_hits:
        suggestions.append("可作为任务候选，确认后再写入任务清单。")
    if decision_hits or kind == "insight":
        suggestions.append("可作为决策候选，确认后再写入决策记录。")
    if risk_hits:
        suggestions.append("可作为风险提醒或问题候选，确认后再写入风险清单或问题清单。")
    if prompt_hits or kind == "prompt":
        suggestions.append("可试跑为项目提示词，稳定后再写入项目提示词。")
    if not suggestions:
        suggestions.append("先作为项目背景资料保留关联，不自动生成项目动作。")

    if strength in {"强", "中"} and suggestions[0] != "先作为项目背景资料保留关联，不自动生成项目动作。":
        questions.append(f"是否把这条材料对「{project.name}」的影响转成任务、决策、风险或项目提示词？")
    return ProjectImpact(
        project=project.name,
        project_link=project_link(project),
        strength=strength,
        reason=reason,
        suggestions=tuple(dict.fromkeys(suggestions)),
        questions=tuple(dict.fromkeys(questions)),
    )


def render_project_impact_lines(project_impacts: list[ProjectImpact]) -> list[str]:
    if not project_impacts:
        return ["- 暂无明确项目影响建议。"]
    lines: list[str] = []
    for impact in project_impacts:
        lines.extend(
            [
                f"- 项目：{impact.project_link}",
                f"  - 关联强度：{impact.strength}",
                f"  - 判断依据：{impact.reason}",
                "  - 建议动作：",
            ]
        )
        lines.extend(f"    - {suggestion}" for suggestion in impact.suggestions)
        if impact.questions:
            lines.append("  - 待确认：")
            lines.extend(f"    - {question}" for question in impact.questions)
    return lines


def render_promotion_gate_lines() -> list[str]:
    lines = [
        "- 当前状态：候选待确认，不能直接视为长期知识。",
        "- 转正前检查：",
    ]
    lines.extend(f"  - {item}" for item in PROMOTION_GATE_ITEMS)
    lines.extend(
        [
            "- 通过后动作：把 `处理状态` 改为 `已处理`，把 `吸收状态` 改为 `已吸收` 或 `已应用`，必要时开启飞书精选同步。",
            "- 未通过动作：保留候选，回到来源继续解析、校对、补充我的判断或降级为资料暂存。",
        ]
    )
    return lines


def infer_topics(note: Any, result: Any) -> list[str]:
    topics = list_values(note.frontmatter.get("主题"))
    text = f"{note.title}\n{note.body[:2400]}"
    additions: list[str] = []
    if "Codex" in text or "codex" in text.lower():
        additions.append("Codex")
    if "提示词" in text or "prompt" in text.lower():
        additions.append("提示词")
    if "管理" in text or "团队" in text:
        additions.append("管理")
    if "AI" in text or "人工智能" in text or "OpenAI" in text:
        additions.append("人工智能")
    for dest in result.destinations:
        if dest.startswith("20_资料库/"):
            additions.append(Path(dest).name)
        elif dest.startswith("75_提示词库/"):
            additions.append(Path(dest).name)
    merged: list[str] = []
    for item in [*topics, *additions]:
        if item and item not in merged:
            merged.append(item)
    return merged[:8]


def frontmatter_block(
    kind: str,
    note: Any,
    result: Any,
    title: str,
    status: str,
    project_impacts: list[ProjectImpact],
) -> list[str]:
    source = repo_relative(note.path)
    url = source_url(note)
    topics = infer_topics(note, result)
    category = {"library": "资料", "prompt": "提示词", "insight": "洞察"}.get(kind, "资料")
    related_projects = [impact.project_link for impact in project_impacts] or [
        "[[10_项目/个人数据资产系统/项目总览|个人数据资产系统]]"
    ]
    lines = [
        "---",
        f"类别: {category}",
        "处理状态: 候选",
        "吸收状态: 待确认",
        "可信状态: 待核验",
        f"标题: {yaml_scalar(title)}",
        f"生成时间: {now_datetime()}",
        "生成方式: knowledge-intake",
        f"来源文件: {yaml_scalar(source)}",
        f"来源链接: {yaml_scalar(url)}",
        f"来源平台: {yaml_scalar(note.platform or '未知')}",
        f"作者: {yaml_scalar(note.frontmatter.get('作者或频道') or '未知')}",
        "关联项目:",
    ]
    lines.extend(f"  - {yaml_scalar(project)}" for project in related_projects)
    lines.append("关联领域:")
    if kind == "prompt":
        lines.append("  - 人工智能协作")
    elif kind == "insight":
        lines.append("  - 候选洞察")
    else:
        lines.append("  - 资料库候选")
    lines.append("主题:")
    lines.extend([f"  - {yaml_scalar(item)}" for item in (topics or ["待补充"])])
    if project_impacts:
        primary = project_impacts[0]
        lines.extend(
            [
                "项目影响:",
                f"  项目: {yaml_scalar(primary.project_link)}",
                f"  关联强度: {yaml_scalar(primary.strength)}",
                f"  判断依据: {yaml_scalar(primary.reason)}",
                "  建议动作:",
            ]
        )
        lines.extend(f"    - {yaml_scalar(suggestion)}" for suggestion in primary.suggestions)
    lines.extend(
        [
            "转正门禁:",
            "  状态: \"待检查\"",
            "  检查项:",
        ]
    )
    lines.extend(f"    - {yaml_scalar(item)}" for item in PROMOTION_GATE_ITEMS)
    lines.extend(
        [
            "  阻塞原因:",
            "    - \"候选待确认，尚未通过长期知识转正门禁\"",
            "  最近检查: \"\"",
            "  检查者: \"Codex后台\"",
        ]
    )
    lines.extend(
        [
            "标签:",
            f"  - {category}",
            "  - 候选",
            "  - knowledge-intake",
            "需要人工确认: 是",
            "敏感状态: 未知",
        ]
    )
    if kind == "library":
        lines.extend(
            [
                "飞书同步:",
                "  策略: \"不同步\"",
                "  状态: \"暂停\"",
                "  飞书页面: \"\"",
                "  页面Token: \"\"",
                "  Wiki节点: \"\"",
                "  最近同步: \"\"",
                "  内容哈希: \"\"",
                "  最近错误: \"候选待确认，暂不同步到飞书精选知识库\"",
            ]
        )
    lines.append("---")
    return lines


def render_library_candidate(note: Any, result: Any, title: str, project_impacts: list[ProjectImpact]) -> str:
    text = source_text(note, 6000)
    lines = [
        *frontmatter_block("library", note, result, title, "候选", project_impacts),
        f"# {title}",
        "",
        "## 资料背景",
        "",
        f"这是一条由 `knowledge-intake` 从收件箱来源自动生成的资料候选。来源平台为 {note.platform or '未知'}，正式沉淀前仍需人工确认核心事实和表达。",
        "",
        "## 原始来源",
        "",
        f"- 收件箱来源：`{repo_relative(note.path)}`",
        f"- 原文链接：{source_url(note) or '暂无'}",
        f"- 作者或频道：{note.frontmatter.get('作者或频道') or '未知'}",
        "",
        "## 核心内容摘要",
        "",
        normalize_placeholder(extract_section(note.body, "中文摘要") or extract_section(note.body, "摘要") or result.value),
        "",
        "## 可复用资料点",
        "",
        normalize_placeholder(extract_section(note.body, "关键点") or "- 待人工整理为稳定资料点。"),
        "",
        "## 来源摘录",
        "",
        text or "暂无可用摘录。",
        "",
        "## 我的理解",
        "",
        normalize_placeholder(extract_section(note.body, "阅读思考") or "待补充。"),
        "",
        "## 项目影响建议",
        "",
        *render_project_impact_lines(project_impacts),
        "",
        "## 长期知识转正",
        "",
        *render_promotion_gate_lines(),
        "",
        "## 待验证事实",
        "",
    ]
    lines.extend(f"- {risk}" for risk in result.risks)
    lines.extend(
        [
            "",
            "## 后续动作",
            "",
            "- 确认是否保留为长期资料。",
            "- 必要时回到原文、转写或 OCR 结果核验关键事实。",
            "- 确认后再考虑拆成原子笔记、洞察或提示词。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_prompt_candidate(note: Any, result: Any, title: str, project_impacts: list[ProjectImpact]) -> str:
    text = source_text(note, 5000)
    lines = [
        *frontmatter_block("prompt", note, result, title, "候选", project_impacts),
        f"# {title}",
        "",
        "## 用途",
        "",
        "把来源材料中的方法、流程或提示词经验整理成可试用的人工智能协作提示词候选。",
        "",
        "## 来源",
        "",
        f"- 收件箱来源：`{repo_relative(note.path)}`",
        f"- 原文链接：{source_url(note) or '暂无'}",
        "",
        "## 适用场景",
        "",
        f"- {result.value}",
        "- 当相似任务再次出现时，先试用这条候选，再决定是否升级为正式提示词或 skill。",
        "",
        "## 提示词候选",
        "",
        f"请基于《{note.title}》中的方法，帮我处理当前任务。",
        "",
        "要求：",
        "",
        "1. 先判断当前任务属于收集、分拣、沉淀、项目推进还是复盘。",
        "2. 提取可复用步骤，不照搬来源表述。",
        "3. 区分来源事实、我的判断和仍需核验的内容。",
        "4. 给出最小可执行动作，并说明需要用户确认的点。",
        "5. 不自动提交、推送、删除或确认长期知识。",
        "",
        "## 来源要点",
        "",
        text or "待补充。",
        "",
        "## 使用建议",
        "",
        "- 先在真实任务里试跑 1 到 2 次。",
        "- 如果稳定有用，再升级为正式提示词或 `.codex/skills/`。",
        "- 如果只是单次经验，保留为资料候选即可。",
        "",
        "## 项目影响建议",
        "",
        *render_project_impact_lines(project_impacts),
        "",
        "## 长期知识转正",
        "",
        *render_promotion_gate_lines(),
        "",
        "## 风险",
        "",
    ]
    lines.extend(f"- {risk}" for risk in result.risks)
    return "\n".join(lines).rstrip() + "\n"


def render_insight_candidate(note: Any, result: Any, title: str, project_impacts: list[ProjectImpact]) -> str:
    text = source_text(note, 5000)
    lines = [
        *frontmatter_block("insight", note, result, title, "候选", project_impacts),
        f"# {title}",
        "",
        "## 观察到的模式",
        "",
        result.value,
        "",
        "## 证据",
        "",
        f"- 收件箱来源：`{repo_relative(note.path)}`",
        f"- 原文链接：{source_url(note) or '暂无'}",
        "",
        text or "待补充。",
        "",
        "## 为什么重要",
        "",
        "- 这是自动生成的候选洞察，需要用户确认是否真的成立。",
        "",
        "## 对项目的影响",
        "",
        *render_project_impact_lines(project_impacts),
        "",
        "## 长期知识转正",
        "",
        *render_promotion_gate_lines(),
        "",
        "## 不确定性",
        "",
    ]
    lines.extend(f"- {risk}" for risk in result.risks)
    return "\n".join(lines).rstrip() + "\n"


def render_candidate(kind: str, note: Any, result: Any, title: str, project_impacts: list[ProjectImpact]) -> str:
    if kind == "prompt":
        return render_prompt_candidate(note, result, title, project_impacts)
    if kind == "insight":
        return render_insight_candidate(note, result, title, project_impacts)
    return render_library_candidate(note, result, title, project_impacts)


def candidate_title(note: Any, kind: str) -> str:
    prefix = "" if kind == "prompt" else f"{now_date()} "
    return sanitize_filename(f"{prefix}{note.title}")


def existing_candidate_links(note: Any) -> list[str]:
    record = extract_section(note.body, "沉淀记录") + "\n" + extract_section(note.body, "入库记录")
    return [link for _, link in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", record)]


def should_create_candidate(note: Any, result: Any, force_unread: bool, force: bool) -> tuple[bool, str]:
    if existing_candidate_links(note) and not force:
        return False, "来源已经有候选回链，避免重复生成。"
    sensitive = str(note.frontmatter.get("敏感状态") or "")
    if sensitive not in {"", "未知", "无"}:
        return False, f"敏感状态为「{sensitive}」，需要人工确认后再沉淀。"
    if not has_enough_content(note):
        return False, "内容质量或解析状态不足，先进入待核验/继续解析。"
    if note.reading_status not in {"已读", "已阅读"} and not force_unread:
        return False, "阅读状态不是已读，先进入待读，不自动生成候选。"
    return True, "满足候选生成条件。"


def make_candidate(note: Any, result: Any, explicit_target: str, force_unread: bool, force: bool) -> Candidate:
    create, reason = should_create_candidate(note, result, force_unread, force)
    kind, directory, target_reason = destination_for_result(note, result, explicit_target)
    project_impacts = infer_project_impacts(note, result, kind)
    title = candidate_title(note, kind)
    target = unique_path(directory / f"{title}.md")
    questions = []
    if not create:
        questions.append(f"是否对《{note.title}》继续处理？建议：继续解析 / 补充判断 / 跳过。")
        for impact in project_impacts:
            questions.extend(impact.questions)
        return Candidate(
            source=note.path,
            target=target,
            kind=kind,
            title=title,
            status="暂缓",
            reason=reason,
            project_impacts=project_impacts,
            questions=list(dict.fromkeys(questions)),
        )

    content = render_candidate(kind, note, result, title, project_impacts)
    questions.append(f"已生成《{title}》候选。是否确认后续晋升为长期知识？回复：确认 / 调整 / 跳过。")
    if kind == "insight":
        questions.append("这条是候选洞察，请确认它是否代表你的真实判断。")
    for impact in project_impacts:
        questions.extend(impact.questions)
    return Candidate(
        source=note.path,
        target=target,
        kind=kind,
        title=title,
        status="已生成候选",
        reason=f"{reason} {target_reason}",
        content=content,
        project_impacts=project_impacts,
        questions=list(dict.fromkeys(questions)),
    )


def set_scalar_field(lines: list[str], key: str, value: str) -> list[str]:
    return triage.set_scalar_field(lines, key, value)


def append_record(text: str, heading: str, record: str) -> str:
    if record in text:
        return text
    if f"## {heading}" in text:
        return re.sub(rf"(## {re.escape(heading)}\n\n)", rf"\1{record}\n", text, count=1)
    if "## 原始链接" in text:
        return text.replace("## 原始链接", f"## {heading}\n\n{record}\n\n## 原始链接", 1)
    return text.rstrip() + f"\n\n## {heading}\n\n{record}\n"


def markdown_link_from_source(source: Path, target: Path) -> str:
    import os

    rel = Path(os.path.relpath(target.resolve(), start=source.parent.resolve())).as_posix()
    return rel.replace(" ", "%20")


def update_source_note(candidate: Candidate) -> None:
    text = read_text(candidate.source)
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"来源笔记缺少 frontmatter：{candidate.source}")
    lines = set_scalar_field(lines, "处理状态", "已分拣")
    lines = set_scalar_field(lines, "入库状态", candidate.status)
    updated = "\n".join(lines).rstrip() + "\n"
    if candidate.status == "已生成候选":
        link = markdown_link_from_source(candidate.source, candidate.target)
        record = f"- {now_date()}：`knowledge-intake` 已生成{candidate.kind}候选：[{candidate.title}]({link})。状态：候选，待确认。"
        updated = append_record(updated, "沉淀记录", record)
    else:
        record = f"- {now_date()}：`knowledge-intake` 暂未生成候选。原因：{candidate.reason}"
        updated = append_record(updated, "入库记录", record)
    candidate.source.write_text(updated.rstrip() + "\n", encoding="utf-8")


def refresh_flow_views() -> list[Path]:
    reading_notes = triage.load_notes_by_status(INBOX_DIR, {"已分拣"})
    reading_queue = [triage.triage_note(note) for note in reading_notes]
    return triage.write_flow_views(reading_queue, FLOW_DIR)


def render_report(run: IntakeRun, *, write: bool) -> str:
    generated = now_datetime()
    lines = [
        "# 入库处理报告",
        "",
        "## 总览",
        "",
        f"- 处理时间：{generated}",
        f"- 模式：{'写入' if write else 'dry-run'}",
        f"- 新保存原始材料：{len(run.captured)}",
        f"- 本次来源数量：{len(run.sources)}",
        f"- 已生成候选：{sum(1 for item in run.candidates if item.status == '已生成候选')}",
        f"- 暂缓处理：{len(run.deferred)}",
        "",
        "## 原始材料",
        "",
    ]
    if run.captured:
        lines.extend(f"- `{repo_relative(path)}`" for path in run.captured)
    else:
        lines.append("- 本次未新建原始材料，使用已有来源。")
    lines.extend(["", "## 候选知识", ""])
    created = [item for item in run.candidates if item.status == "已生成候选"]
    if created:
        for item in created:
            lines.extend(
                [
                    f"### {item.title}",
                    "",
                    f"- 类型：{item.kind}",
                    f"- 来源：`{repo_relative(item.source)}`",
                    f"- 候选：`{repo_relative(item.target)}`",
                    f"- 原因：{item.reason}",
                    "",
                ]
            )
    else:
        lines.append("暂无自动生成候选。")
    lines.extend(["", "## 项目影响建议", ""])
    impact_items = [item for item in [*run.candidates, *run.deferred] if item.project_impacts]
    if impact_items:
        for item in impact_items:
            lines.extend(
                [
                    f"### {item.title}",
                    "",
                    f"- 来源：`{repo_relative(item.source)}`",
                    f"- 状态：{item.status}",
                    "",
                ]
            )
            lines.extend(render_project_impact_lines(item.project_impacts))
            lines.append("")
    else:
        lines.append("暂无明确项目影响建议。")
    lines.extend(["", "## 暂缓和待确认", ""])
    pending = [*run.deferred, *[item for item in run.candidates if item.status != "已生成候选"]]
    if pending:
        for item in pending:
            lines.extend(
                [
                    f"- 《{item.title}》：{item.reason}",
                    *[f"  - {question}" for question in item.questions],
                ]
            )
    else:
        lines.append("- 暂无阻塞项。")
    all_questions = [question for item in [*run.candidates, *run.deferred] for question in item.questions]
    lines.extend(["", "## 给用户的最小问题清单", ""])
    if all_questions:
        for index, question in enumerate(all_questions, start=1):
            lines.append(f"{index}. {question}")
    else:
        lines.append("当前无需用户额外判断。")
    if run.flow_paths:
        lines.extend(["", "## 已刷新流转区", ""])
        lines.extend(f"- `{repo_relative(path)}`" for path in run.flow_paths)
    if run.capture_output:
        lines.extend(["", "## 采集输出", "", "```text", run.capture_output.strip(), "```"])
    return "\n".join(lines).rstrip() + "\n"


def write_report(report: str) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = unique_path(RUN_DIR / f"入库处理-{now_filename()}.md")
    path.write_text(report, encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process my-mind 入库 requests from raw material to candidate knowledge.")
    parser.add_argument("materials", nargs="*", help="URLs or plain text snippets to intake.")
    parser.add_argument("--raw", action="append", default=[], help="Raw user message to intake. Repeatable.")
    parser.add_argument("--source", action="append", default=[], help="Existing 00_收件箱 source note to process. Repeatable.")
    parser.add_argument("--title", default="", help="Title for plain text intake.")
    parser.add_argument("--target", choices=["auto", "library", "prompt", "insight"], default="auto", help="Candidate target type.")
    parser.add_argument("--reading-status", choices=["已读", "未读"], default="已读", help="Reading status for newly captured material.")
    parser.add_argument(
        "--max-transcribe-seconds",
        type=int,
        default=DEFAULT_INTAKE_MAX_TRANSCRIBE_SECONDS,
        help="Video transcription duration limit passed to inbox-capture for explicit 入库 requests.",
    )
    parser.add_argument("--force-unread", action="store_true", help="Generate candidates even when source reading status is not 已读.")
    parser.add_argument("--force", action="store_true", help="Allow generating a new candidate even when source already has candidate links.")
    parser.add_argument("--no-capture", action="store_true", help="Do not call inbox-capture for URL materials.")
    parser.add_argument("--no-flow", action="store_true", help="Do not refresh 05_流转区.")
    parser.add_argument("--capture-arg", action="append", default=[], help="Extra argument passed to inbox-capture. Repeat for each token.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write inbox notes, candidates, source records, flow views, and run report.")
    mode.add_argument("--dry-run", action="store_true", help="Preview only. This is the default.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    write = bool(args.write)
    run = IntakeRun()

    raw_materials = [*args.raw, *args.materials]
    urls, snippets = split_materials(raw_materials)
    if urls and args.no_capture:
        raise RuntimeError("收到 URL 但启用了 --no-capture；请改用 --source 指向已有收件箱笔记。")
    capture_args = list(args.capture_arg)
    has_transcribe_limit = any(
        item == "--max-transcribe-seconds" or item.startswith("--max-transcribe-seconds=") for item in capture_args
    )
    if urls and not has_transcribe_limit:
        capture_args = ["--max-transcribe-seconds", str(args.max_transcribe_seconds), *capture_args]
    captured_urls, capture_output = capture_urls(urls, write=write, reading_status=args.reading_status, extra_args=capture_args)
    captured_text = capture_snippets(snippets, write=write, title=args.title, reading_status=args.reading_status)
    run.captured.extend(captured_urls)
    run.captured.extend(captured_text)
    run.capture_output = capture_output

    source_paths = [resolve_repo_path(path) for path in args.source]
    if write:
        source_paths.extend(run.captured)
    else:
        source_paths.extend([path for path in run.captured if path.exists()])
    seen: set[Path] = set()
    for path in source_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not path.exists():
            if write:
                raise FileNotFoundError(f"来源不存在：{path}")
            continue
        run.sources.append(path)

    if not run.sources and not write and (urls or snippets):
        print("dry-run：原始材料尚未写入，无法继续生成候选。加 --write 后会先入箱再处理。")
        return 0

    for source in run.sources:
        note = load_note(source)
        result = triage.triage_note(note)
        candidate = make_candidate(note, result, args.target, args.force_unread, args.force)
        if candidate.status == "已生成候选":
            run.candidates.append(candidate)
            if write:
                candidate.target.parent.mkdir(parents=True, exist_ok=True)
                candidate.target.write_text(candidate.content, encoding="utf-8")
                update_source_note(candidate)
        else:
            run.deferred.append(candidate)
            if write:
                update_source_note(candidate)

    if write and not args.no_flow:
        run.flow_paths = refresh_flow_views()

    report = render_report(run, write=write)
    print(report, end="")
    if write:
        report_path = write_report(report)
        print(f"\n已写入入库报告：{repo_relative(report_path)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
