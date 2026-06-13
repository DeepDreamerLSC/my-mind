#!/usr/bin/env python3
"""Watch curated feeds and prepare frontier intelligence for my-mind."""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_SOURCES = Path(__file__).resolve().parents[1] / "references" / "sources.json"
DEFAULT_INBOX = ROOT / "00_收件箱"
DEFAULT_RUN_DIR = ROOT / "85_运行记录"

CATEGORIES = ["AI与Agent工具", "工作流与知识系统", "商业", "管理"]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "AI与Agent工具": [
        "agent",
        "agents",
        "agentic",
        "ai agent",
        "openai",
        "codex",
        "anthropic",
        "claude",
        "cursor",
        "mcp",
        "model context protocol",
        "llm",
        "foundation model",
        "open weights",
        "fine-tuning",
        "finetuning",
        "inference",
        "multimodal",
        "reasoning",
        "tool use",
        "function calling",
        "coding assistant",
        "developer tools",
        "eval",
        "workflow automation",
    ],
    "工作流与知识系统": [
        "workflow",
        "automation",
        "knowledge",
        "knowledge management",
        "pkm",
        "personal knowledge",
        "second brain",
        "notes",
        "obsidian",
        "notion",
        "memory",
        "rag",
        "retrieval",
        "inbox",
        "productivity",
        "readwise",
        "logseq",
        "pkm",
        "zettelkasten",
        "newsletter",
        "rss",
        "bookmark",
        "reading workflow",
        "documentation",
        "search",
    ],
    "商业": [
        "business",
        "business model",
        "strategy",
        "market",
        "commercial",
        "monetization",
        "pricing",
        "growth",
        "distribution",
        "platform",
        "ecosystem",
        "startup",
        "founder",
        "venture",
        "fundraising",
        "go-to-market",
        "gtm",
        "product",
        "pm",
        "enterprise",
        "revenue",
        "competition",
        "product strategy",
    ],
    "管理": [
        "management",
        "leadership",
        "manager",
        "team",
        "organization",
        "org",
        "engineering management",
        "product management",
        "cto",
        "cio",
        "vp engineering",
        "operating",
        "execution",
        "decision",
        "hiring",
        "talent",
        "culture",
        "communication",
        "incentive",
        "performance",
        "process",
    ],
}

CHINESE_HINTS: dict[str, list[str]] = {
    "AI与Agent工具": ["智能体", "大模型", "模型", "推理", "多模态", "开源模型", "工具调用", "代码助手", "编程助手", "自动化", "插件"],
    "工作流与知识系统": ["工作流", "知识管理", "个人知识库", "笔记", "记忆", "检索", "收件箱", "生产力", "效率", "阅读", "书签"],
    "商业": ["商业模式", "战略", "增长", "定价", "市场", "竞争", "平台", "生态", "商业化", "产品策略", "创业", "融资", "产品", "公司"],
    "管理": ["管理", "领导力", "组织", "团队", "人才", "招聘", "决策", "执行", "文化", "流程", "工程管理", "技术管理", "组织建设"],
}

REJECT_KEYWORDS = [
    "giveaway",
    "coupon",
    "sale",
    "sponsored",
    "webinar registration",
    "event registration",
]


@dataclass
class FeedSource:
    name: str
    url: str
    category_hints: list[str] = field(default_factory=list)
    quality: int = 3


@dataclass
class FeedItem:
    source: FeedSource
    title: str
    url: str
    summary: str
    published: dt.datetime | None
    author: str = ""
    summary_zh: str = ""
    categories: list[str] = field(default_factory=list)
    score: int = 0
    score_reasons: list[str] = field(default_factory=list)
    duplicate: bool = False


def now_datetime() -> dt.datetime:
    return dt.datetime.now(TZ)


def now_text() -> str:
    return now_datetime().strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return now_datetime().strftime("%Y-%m-%d-%H%M")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def clean_text(value: str, limit: int = 1200) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</(p|div|li|h[1-6])>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    if len(value) > limit:
        return value[:limit].rstrip() + "..."
    return value


def needs_chinese_translation(value: str) -> bool:
    if not value.strip():
        return False
    latin_count = len(re.findall(r"[A-Za-z]", value))
    han_count = len(re.findall(r"[\u4e00-\u9fff]", value))
    has_japanese_or_korean = bool(re.search(r"[\u3040-\u30ff\uac00-\ud7af]", value))
    if has_japanese_or_korean:
        return True
    return latin_count >= 40 and latin_count > han_count * 2


TRANSLATION_CACHE: dict[str, str] = {}


def polish_chinese_translation(value: str, original: str = "") -> str:
    original_lower = original.lower()
    replacements = {
        "科德克斯": "Codex",
        "扣袋子": "Codex",
        "代码克斯": "Codex",
        "开放人工智能": "OpenAI",
        "开放式人工智能": "OpenAI",
        "聊天GPT": "ChatGPT",
        "克劳德": "Claude",
        "人类学": "Anthropic",
        "模型上下文协议": "MCP",
        "功能调用": "Function Calling",
        "工具使用": "Tool Use",
        "光标": "Cursor",
        "副驾驶": "Copilot",
        "黑曜石": "Obsidian",
        "普通合伙人": "合伙人",
        "首席信息官": "CIO",
        "首席执行官": "CEO",
        "编码代理": "代码智能体",
        "编码智能体": "代码智能体",
        "人工智能代理": "AI 智能体",
        "AI 代理": "AI 智能体",
        "代理时代": "智能体时代",
        "执行报告": "高管报告",
        "供应商评论": "供应商复盘",
        "时事通讯": "通讯",
        "超大规模企业": "超大规模云厂商",
        "你会建造什么": "你会构建什么",
        "季终集": "收官集",
        "世界其他地方": "外部世界",
        "捕捉，组织和提炼": "捕获、组织和提炼",
        "捕捉、组织和提炼": "捕获、组织和提炼",
        "捕捉，组织，提炼和快速": "捕获、组织、提炼和表达",
        "捕捉、组织、提炼和快速": "捕获、组织、提炼和表达",
        "快速是该方法必须得到回报的步骤": "表达是这套方法真正产生回报的阶段",
        "表达是该方法必须得到回报的步骤": "表达是这套方法真正产生回报的阶段",
        "前 1% 的人在 24 个月内退出了 10 倍": "顶尖 1% 的退出案例在 24 个月内放大了 10 倍",
        "前1%的人在24个月内退出了10倍": "顶尖 1% 的退出案例在 24 个月内放大了 10 倍",
        "YouTube": "YouTube",
        "RSS": "RSS",
        "Atom": "Atom",
        "API": "API",
        "CLI": "CLI",
        "SaaS": "SaaS",
        "ARR": "ARR",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    if "notion" in original_lower:
        value = re.sub(r"概念\s*[·•]\s*", "Notion · ", value)
        value = re.sub(r"(?<=[（(、，,])概念(?=[、，,)）\s])", "Notion", value)
    if "obsidian" in original_lower:
        value = value.replace("黑曜石", "Obsidian")
    if "token" in original_lower:
        value = value.replace("代币", "Token")
    if "paper" in original_lower and ("notion" in original_lower or "obsidian" in original_lower):
        value = value.replace("或论文", "或纸质笔记")
    if "express" in original_lower and "code" in original_lower:
        value = value.replace("快速", "表达")
    for term in ["OpenAI", "ChatGPT", "Claude", "Anthropic", "Codex", "Notion", "Obsidian"]:
        value = re.sub(term, term, value, flags=re.IGNORECASE)
    value = re.sub(r"\s+([，。；：！？、）】》])", r"\1", value)
    value = re.sub(r"([（【《])\s+", r"\1", value)
    value = re.sub(r"([\u4e00-\u9fff])([A-Za-z][A-Za-z0-9.+#/-]*)", r"\1 \2", value)
    value = re.sub(r"([A-Za-z][A-Za-z0-9.+#/-]*)([\u4e00-\u9fff])", r"\1 \2", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def translate_text_to_chinese(value: str, timeout: int = 8) -> tuple[str, str | None]:
    value = clean_text(value)
    if not value:
        return "", None
    if not needs_chinese_translation(value):
        return value, None
    cached = TRANSLATION_CACHE.get(value)
    if cached is not None:
        return cached, None
    query = urllib.parse.urlencode(
        {
            "client": "gtx",
            "sl": "auto",
            "tl": "zh-CN",
            "dt": "t",
            "q": value,
        }
    )
    request = urllib.request.Request(
        f"https://translate.googleapis.com/translate_a/single?{query}",
        headers={"User-Agent": "my-mind-frontier-watch/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        translated = "".join(part[0] for part in payload[0] if part and part[0])
    except Exception as exc:
        return "", f"中文摘要翻译失败：{exc!r}"
    translated = polish_chinese_translation(clean_text(translated), value)
    TRANSLATION_CACHE[value] = translated
    return translated, None


def ensure_chinese_summaries(items: list[FeedItem], timeout: int) -> list[str]:
    errors: list[str] = []
    for item in items:
        if item.summary_zh:
            continue
        translated, error = translate_text_to_chinese(item.summary, timeout=timeout)
        if translated:
            item.summary_zh = translated
        elif item.summary:
            item.summary_zh = ""
            errors.append(f"{item.title}：{error or '中文摘要翻译失败'}")
    return errors


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    kept = [(key, value) for key, value in query if not key.lower().startswith("utm_")]
    kept = [(key, value) for key, value in kept if key.lower() not in {"ref", "ref_src", "fbclid", "gclid"}]
    normalized_query = urllib.parse.urlencode(kept, doseq=True)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/") or "/", normalized_query, ""))


def normalize_http_url(url: str, base_url: str = "") -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if base_url:
        value = urllib.parse.urljoin(base_url, value)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return canonical_url(value)


def parse_datetime(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(TZ)
    except Exception:
        pass
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(TZ)
    except Exception:
        return None


def fetch_url(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "my-mind-frontier-watch/0.1 (+https://github.com/DeepDreamerLSC/my-mind)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def text_of(element: ET.Element | None, names: list[str]) -> str:
    if element is None:
        return ""
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def attr_link(element: ET.Element) -> str:
    for link in element.findall("{http://www.w3.org/2005/Atom}link") + element.findall("link"):
        href = link.attrib.get("href", "")
        rel = link.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            return href.strip()
    return text_of(element, ["link"])


def rss_link(element: ET.Element, source_url: str) -> str:
    link = normalize_http_url(text_of(element, ["link"]), source_url)
    if link:
        return link
    guid_node = element.find("guid")
    if guid_node is not None and guid_node.text and guid_node.attrib.get("isPermaLink", "").lower() == "true":
        guid = normalize_http_url(guid_node.text, source_url)
        if guid:
            return guid
    return ""


def parse_feed(source: FeedSource, payload: bytes) -> list[FeedItem]:
    root = ET.fromstring(payload)
    items: list[FeedItem] = []
    atom = "{http://www.w3.org/2005/Atom}"
    content = "{http://purl.org/rss/1.0/modules/content/}"
    dc = "{http://purl.org/dc/elements/1.1/}"
    media = "{http://search.yahoo.com/mrss/}"

    rss_items = root.findall(".//item")
    atom_entries = root.findall(f".//{atom}entry")

    for item in rss_items:
        title = text_of(item, ["title"])
        link = rss_link(item, source.url)
        description = text_of(item, ["description", f"{content}encoded"])
        published = parse_datetime(text_of(item, ["pubDate", "published", "updated", f"{dc}date"]))
        author = clean_text(text_of(item, ["author", f"{dc}creator"]), 200)
        if title and link:
            items.append(FeedItem(source, clean_text(title, 240), link, clean_text(description), published, author))

    for entry in atom_entries:
        title = text_of(entry, [f"{atom}title", "title"])
        link = normalize_http_url(attr_link(entry), source.url)
        summary = text_of(entry, [f"{atom}summary", f"{atom}content", "summary", "content"])
        media_group = entry.find(f"{media}group")
        if not summary and media_group is not None:
            summary = text_of(media_group, [f"{media}description", f"{media}title"])
        published = parse_datetime(text_of(entry, [f"{atom}published", f"{atom}updated", "published", "updated"]))
        author_node = entry.find(f"{atom}author")
        author = clean_text(text_of(author_node, [f"{atom}name", "name"]), 200) if author_node is not None else ""
        if title and link:
            items.append(FeedItem(source, clean_text(title, 240), link, clean_text(summary), published, author))
    return items


def load_sources(path: Path) -> list[FeedSource]:
    data = json.loads(read_text(path))
    sources: list[FeedSource] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url:
            continue
        hints = [str(value) for value in item.get("category_hints", []) if str(value) in CATEGORIES]
        quality = int(item.get("quality") or 3)
        sources.append(FeedSource(name=name, url=url, category_hints=hints, quality=max(1, min(5, quality))))
    return sources


def score_item(item: FeedItem, lookback_days: int) -> FeedItem:
    haystack = f"{item.title}\n{item.summary}\n{item.source.name}".lower()
    score = item.source.quality
    categories: list[str] = []
    reasons: list[str] = [f"来源质量 +{item.source.quality}"]

    for category in item.source.category_hints:
        score += 2
        if category not in categories:
            categories.append(category)
        reasons.append(f"来源门类 {category} +2")

    for category in CATEGORIES:
        hits: list[str] = []
        for keyword in CATEGORY_KEYWORDS[category]:
            if keyword.lower() in haystack:
                hits.append(keyword)
        for keyword in CHINESE_HINTS[category]:
            if keyword in item.title or keyword in item.summary:
                hits.append(keyword)
        if hits:
            if category not in categories:
                categories.append(category)
            gained = min(10, 2 * len(set(hits)))
            score += gained
            reasons.append(f"{category} 关键词 {', '.join(sorted(set(hits))[:6])} +{gained}")

    if any(keyword in haystack for keyword in REJECT_KEYWORDS):
        score -= 6
        reasons.append("疑似营销/活动信息 -6")

    if item.published:
        age_days = max(0, (now_datetime() - item.published).days)
        if age_days <= lookback_days:
            score += 4
            reasons.append(f"{age_days} 天内发布 +4")
        elif age_days <= lookback_days * 2:
            score += 1
            reasons.append(f"{age_days} 天内发布 +1")
        else:
            score -= 5
            reasons.append(f"发布时间超过窗口 {age_days} 天 -5")
    else:
        score -= 1
        reasons.append("缺少发布时间 -1")

    item.score = score
    item.categories = categories
    item.score_reasons = reasons
    return item


def existing_urls(inbox: Path) -> set[str]:
    urls: set[str] = set()
    if not inbox.exists():
        return urls
    for path in inbox.glob("*.md"):
        text = read_text(path)
        for match in re.finditer(r"https?://[^\s)>\"]+", text):
            urls.add(canonical_url(match.group(0).rstrip("。，,；;")))
    return urls


def suggested_destination(categories: list[str]) -> str:
    if "工作流与知识系统" in categories:
        return "`20_资料库/工作流与自动化/`、`75_提示词库/Codex工作流/` 或 `10_项目/个人数据资产系统/`"
    if "AI与Agent工具" in categories:
        return "`20_资料库/AI产品与工具/`、`75_提示词库/Codex工作流/` 或 `10_项目/个人数据资产系统/`"
    if "商业" in categories:
        return "`20_资料库/人工智能产业/` 或 `60_行业情报/市场与商业化/`"
    if "管理" in categories:
        return "`20_资料库/管理与组织/` 或 `30_原子笔记/`"
    return "`20_资料库/`"


def value_reason(item: FeedItem) -> str:
    categories = "、".join(item.categories) if item.categories else "未分类"
    if "AI与Agent工具" in item.categories or "工作流与知识系统" in item.categories:
        return f"命中 {categories}，可能影响 Codex/OpenClaw/知识库自动化的后续设计。"
    if "商业" in item.categories:
        return f"命中 {categories}，适合作为产品策略、商业模式或市场判断素材。"
    if "管理" in item.categories:
        return f"命中 {categories}，适合提炼团队、组织或决策方法。"
    return "来源质量较高，但需要人工判断是否值得继续阅读。"


def has_distinct_original_summary(item: FeedItem) -> bool:
    return bool(item.summary and item.summary_zh and item.summary.strip() != item.summary_zh.strip())


def sanitize_filename(value: str, max_len: int = 80) -> str:
    value = re.sub(r"[\\/:*?\"<>|#\n\r\t]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len].strip() or "未命名情报"


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


def render_report(
    selected: list[FeedItem],
    candidates: list[FeedItem],
    failures: list[tuple[str, str]],
    *,
    lookback_days: int,
    min_score: int,
    include_older: bool,
    wrote_inbox: list[Path],
) -> str:
    window_text = f"最近 {lookback_days} 天"
    if include_older:
        window_text += "，已显式允许窗口外内容"
    else:
        window_text += "，默认排除窗口外或缺少发布时间的内容"
    lines = [
        "# 前沿情报巡检",
        "",
        "## 总览",
        "",
        f"- 生成时间：{now_text()}",
        "- 生成来源：Codex / frontier-watch",
        f"- 时间窗口：{window_text}",
        f"- 入选门槛：分数 >= {min_score}，且命中至少一个门类",
        f"- 候选数量：{len(candidates)}",
        f"- 入选数量：{len(selected)}",
        f"- 本次入箱：{len(wrote_inbox)}",
        "",
        "## 入选情报",
        "",
    ]
    if not selected:
        lines.append("暂无达到门禁的情报。")
        lines.append("")
    for index, item in enumerate(selected, start=1):
        published = item.published.strftime("%Y-%m-%d %H:%M:%S %z") if item.published else "未知"
        lines.extend(
            [
                f"### {index}. {item.title}",
                "",
                f"- 门类：{', '.join(item.categories) if item.categories else '未分类'}",
                f"- 来源：{item.source.name}",
                f"- 作者：{item.author or '未知'}",
                f"- 发布时间：{published}",
                f"- 分数：{item.score}",
                f"- 原文链接：[{item.source.name}]({item.url})",
                f"- 为什么值得看：{value_reason(item)}",
                f"- 建议去向：{suggested_destination(item.categories)}",
                f"- 入箱建议：{'跳过，已存在相同链接' if item.duplicate else '可入箱，阅读后再决定是否沉淀'}",
                "",
                "#### 中文摘要",
                "",
                item.summary_zh or item.summary or "RSS/Atom 未提供摘要，建议打开原文判断。",
                "",
            ]
        )
        if has_distinct_original_summary(item):
            lines.extend(["#### 原文摘录", "", item.summary, ""])
        lines.extend(["#### 命中依据", ""])
        for reason in item.score_reasons[:8]:
            lines.append(f"- {reason}")
        lines.append("")

    if wrote_inbox:
        lines.extend(["## 已写入收件箱", ""])
        for path in wrote_inbox:
            rel_path = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            lines.append(f"- `{rel_path}`")
        lines.append("")

    if failures:
        lines.extend(["## 来源失败", ""])
        for name, reason in failures:
            lines.append(f"- {name}：{reason}")
        lines.append("")

    high_candidates = [item for item in candidates if item not in selected][:10]
    if high_candidates:
        lines.extend(["## 未入选高分候选", ""])
        for item in high_candidates:
            lines.append(f"- {item.score} 分 / {item.source.name} / [{item.title}]({item.url})")
        lines.append("")

    lines.extend(
        [
            "## 处理边界",
            "",
            "- 本报告只做情报候选筛选，不确认事实、不直接沉淀长期知识。",
            "- 默认不入箱；只有显式 `--write-inbox` 才把入选项写入 `00_收件箱/`。",
            "- 入箱后仍由 `inbox-triage` 分拣，用户阅读反馈后再决定是否沉淀。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_inbox_note(item: FeedItem) -> str:
    published = item.published.strftime("%Y-%m-%d") if item.published else ""
    categories = item.categories or ["未分类"]
    summary_len = len(item.summary_zh or item.summary)
    lines = [
        "---",
        "类别: 收件箱",
        "资料类型: 前沿情报",
        f"来源平台: {item.source.name}",
        f"标题: {json.dumps(item.title, ensure_ascii=False)}",
        f"作者或频道: {json.dumps(item.author or item.source.name, ensure_ascii=False)}",
        f"发布时间: {published}",
        f"捕获时间: {json.dumps(now_text(), ensure_ascii=False)}",
        f"来源链接: {json.dumps(item.url, ensure_ascii=False)}",
        f"原始链接: {json.dumps(item.url, ensure_ascii=False)}",
        "解析工具: frontier-watch",
        "解析器: RSS/Atom",
        "解析状态: 已解析",
        "内容质量: 可推送",
        "质量门禁: 已通过前沿情报来源和分数门禁，可进入分拣和前台阅读候选；沉淀前仍需阅读原文核验。",
        "内容摘录来源: RSS/Atom 摘要中文译写",
        "内容摘录后端:",
        f"内容摘录字数: {summary_len}",
        "阅读状态: 未读",
        "处理状态: 待分拣",
        "关联项目:",
    ]
    if "AI与Agent工具" in categories or "工作流与知识系统" in categories:
        lines.append("  - 个人数据资产系统")
    lines.extend(["关联领域: []", "主题:"])
    for category in categories:
        lines.append(f"  - {category}")
    lines.extend(["标签:"])
    for tag in ["前沿情报", *categories]:
        lines.append(f"  - {tag}")
    lines.extend(["敏感状态: 未知", "---", "", f"# {item.title}", ""])
    lines.extend(
        [
            "## 基础信息",
            "",
            f"- 来源：{item.source.name}",
            f"- 作者：{item.author or '未知'}",
            f"- 发布时间：{published or '未知'}",
            f"- 原文链接：{item.url}",
            f"- 情报门类：{', '.join(categories)}",
            f"- 分数：{item.score}",
            "",
            "## 中文摘要",
            "",
            item.summary_zh or item.summary or "RSS/Atom 未提供摘要，建议打开原文判断。",
            "",
        ]
    )
    if has_distinct_original_summary(item):
        lines.extend(["## 原文摘录", "", item.summary, ""])
    lines.extend(
        [
            "## 为什么保存",
            "",
            f"- {value_reason(item)}",
            f"- 建议去向：{suggested_destination(categories)}",
            "",
            "## 初步想法",
            "",
            "- 待阅读后补充。",
            "",
            "## 阅读思考",
            "",
            "- 待阅读后补充。",
            "- 可记录：这条情报是否影响当前项目、商业判断、管理方法或后续沉淀方向。",
            "",
            "## 后续处理建议",
            "",
            "- 先打开原文判断内容质量。",
            "- 如果值得保留，再反馈 `已读`、`沉淀`、`跳过` 或 `继续追踪`。",
            "",
            "## 原始链接",
            "",
            item.url,
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_inbox_items(items: list[FeedItem], inbox: Path) -> list[Path]:
    inbox.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    today = now_datetime().strftime("%Y-%m-%d")
    for item in items:
        if item.duplicate:
            continue
        filename = f"{today} 前沿情报 - {sanitize_filename(item.title)}.md"
        path = unique_path(inbox, filename)
        path.write_text(render_inbox_note(item), encoding="utf-8")
        written.append(path)
    return written


def parse_selected_indexes(value: str, total: int) -> list[int]:
    if not value:
        return list(range(total))
    indexes: list[int] = []
    for part in re.split(r"[,，\s]+", value.strip()):
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            if start_text.isdigit() and end_text.isdigit():
                start = max(1, int(start_text))
                end = min(total, int(end_text))
                indexes.extend(range(start - 1, end))
            continue
        if part.isdigit():
            index = int(part)
            if 1 <= index <= total:
                indexes.append(index - 1)
    seen: set[int] = set()
    unique: list[int] = []
    for index in indexes:
        if index not in seen:
            unique.append(index)
            seen.add(index)
    return unique


def parse_report_items(path: Path) -> list[FeedItem]:
    text = read_text(path)
    chunks = re.split(r"\n###\s+\d+\.\s+", "\n" + text)
    items: list[FeedItem] = []
    for chunk in chunks[1:]:
        title = clean_text(chunk.splitlines()[0], 240) if chunk.splitlines() else ""
        source_name = ""
        author = ""
        published: dt.datetime | None = None
        score = 0
        categories: list[str] = []
        url = ""

        if match := re.search(r"^- 门类：(.+)$", chunk, flags=re.MULTILINE):
            categories = [part.strip() for part in match.group(1).split(",") if part.strip() in CATEGORIES]
        if match := re.search(r"^- 来源：(.+)$", chunk, flags=re.MULTILINE):
            source_name = clean_text(match.group(1), 120)
        if match := re.search(r"^- 作者：(.+)$", chunk, flags=re.MULTILINE):
            author = "" if match.group(1).strip() == "未知" else clean_text(match.group(1), 200)
        if match := re.search(r"^- 发布时间：(.+)$", chunk, flags=re.MULTILINE):
            published = parse_datetime(match.group(1))
        if match := re.search(r"^- 分数：(\d+)", chunk, flags=re.MULTILINE):
            score = int(match.group(1))
        if match := re.search(r"^- 原文链接：\[[^\]]+\]\(([^)]+)\)", chunk, flags=re.MULTILINE):
            url = normalize_http_url(match.group(1))

        summary = ""
        summary_zh = ""
        if match := re.search(r"#### 中文摘要\s*\n\n(.*?)(?:\n#### 原文摘录|\n#### 命中依据|\n## |\Z)", chunk, flags=re.DOTALL):
            summary_zh = clean_text(match.group(1))
        if match := re.search(r"#### 原文摘录\s*\n\n(.*?)(?:\n#### 命中依据|\n## |\Z)", chunk, flags=re.DOTALL):
            summary = clean_text(match.group(1))
        if not summary and not summary_zh:
            if match := re.search(r"#### 摘要\s*\n\n(.*?)(?:\n#### 命中依据|\n## |\Z)", chunk, flags=re.DOTALL):
                summary = clean_text(match.group(1))

        reasons: list[str] = []
        if match := re.search(r"#### 命中依据\s*\n\n(.*?)(?:\n### |\n## |\Z)", chunk, flags=re.DOTALL):
            for line in match.group(1).splitlines():
                line = line.strip()
                if line.startswith("- "):
                    reasons.append(line[2:].strip())

        if not title or not url:
            continue
        item = FeedItem(
            source=FeedSource(name=source_name or "未知来源", url=url, category_hints=categories, quality=3),
            title=title,
            url=url,
            summary=summary,
            published=published,
            author=author,
            summary_zh=summary_zh,
            categories=categories,
            score=score,
            score_reasons=reasons,
        )
        items.append(item)
    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect curated frontier intelligence for my-mind.")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES), help="JSON source config.")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Inbox directory.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Run-report directory.")
    parser.add_argument("--lookback-days", type=int, default=14, help="Freshness window used for scoring.")
    parser.add_argument("--limit", type=int, default=3, help="Maximum selected items.")
    parser.add_argument("--per-category", type=int, default=1, help="Maximum items per category before filling remaining slots.")
    parser.add_argument("--min-score", type=int, default=12, help="Minimum score for selected items.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds per source.")
    parser.add_argument("--category", action="append", choices=CATEGORIES, help="Limit to one or more categories.")
    parser.add_argument("--dry-run", action="store_true", help="Print report instead of writing it.")
    parser.add_argument("--write-inbox", action="store_true", help="Write selected non-duplicate items to 00_收件箱.")
    parser.add_argument("--allow-duplicates", action="store_true", help="Allow selecting items whose URLs already exist in inbox.")
    parser.add_argument("--include-older", action="store_true", help="Allow selecting items outside the freshness window.")
    parser.add_argument("--from-report", help="Use selected items from an existing frontier-watch report instead of fetching feeds.")
    parser.add_argument("--select", default="", help="Report item numbers to use, such as 1,3 or 1-3. Defaults to all report items.")
    parser.add_argument("--no-translate-summaries", action="store_true", help="Disable automatic Chinese translation for selected summaries.")
    parser.add_argument("--translation-timeout", type=int, default=8, help="HTTP timeout seconds per selected summary translation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = Path(args.sources)
    inbox = Path(args.inbox)
    run_dir = Path(args.run_dir)
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    if not inbox.is_absolute():
        inbox = ROOT / inbox
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir

    if args.from_report:
        report_path = Path(args.from_report)
        if not report_path.is_absolute():
            report_path = ROOT / report_path
        report_items = parse_report_items(report_path)
        selected_indexes = parse_selected_indexes(args.select, len(report_items))
        selected = [report_items[index] for index in selected_indexes]
        existing = existing_urls(inbox)
        for item in selected:
            item.duplicate = canonical_url(item.url) in existing
        writable = selected if args.allow_duplicates else [item for item in selected if not item.duplicate]
        failures: list[tuple[str, str]] = []
        if not args.no_translate_summaries:
            for error in ensure_chinese_summaries(selected, timeout=max(args.translation_timeout, 2)):
                failures.append(("中文摘要", error))
        wrote_inbox = write_inbox_items(writable, inbox) if args.write_inbox else []
        report = render_report(
            selected,
            report_items,
            failures,
            lookback_days=args.lookback_days,
            min_score=args.min_score,
            include_older=True,
            wrote_inbox=wrote_inbox,
        )
        if args.dry_run:
            print(report, end="")
            return 0
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = unique_path(run_dir, f"前沿情报入箱-{now_filename()}.md")
        output_path.write_text(report, encoding="utf-8")
        print(output_path.relative_to(ROOT) if output_path.is_relative_to(ROOT) else output_path)
        return 0

    categories_filter = set(args.category or CATEGORIES)
    sources = load_sources(source_path)
    existing = existing_urls(inbox)
    items_by_url: dict[str, FeedItem] = {}
    failures: list[tuple[str, str]] = []

    for source in sources:
        if source.category_hints and not (set(source.category_hints) & categories_filter):
            continue
        try:
            payload = fetch_url(source.url, timeout=max(args.timeout, 5))
            parsed = parse_feed(source, payload)
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
            failures.append((source.name, str(exc)))
            continue
        for item in parsed:
            score_item(item, max(args.lookback_days, 1))
            item.categories = [category for category in item.categories if category in categories_filter]
            if not item.categories:
                continue
            item.duplicate = canonical_url(item.url) in existing
            previous = items_by_url.get(item.url)
            if previous is None or item.score > previous.score:
                items_by_url[item.url] = item

    fresh_cutoff = now_datetime() - dt.timedelta(days=max(args.lookback_days, 1))
    candidates = sorted(items_by_url.values(), key=lambda item: (item.score, item.published or dt.datetime.min.replace(tzinfo=TZ)), reverse=True)
    candidates = [item for item in candidates if item.score >= args.min_score and (args.allow_duplicates or not item.duplicate)]
    if not args.include_older:
        candidates = [item for item in candidates if item.published is not None and item.published >= fresh_cutoff]

    selected: list[FeedItem] = []
    per_category_counts = {category: 0 for category in CATEGORIES}
    for item in candidates:
        if len(selected) >= max(args.limit, 1):
            break
        primary = item.categories[0]
        if per_category_counts.get(primary, 0) >= max(args.per_category, 0):
            continue
        selected.append(item)
        per_category_counts[primary] = per_category_counts.get(primary, 0) + 1

    for item in candidates:
        if len(selected) >= max(args.limit, 1):
            break
        if item not in selected:
            selected.append(item)

    if not args.no_translate_summaries:
        for error in ensure_chinese_summaries(selected, timeout=max(args.translation_timeout, 2)):
            failures.append(("中文摘要", error))

    wrote_inbox = write_inbox_items(selected, inbox) if args.write_inbox else []
    report = render_report(
        selected,
        candidates,
        failures,
        lookback_days=args.lookback_days,
        min_score=args.min_score,
        include_older=args.include_older,
        wrote_inbox=wrote_inbox,
    )

    if args.dry_run:
        print(report, end="")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(run_dir, f"前沿情报巡检-{now_filename()}.md")
    output_path.write_text(report, encoding="utf-8")
    print(output_path.relative_to(ROOT) if output_path.is_relative_to(ROOT) else output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
