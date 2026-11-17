#!/usr/bin/env python3
"""Build a layered, mobile-readable view from source-backed media transcripts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence


SHORT_TRANSCRIPT_SECONDS = 180
MIN_CLAIMS = 3
MAX_CLAIMS = 7
MIN_CHAPTERS = 3
MAX_CHAPTERS = 8
ELLIPSIS_RE = re.compile(r"(?:\.{3,}|…+|\[…\])\s*$")
TRUNCATION_MARKERS = ("摘录已截断", "仍包含截断", "补全文稿", "查看全文")
TIMELINE_RE = re.compile(r"^-\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[：:]\s*(.+)$", flags=re.M)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", flags=re.M)
TRANSITION_RE = re.compile(
    r"^(接下来|然后|下面|另外|与此同时|最后|总结|回到|首先|其次|第三|现在|next|then|another|finally|in summary|first|second|third)\b",
    flags=re.I,
)


@dataclass(frozen=True)
class TimelineEntry:
    start_seconds: int
    end_seconds: int
    text: str


@dataclass(frozen=True)
class Claim:
    statement: str
    start_seconds: int
    end_seconds: int
    evidence: str


@dataclass(frozen=True)
class Chapter:
    title: str
    start_seconds: int
    end_seconds: int
    summary: str
    evidence: str
    transcript_text: str


@dataclass(frozen=True)
class ReadingVersion:
    conclusion: str
    claims: tuple[Claim, ...]
    chapters: tuple[Chapter, ...]
    transcript: str
    duration_seconds: int
    transcript_mode: str
    source_language: str

    @property
    def has_transcript(self) -> bool:
        return bool(self.transcript.strip())

    def digest(self) -> str:
        payload = {
            "conclusion": self.conclusion,
            "claims": [claim.__dict__ for claim in self.claims],
            "chapters": [chapter.__dict__ for chapter in self.chapters],
            "duration_seconds": self.duration_seconds,
            "transcript_mode": self.transcript_mode,
            "source_language": self.source_language,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_text(value: object) -> str:
    text = str(value or "").replace("\u3000", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def contains_cjk(value: object) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", str(value or "")))


def parse_timecode(value: object) -> int:
    text = normalize_text(value)
    if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        return 0
    parts = [int(part) for part in text.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def format_timecode(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def markdown_sections(text: str) -> list[tuple[int, str, str]]:
    matches = list(HEADING_RE.finditer(text))
    sections: list[tuple[int, str, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        end = len(text)
        for candidate in matches[index + 1 :]:
            if len(candidate.group(1)) <= level:
                end = candidate.start()
                break
        sections.append((level, normalize_text(match.group(2)), text[match.end() : end].strip()))
    return sections


def section_by_titles(text: str, titles: Sequence[str]) -> str:
    wanted = {title.casefold() for title in titles}
    for _, title, body in markdown_sections(text):
        if title.casefold() in wanted and body.strip():
            return body.strip()
    return ""


def remove_markdown(value: object) -> str:
    text = normalize_text(value)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`>#]", "", text)
    text = re.sub(r"^[-+]\s+", "", text)
    return normalize_text(text)


def complete_statement(value: object) -> str:
    text = remove_markdown(value)
    text = re.sub(r"(?:\.{3,}|…+)", "。", text)
    text = re.sub(r"^(?:所以|不过|然后|接下来|而是|但是|但|其实|再来讲|首先|其次|第三|最后)[，,：:\s]*", "", text)
    text = ELLIPSIS_RE.sub("", text).rstrip("，,、；;：: ")
    if not text:
        return ""
    if text[-1] not in "。！？!?；;":
        text += "。"
    return text


def split_long_unit(value: object, maximum: int = 120) -> list[str]:
    remaining = remove_markdown(value)
    result: list[str] = []
    while len(remaining) > maximum:
        window = remaining[: maximum + 1]
        breakpoints = [match.end() for match in re.finditer(r"[。！？!?；;，,：:]|\s+", window)]
        usable = [position for position in breakpoints if position >= maximum // 2]
        cut = max(usable) if usable else maximum
        result.append(complete_statement(remaining[:cut]))
        remaining = remaining[cut:].strip()
    if remaining:
        result.append(complete_statement(remaining))
    return [item for item in result if item]


def sentence_units(value: object) -> list[str]:
    raw = str(value or "").replace("\r", "\n")
    raw = re.sub(r"https?://\S+", "", raw)
    raw = re.sub(r"^[-*]\s+", "", raw, flags=re.M)
    raw = re.sub(r"\n{2,}", "\n", raw)
    units: list[str] = []
    buffer = ""
    for line in raw.splitlines():
        line = remove_markdown(line)
        if not line:
            continue
        buffer = f"{buffer} {line}".strip()
        if re.search(r"[。！？!?；;.]$", line) or len(buffer) >= 90:
            pieces = re.split(r"(?<=[。！？!?；;])\s+|(?<=[.!?])\s+(?=[A-Z0-9])", buffer)
            for piece in pieces:
                units.extend(split_long_unit(piece))
            buffer = ""
    if buffer:
        units.extend(split_long_unit(buffer))
    return [unit for unit in units if unit and not any(marker in unit for marker in TRUNCATION_MARKERS)]


def text_tokens(value: object) -> set[str]:
    text = remove_markdown(value).casefold()
    words = set(re.findall(r"[a-z0-9][a-z0-9_+-]{1,}", text))
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", text))
    words.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return {token for token in words if token}


def similarity(left: object, right: object) -> float:
    left_tokens = text_tokens(left)
    right_tokens = text_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def bounded_complete_text(value: object, minimum: int, maximum: int) -> str:
    units = sentence_units(value)
    if not units:
        return ""
    selected: list[str] = []
    for unit in units:
        candidate = "".join([*selected, unit])
        if selected and len(candidate) > maximum:
            break
        selected.append(unit)
        if len("".join(selected)) >= minimum:
            break
    result = "".join(selected)
    if len(result) <= maximum:
        return result
    clauses = re.split(r"(?<=[，,；;])", result)
    trimmed = ""
    for clause in clauses:
        if trimmed and len(trimmed + clause) > maximum - 1:
            break
        trimmed += clause
    if not trimmed:
        window = result[:maximum]
        breaks = [match.end() for match in re.finditer(r"[。！？!?；;，,：:]|\s+", window)]
        usable = [position for position in breaks if position >= maximum // 2]
        cut = max(usable) if usable else maximum - 1
        trimmed = result[:cut]
    return complete_statement(trimmed)


def duration_seconds(metadata: Mapping[str, str], body: str, timeline: Sequence[TimelineEntry], transcript: str) -> int:
    for key in ("时长", "视频时长", "音频时长"):
        parsed = parse_timecode(metadata.get(key, ""))
        if parsed:
            return parsed
    match = re.search(r"^-\s*(?:时长|视频时长|音频时长)\s*[：:]\s*(\d{1,2}:\d{2}(?::\d{2})?)", body, flags=re.M)
    if match:
        return parse_timecode(match.group(1))
    if timeline:
        return max(entry.end_seconds for entry in timeline)
    units = sentence_units(transcript)
    return max(1, int(sum(len(unit) for unit in units) / 4)) if units else 0


def raw_timeline(body: str, estimated_duration: int = 0) -> list[TimelineEntry]:
    matches = list(TIMELINE_RE.finditer(section_by_titles(body, ("时间线摘录", "时间线", "章节目录"))))
    entries: list[TimelineEntry] = []
    for index, match in enumerate(matches):
        start = parse_timecode(match.group(1))
        end = parse_timecode(matches[index + 1].group(1)) if index + 1 < len(matches) else max(start + 30, estimated_duration)
        text = remove_markdown(match.group(2))
        if text and not ELLIPSIS_RE.search(text):
            entries.append(TimelineEntry(start, max(start + 1, end), text))
    return entries


def transcript_text(body: str) -> str:
    return section_by_titles(
        body,
        ("转写摘录", "完整转写", "完整转录", "字幕正文", "字幕文本", "Transcript", "Full transcript"),
    )


def source_summary(body: str) -> str:
    return section_by_titles(body, ("摘要", "中文摘要", "内容摘要", "视频摘要"))


def source_key_points(body: str) -> list[str]:
    section = section_by_titles(body, ("关键点", "核心观点", "重点", "要点"))
    return [complete_statement(match.group(1)) for match in re.finditer(r"^[-*]\s+(.+)$", section, flags=re.M) if complete_statement(match.group(1))]


def synthetic_timeline(transcript: str, duration: int) -> list[TimelineEntry]:
    units = sentence_units(transcript)
    if not units:
        return []
    target = min(MAX_CHAPTERS, max(MIN_CHAPTERS, round(math.sqrt(len(units)))))
    target = min(target, len(units))
    entries: list[TimelineEntry] = []
    for index in range(target):
        start_index = round(index * len(units) / target)
        end_index = round((index + 1) * len(units) / target)
        start = round(index * duration / target) if duration else index * 60
        end = round((index + 1) * duration / target) if duration else (index + 1) * 60
        entries.append(TimelineEntry(start, max(start + 1, end), "".join(units[start_index:end_index])))
    return entries


def boundary_score(left: TimelineEntry, right: TimelineEntry) -> float:
    score = 1.0 - similarity(left.text, right.text)
    if TRANSITION_RE.search(remove_markdown(right.text)):
        score += 1.0
    if right.start_seconds - left.end_seconds > 15:
        score += 0.5
    return score


def group_timeline(entries: Sequence[TimelineEntry], duration: int) -> list[list[TimelineEntry]]:
    if not entries:
        return []
    target = min(MAX_CHAPTERS, max(MIN_CHAPTERS, round(math.sqrt(len(entries)) * 2)))
    target = min(target, len(entries))
    if target == len(entries):
        return [[entry] for entry in entries]
    boundaries = sorted(
        range(1, len(entries)),
        key=lambda index: boundary_score(entries[index - 1], entries[index]),
        reverse=True,
    )[: target - 1]
    stops = [0, *sorted(boundaries), len(entries)]
    return [list(entries[stops[index] : stops[index + 1]]) for index in range(len(stops) - 1)]


def title_from_text(value: object) -> str:
    units = sentence_units(value)
    candidates = [unit for unit in units if 10 <= len(unit) <= 130]
    cue_re = re.compile(r"核心|关键|目标|价值|能力|流程|模式|功能|区别|方法|需要|应该|可以|支持|介绍|讲|总结|必须|真正|不等同|来自|在于", flags=re.I)
    weak_start_re = re.compile(r"^(但|而|这|它|我们|你|所以|相关|以后|现在|然后|接下来)")
    chosen = max(
        candidates or units,
        key=lambda unit: (
            2 if cue_re.search(unit) else 0,
            -1 if weak_start_re.search(unit) else 0,
            len(text_tokens(unit)),
            -len(unit),
        ),
        default="核心内容",
    )
    chosen = chosen.rstrip("。！？!?；;")
    clauses = [
        clause.strip()
        for clause in re.split(
            r"[，,；;：:。！？!?]|\s+(?=(?:很多人|其实|而|但|首先|其次|第三|再来|真正|简单来说|这才|如果|所以|不过))",
            chosen,
        )
        if clause.strip()
    ]
    suitable = [clause for clause in clauses if 8 <= len(clause) <= 42]
    title = max(
        suitable,
        key=lambda clause: (
            2 if cue_re.search(clause) else 0,
            -1 if weak_start_re.search(clause) else 0,
            1 if clause == clauses[0] else 0,
            -abs(len(clause) - 22),
        ),
        default="",
    )
    if not title:
        words = chosen.split()
        if len(words) > 1:
            selected: list[str] = []
            for word in words:
                if selected and len(" ".join([*selected, word])) > 36:
                    break
                selected.append(word)
            title = " ".join(selected)
        else:
            title = chosen[:36].strip()
    return ELLIPSIS_RE.sub("", title).strip() or "核心内容"


def chapters_from_timeline(entries: Sequence[TimelineEntry], duration: int, source_context: str = "") -> tuple[Chapter, ...]:
    chapters: list[Chapter] = []
    groups = group_timeline(entries, duration)
    for group_index, group in enumerate(groups):
        raw = " ".join(entry.text for entry in group)
        units = sentence_units(raw)
        context = list(group)
        distance = 1
        while len(remove_markdown(" ".join(entry.text for entry in context))) < 150 and distance < len(groups):
            for neighbor_index in (group_index - distance, group_index + distance):
                if 0 <= neighbor_index < len(groups):
                    context.extend(groups[neighbor_index])
            distance += 1
        summary_source = " ".join(entry.text for entry in context)
        if len(remove_markdown(summary_source)) < 150 and source_context:
            summary_source = f"{summary_source} {source_context}"
        summary = bounded_complete_text(summary_source, 150, 400)
        evidence = "".join(units[:4]) if units else summary
        chapters.append(
            Chapter(
                title=title_from_text(raw),
                start_seconds=group[0].start_seconds,
                end_seconds=group[-1].end_seconds,
                summary=summary or complete_statement(raw),
                evidence=bounded_complete_text(evidence, 60, 260),
                transcript_text=raw,
            )
        )
    return tuple(chapters)


def retitle_chapters(chapters: Sequence[Chapter], claims: Sequence[Claim]) -> tuple[Chapter, ...]:
    titled: list[Chapter] = []
    used_statements: set[str] = set()
    for chapter in chapters:
        overlapping = [
            claim
            for claim in claims
            if claim.statement not in used_statements
            and claim.start_seconds < chapter.end_seconds
            and claim.end_seconds > chapter.start_seconds
        ]
        if overlapping:
            claim = max(overlapping, key=lambda item: similarity(item.evidence, chapter.transcript_text))
            used_statements.add(claim.statement)
            title = title_from_text(claim.statement)
        else:
            title = title_from_text(chapter.transcript_text)
        titled.append(
            Chapter(
                title=title,
                start_seconds=chapter.start_seconds,
                end_seconds=chapter.end_seconds,
                summary=chapter.summary,
                evidence=chapter.evidence,
                transcript_text=chapter.transcript_text,
            )
        )
    return tuple(titled)


def choose_timeline_entry(statement: str, entries: Sequence[TimelineEntry]) -> TimelineEntry | None:
    if not entries:
        return None
    return max(entries, key=lambda entry: similarity(statement, entry.text))


def claim_candidates(*values: object) -> list[str]:
    candidates: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            source = [complete_statement(item) for item in value]
        else:
            source = sentence_units(value)
        for candidate in source:
            if (
                not candidate
                or ELLIPSIS_RE.search(candidate)
                or not 12 <= len(candidate) <= 220
                or re.match(r"^(?:它|这|这些|那个|相关|我们上一个|但我们的|如果|因为)", candidate)
            ):
                continue
            if any(similarity(candidate, existing) >= 0.78 for existing in candidates):
                continue
            candidates.append(candidate)
    return candidates


def build_claims(candidates: Sequence[str], entries: Sequence[TimelineEntry], chapters: Sequence[Chapter]) -> tuple[Claim, ...]:
    selected = list(candidates[:MAX_CLAIMS])
    for chapter in chapters:
        if len(selected) >= MIN_CLAIMS:
            break
        statement = complete_statement(chapter.title)
        if statement and not any(similarity(statement, item) >= 0.78 for item in selected):
            selected.append(statement)
    claims: list[Claim] = []
    for statement in selected[:MAX_CLAIMS]:
        entry = choose_timeline_entry(statement, entries)
        if entry:
            evidence = bounded_complete_text(entry.text, 60, 280)
            claims.append(Claim(statement, entry.start_seconds, entry.end_seconds, evidence))
        else:
            claims.append(Claim(statement, 0, 0, statement))
    return tuple(claims)


def build_conclusion(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        items = value if isinstance(value, (list, tuple)) else (value,)
        parts.extend(remove_markdown(item) for item in items if remove_markdown(item))
    combined = " ".join(parts)
    return bounded_complete_text(combined, 100, 200)


def build_reading_version(
    *,
    metadata: Mapping[str, str],
    body: str,
    item_summary: str = "",
    item_excerpt: str = "",
) -> ReadingVersion | None:
    transcript = transcript_text(body)
    initial_timeline = raw_timeline(body)
    duration = duration_seconds(metadata, body, initial_timeline, transcript)
    timeline = raw_timeline(body, duration)
    if not transcript:
        return None
    if transcript and len(timeline) < MIN_CHAPTERS:
        timeline = synthetic_timeline(transcript, duration)

    key_points = source_key_points(body)
    context = " ".join([source_summary(body), item_summary, *key_points])
    chapters = chapters_from_timeline(timeline, duration, context)
    chapter_points = claim_candidates([complete_statement(chapter.title) for chapter in chapters])
    supplemental = claim_candidates(key_points)
    supplemental = [
        point
        for point in supplemental
        if not any(similarity(point, chapter_point) >= 0.78 for chapter_point in chapter_points)
    ]
    remaining = max(0, MAX_CLAIMS - len(chapter_points))
    if remaining and len(supplemental) > remaining:
        indexes = sorted({round(index * (len(supplemental) - 1) / (remaining - 1)) for index in range(remaining)}) if remaining > 1 else [len(supplemental) // 2]
        supplemental = [supplemental[index] for index in indexes]
    candidates = claim_candidates(chapter_points, supplemental[:remaining])
    if len(candidates) < MIN_CLAIMS:
        candidates = claim_candidates(candidates, item_summary, source_summary(body), [chapter.title for chapter in chapters])
    if len(candidates) < MIN_CLAIMS:
        candidates = claim_candidates(candidates, item_excerpt)
    claims = build_claims(candidates, timeline, chapters)
    chapters = retitle_chapters(chapters, claims)
    conclusion = build_conclusion(source_summary(body), item_summary, [claim.statement for claim in claims])
    language = normalize_text(metadata.get("转写语言") or metadata.get("语言") or "")
    mode = "inline" if transcript and duration and duration <= SHORT_TRANSCRIPT_SECONDS else "separate" if transcript else "none"
    return ReadingVersion(
        conclusion=conclusion,
        claims=claims,
        chapters=chapters,
        transcript=transcript,
        duration_seconds=duration,
        transcript_mode=mode,
        source_language=language,
    )


def transcript_markdown(title: str, reading: ReadingVersion) -> str:
    lines = [f"# 完整转写｜{title}", "", "本页保留完整转写证据，并按语义章节整理。主阅读页只展示结论、观点和必要证据。", ""]
    for chapter in reading.chapters:
        lines.extend(
            [
                f"## {format_timecode(chapter.start_seconds)}-{format_timecode(chapter.end_seconds)}｜{chapter.title}",
                "",
            ]
        )
        paragraphs = sentence_units(chapter.transcript_text)
        for index in range(0, len(paragraphs), 4):
            lines.extend(["".join(paragraphs[index : index + 4]), ""])
    if not reading.chapters and reading.transcript:
        paragraphs = sentence_units(reading.transcript)
        for index in range(0, len(paragraphs), 4):
            lines.extend(["".join(paragraphs[index : index + 4]), ""])
    if reading.transcript:
        lines.extend(["## 完整逐字稿", "", "以下内容保留全部转写文本，便于搜索和逐句核验。", ""])
        paragraphs = sentence_units(reading.transcript)
        for index in range(0, len(paragraphs), 4):
            lines.extend(["".join(paragraphs[index : index + 4]), ""])
    return "\n".join(lines).rstrip() + "\n"
