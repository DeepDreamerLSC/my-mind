#!/usr/bin/env python3
"""Generate a concise frontdesk push note for OpenClaw."""

from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_INBOX = ROOT / "00_收件箱"
DEFAULT_FLOW_DIR = ROOT / "05_流转区"
DEFAULT_RUN_DIR = ROOT / "85_运行记录"
DEFAULT_PROJECT_DIR = ROOT / "10_项目" / "个人数据资产系统"
FLOW_PRIORITY_FALLBACK = 999_999


HIGH_KEYWORDS = [
    "codex",
    "openai",
    "openclaw",
    "skill",
    "prompt",
    "提示词",
    "自动化",
    "agent",
    "工作流",
    "收件箱",
    "沉淀",
]
MEDIUM_KEYWORDS = ["人工智能", "ai", "管理", "项目", "知识", "复盘", "资料库", "原子笔记"]
TERMINAL_STATUSES = {"已处理", "已晋升", "可丢弃", "已归档"}


@dataclass
class InboxNote:
    path: Path
    title: str
    platform: str
    author: str
    source_url: str
    original_url: str
    transcript_url: str
    process_status: str
    parse_status: str
    summary: str
    reading_excerpt: str
    quality_note: str
    reading_questions: list[str]
    distill_direction: str
    score: int
    flow_rank: int
    value: str
    action: str


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end].strip()
    body = text[end + 4 :].lstrip()
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.startswith(" "):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta, body


def first_section(body: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, body, flags=re.M | re.S)
    return match.group(1).strip() if match else ""


def first_subsection(section: str, heading: str) -> str:
    pattern = rf"^### {re.escape(heading)}\n(.*?)(?=^### |\Z)"
    match = re.search(pattern, section, flags=re.M | re.S)
    return match.group(1).strip() if match else ""


def clean_line(text: str, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -\n\t")
    if len(text) <= limit:
        return text
    clipped = text[:limit].rstrip()
    sentence_end = max(clipped.rfind("。"), clipped.rfind("！"), clipped.rfind("？"))
    if sentence_end >= int(limit * 0.55):
        return clipped[: sentence_end + 1].rstrip()
    return clipped + "..."


def first_url(text: str) -> str:
    match = re.search(r"https?://[^\s)>\"']+", text)
    return match.group(0).rstrip("。，,；;") if match else ""


def note_url(meta: dict[str, str], body: str, field: str, fallback_section: str = "") -> str:
    url = meta.get(field, "").strip().strip('"')
    if url:
        return url
    if fallback_section:
        return first_url(first_section(body, fallback_section))
    return ""


def parse_flow_priority(flow_dir: Path) -> dict[str, int]:
    queue_path = flow_dir / "10_待读" / "收件箱待读队列.md"
    if not queue_path.exists():
        return {}
    priority: dict[str, int] = {}
    index = 0
    for line in read_text(queue_path).splitlines():
        match = re.search(r"^- 来源文件：\[([^\]]+)\]\(", line)
        if not match:
            continue
        source_path = match.group(1).strip()
        if not source_path:
            continue
        index += 1
        priority.setdefault(source_path, index)
    return priority


def clip_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit].rstrip()
    sentence_end = max(clipped.rfind("。"), clipped.rfind("！"), clipped.rfind("？"), clipped.rfind("\n"))
    if sentence_end >= int(limit * 0.65):
        clipped = clipped[: sentence_end + 1].rstrip()
    return clipped + "\n\n（摘录已截断，完整内容见来源文件。）"


def clean_reading_text(text: str) -> str:
    skip_prefixes = (
        "- 来源：",
        "- 后端：",
        "- 模型：",
        "- 兼容模式：",
        "- 语言：",
        "- 转写片段数：",
        "- 估算字数：",
        "- 估算词数：",
        "- 图片总数：",
        "- 已处理图片数：",
        "- 识别到文字图片数：",
        "- 章节数：",
        "- 转录片段数：",
        "- 发布时间：",
        "- 来源图片：",
        "- 封面图：",
        "说明：",
        "tiny 模型适合",
    )
    cleaned: list[str] = []
    blank_pending = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            blank_pending = bool(cleaned)
            continue
        if line.startswith(skip_prefixes):
            continue
        if re.match(r"^https?://", line):
            continue
        if re.match(r"^- .*https?://", line):
            continue
        if re.match(r"^### 图片 \d+", line):
            continue
        if "这里保存的是" in line and ("OCR" in line or "转写" in line):
            continue
        if blank_pending and cleaned and cleaned[-1] != "":
            cleaned.append("")
        cleaned.append(line)
        blank_pending = False
    result = "\n".join(cleaned)
    result = re.sub(r"#([^#\n]*?\[话题\])#", "", result)
    result = re.sub(r"\s+#\S+", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"[ \t]+", " ", result)
    return result.strip()


def add_excerpt_block(blocks: list[tuple[str, str]], title: str, text: str) -> None:
    cleaned = clean_reading_text(text)
    if cleaned and cleaned not in {"暂无。", "暂无", "无"}:
        blocks.append((title, cleaned))


def reading_excerpt(body: str, limit: int) -> str:
    blocks: list[tuple[str, str]] = []

    video = first_section(body, "视频内容摘录")
    if video:
        add_excerpt_block(blocks, "视频摘要", first_subsection(video, "摘要"))
        add_excerpt_block(blocks, "关键点", first_subsection(video, "关键点"))
        if not blocks:
            add_excerpt_block(blocks, "视频内容摘录", video)

    add_excerpt_block(blocks, "初步内容解析", first_section(body, "初步内容解析"))
    add_excerpt_block(blocks, "中文摘要", first_section(body, "中文摘要"))
    add_excerpt_block(blocks, "文案摘录", first_section(body, "文案摘录"))
    add_excerpt_block(blocks, "原文摘录", first_section(body, "原文摘录"))
    add_excerpt_block(blocks, "图片文字 OCR", first_section(body, "图片文字 OCR"))
    add_excerpt_block(blocks, "可提炼主题", first_section(body, "可提炼主题"))
    add_excerpt_block(blocks, "简介摘录", first_section(body, "简介摘录"))

    if not blocks:
        return ""

    parts: list[str] = []
    remaining = limit
    seen: set[str] = set()
    for title, text in blocks:
        normalized = re.sub(r"\s+", "", text)
        if normalized in seen:
            continue
        seen.add(normalized)
        header = f"**{title}**"
        allowance = max(remaining - len(header) - 2, 0)
        if allowance < 120:
            break
        clipped = clip_text(text, allowance)
        parts.append(f"{header}\n{clipped}")
        remaining = limit - len("\n\n".join(parts))
        if remaining <= 160:
            break
    return "\n\n".join(parts).strip()


def short_summary(body: str, fallback_excerpt: str, limit: int = 180) -> str:
    video = first_section(body, "视频内容摘录")
    candidates = [
        first_subsection(video, "摘要") if video else "",
        first_section(body, "中文摘要"),
        first_section(body, "初步内容解析"),
        first_section(body, "文案摘录"),
        first_section(body, "简介摘录"),
        fallback_excerpt,
    ]
    for candidate in candidates:
        cleaned = clean_reading_text(re.sub(r"\*\*[^*]+\*\*", "", candidate))
        if cleaned:
            return clean_line(cleaned, limit)
    return ""


def note_score(meta: dict[str, str], body: str) -> int:
    text = f"{meta.get('标题', '')}\n{body[:3000]}".lower()
    score = 0
    status = meta.get("处理状态", "")
    if status == "待分拣":
        score += 30
    elif status == "已分拣":
        score += 22
    elif status:
        score += 8
    if meta.get("解析状态") == "已解析":
        score += 10
    if meta.get("图片OCR字数"):
        score += 10
    if meta.get("内容摘录字数"):
        score += 10
    score += sum(12 for keyword in HIGH_KEYWORDS if keyword in text)
    score += sum(5 for keyword in MEDIUM_KEYWORDS if keyword in text)
    return score


def note_value(meta: dict[str, str], body: str) -> str:
    text = f"{meta.get('标题', '')}\n{body[:3000]}".lower()
    if any(keyword in text for keyword in ["codex", "openai", "skill", "prompt", "提示词", "agent", "工作流"]):
        return "和 Codex、提示词、skill 或自动化直接相关，适合优先阅读并判断是否沉淀。"
    if meta.get("图片OCR字数"):
        return "已经完成图片 OCR，具备进一步阅读和沉淀的基础。"
    if any(keyword in text for keyword in ["管理", "团队", "组织"]):
        return "属于管理和组织方法素材，可判断是否进入资料库或项目实践。"
    if any(keyword in text for keyword in ["ai", "人工智能", "nvidia", "模型"]):
        return "属于人工智能资料或行业信号，适合作为资料库候选。"
    return "已入箱但还没有形成明确判断，适合先快速阅读后决定去向。"


def note_action(meta: dict[str, str], body: str) -> str:
    text = f"{meta.get('标题', '')}\n{body[:3000]}".lower()
    if any(keyword in text for keyword in ["codex", "openai", "skill", "prompt", "提示词", "agent", "工作流"]):
        return "已读后回复“沉淀成提示词”或补充你的判断。"
    if meta.get("来源平台") == "小红书" and not meta.get("图片OCR字数"):
        return "回复“继续解析”，先补 OCR 后再决定是否沉淀。"
    if meta.get("来源平台") in {"抖音", "YouTube"} and not meta.get("内容摘录字数"):
        return "回复“继续解析”，先补转写或字幕后再整理。"
    return "回复“已读：你的想法”或“跳过”。"


def distill_direction(meta: dict[str, str], body: str) -> str:
    text = f"{meta.get('标题', '')}\n{body[:3000]}".lower()
    if any(keyword in text for keyword in ["codex", "openai", "skill", "prompt", "提示词", "agent", "工作流"]):
        return "`75_提示词库/Codex工作流/` 或 `10_项目/个人数据资产系统/`，优先判断是否能沉淀成可复用工作流。"
    if any(keyword in text for keyword in ["视觉", "高级感", "排版", "配色"]) or re.search(r"\b(html|ppt)\b", text):
        return "`75_提示词库/前端与视觉/` 或 `20_资料库/设计与视觉/`，优先判断哪些约束能稳定复用。"
    if any(keyword in text for keyword in ["管理", "团队", "组织"]):
        return "`20_资料库/管理与组织/` 或 `30_原子笔记/`，优先提炼管理原则和可执行做法。"
    if any(keyword in text for keyword in ["ai", "人工智能", "nvidia", "模型", "算力"]):
        return "`20_资料库/人工智能产业/` 或 `60_行业情报/模型与公司/`，优先保留趋势判断和事实线索。"
    return "`20_资料库/AI产品与工具/` 或 `20_资料库/工作流与自动化/`，先作为资料候选保留，读后再决定是否拆成原子笔记。"


def quality_note(meta: dict[str, str], body: str) -> str:
    notes: list[str] = []
    if meta.get("解析状态") != "已解析":
        notes.append("解析状态尚未完全确认，阅读前先关注内容是否完整。")
    if "模型：tiny" in body or "- 模型：tiny" in body:
        notes.append("本条包含 tiny 模型转写，中文错词概率较高，沉淀前需要校对关键术语。")
    if meta.get("图片OCR字数"):
        notes.append("本条包含图片 OCR，适合先读大意，沉淀前需要校对图片文字。")
    if "官方 transcript" in body or "官方转录来源" in body:
        notes.append("本条已有可追溯转录来源，适合后续做更完整资料沉淀。")
    has_readable_section = any(
        first_section(body, heading)
        for heading in [
            "中文摘要",
            "文案摘录",
            "原文摘录",
            "视频内容摘录",
            "图片文字 OCR",
            "初步内容解析",
            "可提炼主题",
            "简介摘录",
        ]
    )
    if not meta.get("内容摘录字数") and not meta.get("图片OCR字数") and not has_readable_section:
        notes.append("当前缺少正文级摘录，若要沉淀应先继续解析。")
    return "；".join(notes) if notes else "暂无明显质量风险。"


def reading_questions(meta: dict[str, str], body: str) -> list[str]:
    text = f"{meta.get('标题', '')}\n{body[:3000]}".lower()
    if any(keyword in text for keyword in ["codex", "openai", "skill", "prompt", "提示词", "agent", "工作流"]):
        return [
            "这条能否沉淀成一个可复用提示词、skill 或自动化流程？",
            "它对当前 `my-mind` 的入箱、分拣、反馈或飞书阅读闭环有什么直接启发？",
            "是否有一个可以马上加入任务清单的最小动作？",
        ]
    if any(keyword in text for keyword in ["管理", "团队", "组织"]):
        return [
            "它提出的管理原则是否能解释你当前遇到的团队或项目问题？",
            "有没有一句话值得转成原子笔记？",
            "是否值得进入资料库，还是只保留为一次性参考？",
        ]
    if any(keyword in text for keyword in ["ai", "人工智能", "nvidia", "模型", "算力"]):
        return [
            "这里的判断是事实、趋势，还是作者观点？",
            "有没有需要后续核验的公司、产品、日期或技术判断？",
            "它适合进入资料库，还是更像行业情报信号？",
        ]
    return [
        "这条资料读完后是否还有保留价值？",
        "它更适合做资料库、原子笔记、项目任务，还是直接跳过？",
        "你读完后最想保留的一句话是什么？",
    ]


def load_inbox_notes(inbox: Path, excerpt_chars: int, flow_priority: dict[str, int] | None = None) -> list[InboxNote]:
    flow_priority = flow_priority or {}
    notes: list[InboxNote] = []
    for path in sorted(inbox.glob("*.md")):
        if path.name == "目录说明.md":
            continue
        text = read_text(path)
        meta, body = split_frontmatter(text)
        status = meta.get("处理状态", "")
        if status in TERMINAL_STATUSES:
            continue
        rel_path = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
        title = meta.get("标题") or path.stem
        platform = meta.get("来源平台") or "未知"
        author = meta.get("作者或频道") or "未知"
        source_url = note_url(meta, body, "来源链接", "原始链接")
        original_url = note_url(meta, body, "原始链接")
        transcript_url = note_url(meta, body, "外部转录链接", "官方转录来源")
        excerpt = reading_excerpt(body, excerpt_chars)
        summary = short_summary(body, excerpt)
        score = note_score(meta, body)
        notes.append(
            InboxNote(
                path=path,
                title=title,
                platform=platform,
                author=author,
                source_url=source_url,
                original_url=original_url,
                transcript_url=transcript_url,
                process_status=status or "未知",
                parse_status=meta.get("解析状态") or "未知",
                summary=summary,
                reading_excerpt=excerpt,
                quality_note=quality_note(meta, body),
                reading_questions=reading_questions(meta, body),
                distill_direction=distill_direction(meta, body),
                score=score,
                flow_rank=flow_priority.get(rel_path, FLOW_PRIORITY_FALLBACK),
                value=note_value(meta, body),
                action=note_action(meta, body),
            )
        )
    notes.sort(key=lambda note: (note.flow_rank, -note.score, -note.path.stat().st_mtime))
    return notes


def task_summary(project_dir: Path) -> tuple[list[str], list[str]]:
    task_file = project_dir / "任务清单.md"
    if not task_file.exists():
        return [], []
    completed: list[str] = []
    pending: list[str] = []
    for line in read_text(task_file).splitlines():
        match = re.match(r"- \[(x| )\] (.+)", line)
        if not match:
            continue
        item = match.group(2).strip()
        if match.group(1) == "x":
            completed.append(item)
        else:
            pending.append(item)
    return completed[-3:], pending[:3]


def build_push(notes: list[InboxNote], project_dir: Path, limit: int) -> str:
    completed, pending = task_summary(project_dir)
    lines = [
        "# my-mind 前台推送",
        "",
        "## 总览",
        "",
        f"- 生成时间：{now_datetime()}",
        "- 生成来源：Codex / frontdesk-push",
        f"- 候选条目：{len(notes)}",
        "- 用途：供飞书手机阅读页或 OpenClaw 前台摘要使用；OpenClaw 直接发消息时建议只转发标题、链接和回复指令。",
        "",
        "## 今天最值得读",
        "",
    ]
    selected = notes[:limit]
    if not selected:
        lines.append("暂无需要推送的待读或待沉淀条目。")
    for index, note in enumerate(selected, start=1):
        rel_path = note.path.relative_to(ROOT) if note.path.is_relative_to(ROOT) else note.path
        lines.extend(
            [
                f"### {index}. {note.title}",
                "",
                f"- 来源：{note.platform} / {note.author}",
                f"- 状态：{note.process_status} / {note.parse_status}",
                f"- 为什么值得读：{note.value}",
                f"- 建议动作：{note.action}",
                f"- 建议沉淀方向：{note.distill_direction}",
                f"- 来源文件：`{rel_path}`",
            ]
        )
        if note.source_url:
            lines.append(f"- 原文链接：[{note.platform} 原文]({note.source_url})")
        if note.original_url and note.original_url != note.source_url:
            lines.append(f"- 分享链接：[原始分享入口]({note.original_url})")
        if note.transcript_url and note.transcript_url not in {note.source_url, note.original_url}:
            lines.append(f"- 转录链接：[外部转录]({note.transcript_url})")
        if note.summary:
            lines.append(f"- 一句话摘要：{note.summary}")
        lines.append("")
        lines.append("#### 内容摘录")
        lines.append("")
        if note.reading_excerpt:
            lines.append(note.reading_excerpt)
        else:
            lines.append("暂无可直接阅读的正文摘录，建议先回复“继续解析”。")
        lines.append("")
        lines.append("#### 阅读时重点")
        lines.append("")
        for question in note.reading_questions:
            lines.append(f"- {question}")
        lines.append("")
        lines.append("#### 质量提醒")
        lines.append("")
        lines.append(f"- {note.quality_note}")
        lines.append("")
        lines.append("#### 可回复")
        lines.append("")
        lines.append(f"- `{index} 已读：你的想法`")
        lines.append(f"- `{index} 沉淀成提示词`")
        lines.append(f"- `{index} 跳过`")
        lines.append(f"- `{index} 继续解析`")
        lines.append("")

    lines.extend(["## 项目进度", ""])
    if completed:
        lines.append("- 近期已完成：" + "；".join(completed))
    else:
        lines.append("- 近期已完成：暂无可读取记录")
    if pending:
        lines.append("- 当前卡点：" + "；".join(pending[:2]))
        lines.append(f"- 下一步建议：优先推进“{pending[0]}”。")
    else:
        lines.append("- 当前卡点：暂无未完成任务")
        lines.append("- 下一步建议：继续按当前节奏巡检和沉淀。")

    lines.extend(
        [
            "",
            "## 可回复指令",
            "",
            "- `序号 已读：你的想法`",
            "- `序号 沉淀成提示词`",
            "- `序号 跳过`",
            "- `序号 继续解析`",
            "",
            "## 处理边界",
            "",
            "- OpenClaw 可推送飞书链接或精简摘要，不直接改写长期知识。",
            "- 用户回复后写入 `85_运行记录/前台反馈队列.jsonl`。",
            "- Codex 后台消费反馈，再决定是否回写阅读思考或生成候选沉淀物。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def unique_path(directory: Path, filename: str) -> Path:
    path = directory / filename
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a my-mind frontdesk push note for OpenClaw.")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Inbox directory.")
    parser.add_argument("--flow-dir", default=str(DEFAULT_FLOW_DIR), help="Flow-zone directory used to prioritize pending reading items.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Directory for frontdesk push notes.")
    parser.add_argument("--project-dir", default=str(DEFAULT_PROJECT_DIR), help="Project directory for progress summary.")
    parser.add_argument("--limit", type=int, default=3, help="Maximum pushed inbox items.")
    parser.add_argument("--excerpt-chars", type=int, default=1200, help="Maximum reading excerpt characters per item.")
    parser.add_argument("--ignore-flow", action="store_true", help="Ignore 05_流转区 priority and rank directly from inbox notes.")
    parser.add_argument("--dry-run", action="store_true", help="Print push note instead of writing it.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inbox = Path(args.inbox)
    flow_dir = Path(args.flow_dir)
    run_dir = Path(args.run_dir)
    project_dir = Path(args.project_dir)
    if not inbox.is_absolute():
        inbox = ROOT / inbox
    if not flow_dir.is_absolute():
        flow_dir = ROOT / flow_dir
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if not project_dir.is_absolute():
        project_dir = ROOT / project_dir

    flow_priority = {} if args.ignore_flow else parse_flow_priority(flow_dir)
    note = build_push(load_inbox_notes(inbox, max(args.excerpt_chars, 200), flow_priority), project_dir, max(args.limit, 1))
    if args.dry_run:
        print(note, end="")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(run_dir, f"前台推送-{now_filename()}.md")
    output_path.write_text(note, encoding="utf-8")
    print(output_path.relative_to(ROOT) if output_path.is_relative_to(ROOT) else output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
