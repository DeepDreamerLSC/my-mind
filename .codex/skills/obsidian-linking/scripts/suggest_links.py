#!/usr/bin/env python3
"""Suggest Obsidian wikilinks for my-mind Markdown notes."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


INCLUDE_ROOTS = (
    "10_项目",
    "15_索引",
    "20_资料库",
    "30_原子笔记",
    "35_主动回忆",
    "60_行业情报",
    "65_洞察",
    "75_提示词库",
    "80_复盘",
)

EXCLUDE_ROOTS = (
    "00_收件箱",
    "05_流转区",
    "85_运行记录",
    ".git",
    ".obsidian",
    ".codex",
)

LINK_FIELDS = ("关联项目", "关联领域", "主题", "证据来源")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+#.-]{1,}|[\u4e00-\u9fff]{2,}")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
STOP_FRAGMENTS = {
    "00_收件箱",
    "05_流转区",
    "10_项目",
    "15_索引",
    "20_资料库",
    "30_原子笔记",
    "35_主动回忆",
    "60_行业情报",
    "65_洞察",
    "75_提示词库",
    "80_复盘",
    "收件箱",
    "流转区",
    "项目",
    "索引",
    "资料库",
    "原子笔记",
    "主动回忆",
    "行业情报",
    "洞察",
    "提示词库",
    "复盘",
    "目录说明",
    "项目总览",
    "资料库候选",
}


@dataclass(frozen=True)
class Candidate:
    path: Path
    title: str
    aliases: tuple[str, ...]
    kind: str

    @property
    def rel_no_ext(self) -> str:
        return self.path.with_suffix("").as_posix()

    @property
    def wikilink(self) -> str:
        display = self.title.strip() or self.path.stem
        return f"[[{self.rel_no_ext}|{display}]]"


@dataclass(frozen=True)
class Suggestion:
    candidate: Candidate
    score: int
    reasons: tuple[str, ...]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end == -1:
        return "", text
    body_start = text.find("\n", end + 4)
    if body_start == -1:
        return text[4:end], ""
    return text[4:end], text[body_start + 1 :]


def parse_frontmatter(frontmatter: str) -> dict[str, object]:
    data: dict[str, object] = {}
    current_key: str | None = None
    for raw_line in frontmatter.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith((" ", "\t")):
            stripped = raw_line.strip()
            if current_key and stripped.startswith("- "):
                value = clean_scalar(stripped[2:])
                existing = data.get(current_key)
                if not isinstance(existing, list):
                    existing = [] if existing in (None, "") else [str(existing)]
                existing.append(value)
                data[current_key] = existing
            continue
        if ":" not in raw_line:
            current_key = None
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            data[key] = []
        elif value.startswith("[") and value.endswith("]"):
            data[key] = parse_inline_list(value)
        else:
            data[key] = clean_scalar(value)
    return data


def parse_inline_list(value: str) -> list[str]:
    inner = value[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    current = []
    quote: str | None = None
    for char in inner:
        if char in ("'", '"'):
            quote = None if quote == char else char if quote is None else quote
            current.append(char)
            continue
        if char == "," and quote is None:
            items.append(clean_scalar("".join(current).strip()))
            current = []
        else:
            current.append(char)
    if current:
        items.append(clean_scalar("".join(current).strip()))
    return [item for item in items if item]


def clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def as_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def title_from_text(path: Path, text: str, meta: dict[str, object]) -> str:
    if path.name == "目录说明.md" and len(path.parts) >= 2:
        return path.parts[-2]
    if path.name == "项目总览.md" and len(path.parts) >= 2:
        return path.parts[-2]
    title_values = as_list(meta.get("标题"))
    if title_values:
        return title_values[0]
    match = H1_RE.search(text)
    if match:
        return match.group(1).strip()
    return path.stem


def aliases_from_meta(meta: dict[str, object]) -> tuple[str, ...]:
    aliases: list[str] = []
    for key in ("别名", "aliases", "alias", "主题"):
        aliases.extend(as_list(meta.get(key)))
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def candidate_kind(path: Path) -> str:
    if not path.parts:
        return "笔记"
    root = path.parts[0]
    if root == "10_项目":
        return "项目" if path.name == "项目总览.md" else "项目文档"
    if root == "15_索引":
        return "索引"
    if root == "20_资料库":
        return "领域" if path.name == "目录说明.md" else "资料"
    if root == "30_原子笔记":
        return "原子笔记"
    if root == "35_主动回忆":
        return "回忆卡片"
    if root == "60_行业情报":
        return "行业情报"
    if root == "65_洞察":
        return "洞察"
    if root == "75_提示词库":
        return "提示词场景" if path.name == "目录说明.md" else "提示词"
    if root == "80_复盘":
        return "复盘"
    return "笔记"


def iter_markdown_files(root: Path) -> Iterable[Path]:
    for include in INCLUDE_ROOTS:
        base = root / include
        if not base.exists():
            continue
        yield from sorted(base.rglob("*.md"))


def should_exclude(path: Path) -> bool:
    return bool(path.parts and path.parts[0] in EXCLUDE_ROOTS)


def collect_candidates(root: Path, source_paths: set[Path]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for path in iter_markdown_files(root):
        rel = path.relative_to(root)
        if rel in source_paths or should_exclude(rel):
            continue
        text = read_text(path)
        frontmatter, _ = split_frontmatter(text)
        meta = parse_frontmatter(frontmatter)
        title = title_from_text(rel, text, meta)
        candidates.append(
            Candidate(
                path=rel,
                title=title,
                aliases=aliases_from_meta(meta),
                kind=candidate_kind(rel),
            )
        )
    return candidates


def existing_links(text: str) -> set[str]:
    links: set[str] = set()
    for match in WIKILINK_RE.finditer(text):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        links.add(normalize_key(target))
        links.add(normalize_key(Path(target).name))
    return links


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def title_fragments(candidate: Candidate) -> list[str]:
    raw_parts = [candidate.title, candidate.path.stem, *candidate.path.parts[:-1], *candidate.aliases]
    fragments: list[str] = []
    for raw in raw_parts:
        cleaned = re.sub(r"[_/|#()\[\]【】《》:：,，.。\-]+", " ", raw)
        fragments.extend(part.strip() for part in cleaned.split() if is_meaningful_fragment(part.strip()))
        for token in TOKEN_RE.findall(raw):
            if is_meaningful_fragment(token):
                fragments.append(token)
    return list(dict.fromkeys(fragments))


def is_meaningful_fragment(value: str) -> bool:
    value = value.strip()
    if not (2 <= len(value) <= 30):
        return False
    if value in STOP_FRAGMENTS:
        return False
    if re.fullmatch(r"\d{2,4}", value):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    return True


def score_candidate(source_text: str, candidate: Candidate, existing: set[str]) -> Suggestion | None:
    normalized_targets = {
        normalize_key(candidate.title),
        normalize_key(candidate.path.stem),
        normalize_key(candidate.rel_no_ext),
    }
    if normalized_targets & existing:
        return None

    score = 0
    reasons: list[str] = []
    searchable = source_text.lower()

    names = [candidate.title, candidate.path.stem, *candidate.aliases]
    for name in dict.fromkeys(name for name in names if name):
        if is_meaningful_fragment(name) and name.lower() in searchable:
            score += 45
            reasons.append(f"出现名称：{name}")
            break

    hit_fragments: list[str] = []
    for fragment in title_fragments(candidate):
        if fragment.lower() in searchable:
            hit_fragments.append(fragment)
            score += min(18, max(4, len(fragment)))
    if hit_fragments:
        reasons.append("命中关键词：" + "、".join(hit_fragments[:5]))

    if candidate.path.name == "目录说明.md":
        score += 6
        reasons.append("可作为领域入口")
    if candidate.path.name == "项目总览.md":
        score += 8
        reasons.append("可作为项目入口")
    if candidate.kind in {"项目", "领域", "洞察", "提示词", "提示词场景"}:
        score += 4

    if score < 12:
        return None
    return Suggestion(candidate=candidate, score=score, reasons=tuple(dict.fromkeys(reasons)))


def suggest_links(source_text: str, candidates: list[Candidate], limit: int) -> list[Suggestion]:
    existing = existing_links(source_text)
    suggestions = [
        suggestion
        for candidate in candidates
        if (suggestion := score_candidate(source_text, candidate, existing))
    ]
    suggestions.sort(key=lambda item: (item.score, item.candidate.kind, item.candidate.title), reverse=True)
    return suggestions[:limit]


def field_suggestions(source_path: Path, meta: dict[str, object], candidates: list[Candidate]) -> list[str]:
    lines: list[str] = []
    target_by_key: dict[str, Candidate] = {}
    for candidate in candidates:
        keys = {candidate.title, candidate.path.stem, candidate.rel_no_ext}
        if candidate.path.name == "目录说明.md" and len(candidate.path.parts) >= 2:
            keys.add(candidate.path.parts[-2])
        if candidate.path.name == "项目总览.md" and len(candidate.path.parts) >= 2:
            keys.add(candidate.path.parts[-2])
        for key in keys:
            target_by_key[normalize_key(key)] = candidate

    for field in LINK_FIELDS:
        values = as_list(meta.get(field))
        plain_values = [value for value in values if value and "[[" not in value]
        for value in plain_values:
            if value in STOP_FRAGMENTS:
                continue
            target = target_by_key.get(normalize_key(value))
            if target:
                lines.append(f"- `{field}`：`{value}` 可改为 `{target.wikilink}`。")
            else:
                lines.append(f"- `{field}`：`{value}` 可先改为 `[[{value}]]`，后续再决定是否创建主题页。")

    if source_path.parts[:1] == ("20_资料库",) and len(source_path.parts) >= 2:
        domain = source_path.parts[1]
        target = target_by_key.get(normalize_key(domain))
        if target and target.wikilink not in "\n".join(lines):
            lines.append(f"- `关联领域`：当前位于 `{domain}`，建议包含 `{target.wikilink}`。")

    if source_path.parts[:1] == ("75_提示词库",) and len(source_path.parts) >= 2:
        scene = source_path.parts[1]
        target = target_by_key.get(normalize_key(scene))
        if target:
            lines.append(f"- `主题`：当前提示词场景是 `{scene}`，建议包含 `{target.wikilink}`。")

    if source_path.parts[:1] == ("65_洞察",) and len(source_path.parts) >= 2:
        state = source_path.parts[1]
        lines.append(f"- `洞察状态`：当前目录显示为 `{state}`，请确认 front matter 状态与目录一致。")

    return list(dict.fromkeys(lines))


def render_note_report(root: Path, note: Path, candidates: list[Candidate], limit: int) -> str:
    text = read_text(root / note)
    frontmatter, _ = split_frontmatter(text)
    meta = parse_frontmatter(frontmatter)
    title = title_from_text(note, text, meta)
    link_suggestions = suggest_links(text, candidates, limit)
    field_lines = field_suggestions(note, meta, candidates)

    out: list[str] = []
    out.append(f"## {title}")
    out.append("")
    out.append(f"- 文件：`{note.as_posix()}`")
    out.append(f"- 类别：`{as_list(meta.get('类别'))[0] if as_list(meta.get('类别')) else '未标注'}`")
    out.append("")
    out.append("### 字段规范化建议")
    out.append("")
    if field_lines:
        out.extend(field_lines)
    else:
        out.append("- 暂无明显字段规范化建议。")
    out.append("")
    out.append("### 建议新增链接")
    out.append("")
    if link_suggestions:
        out.append("| 分数 | 类型 | 建议链接 | 原因 | 路径 |")
        out.append("| ---: | --- | --- | --- | --- |")
        for suggestion in link_suggestions:
            reason = "；".join(suggestion.reasons) or "局部词汇相似"
            candidate = suggestion.candidate
            out.append(
                f"| {suggestion.score} | {candidate.kind} | {candidate.wikilink} | "
                f"{reason} | `{candidate.path.as_posix()}` |"
            )
    else:
        out.append("- 暂无足够可信的新增链接建议。")
    out.append("")
    out.append("### 后续动作")
    out.append("")
    out.append("- 人工确认后，再把高可信链接补入 front matter 或正文。")
    out.append("- 不要因为出现链接建议，就把候选内容直接升级为已确认知识。")
    out.append("")
    return "\n".join(out)


def resolve_note(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.relative_to(root)
        except ValueError as exc:
            raise SystemExit(f"目标笔记不在当前仓库内：{path}") from exc
    return path


def unique_report_path(root: Path) -> Path:
    report_dir = root / "85_运行记录"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    path = report_dir / f"关联建议-{stamp}.md"
    if not path.exists():
        return path
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return report_dir / f"关联建议-{stamp}.md"


def build_report(root: Path, notes: list[Path], limit: int) -> str:
    candidates = collect_candidates(root, set(notes))
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [
        "---",
        "类别: 运行记录",
        "记录类型: Obsidian关联建议",
        f"生成时间: {now}",
        "---",
        "",
        "# Obsidian 关联建议",
        "",
        f"- 目标笔记数：{len(notes)}",
        f"- 候选链接池：{len(candidates)}",
        "",
    ]
    for note in notes:
        out.append(render_note_report(root, note, candidates, limit))
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--note", action="append", required=True, help="Target Markdown note path. Can be repeated.")
    parser.add_argument("--limit", type=int, default=12, help="Maximum link suggestions per note.")
    parser.add_argument("--write", action="store_true", help="Write report under 85_运行记录 instead of printing only.")
    args = parser.parse_args(argv)

    root = Path.cwd()
    notes = [resolve_note(root, value) for value in args.note]
    missing = [note for note in notes if not (root / note).is_file()]
    if missing:
        for note in missing:
            print(f"找不到目标笔记：{note}", file=sys.stderr)
        return 2

    report = build_report(root, notes, max(args.limit, 1))
    if args.write:
        path = unique_report_path(root)
        path.write_text(report, encoding="utf-8")
        print(path.as_posix())
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
