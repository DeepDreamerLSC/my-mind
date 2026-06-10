#!/usr/bin/env python3
"""Capture link metadata into the my-mind inbox."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from html import unescape as html_unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_INBOX = ROOT / "00_收件箱"
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
DOUYIN_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
XHS_DESKTOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
TRANSCRIPT_SUMMARY_SENTENCE_COUNT = 12
TRANSCRIPT_KEY_POINT_COUNT = 12
TRANSCRIPT_TIMELINE_INTERVAL_SECONDS = 300


def now_date() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d")


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def yaml_scalar(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if any(ch in text for ch in [":", "#", "[", "]", "{", "}", ",", "\"", "'"]) or text.startswith(("-", "@", "`")):
        return json.dumps(text, ensure_ascii=False)
    return text


def yaml_list(values: list[Any]) -> list[str]:
    cleaned = [str(value).strip() for value in values if value not in (None, "")]
    if not cleaned:
        return ["[]"]
    return [f"  - {yaml_scalar(value)}" for value in cleaned]


def sanitize_filename_part(text: str, fallback: str = "未命名") -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .")
    if not text:
        text = fallback
    return text[:80].rstrip()


def guess_platform(url: str, info: dict[str, Any] | None = None) -> str:
    extractor = (info or {}).get("extractor_key") or (info or {}).get("extractor") or ""
    extractor_l = str(extractor).lower()
    host = urlparse(url).netloc.lower()
    if "youtube" in extractor_l or "youtu.be" in host or "youtube.com" in host:
        return "YouTube"
    if extractor_l in {"twitter", "x"} or "twitter.com" in host or "x.com" in host:
        return "X"
    if "douyin" in extractor_l or "douyin.com" in host or "iesdouyin.com" in host:
        return "抖音"
    if "xiaohongshu" in extractor_l or "xiaohongshu.com" in host or "xhslink.com" in host:
        return "小红书"
    if "bilibili" in extractor_l or "bilibili.com" in host:
        return "B站"
    if "tiktok" in extractor_l or "tiktok.com" in host:
        return "TikTok"
    if host:
        return host.removeprefix("www.")
    return "链接"


def run_yt_dlp(url: str) -> tuple[dict[str, Any] | None, str | None]:
    if not shutil.which("yt-dlp"):
        return None, "未找到 yt-dlp"

    command = [
        "yt-dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-playlist",
        "--no-warnings",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "yt-dlp 解析超时"

    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or f"yt-dlp 返回码 {result.returncode}"
        return None, reason[-1000:]

    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"yt-dlp 输出不是有效 JSON：{exc}"


def youtube_oembed(url: str) -> tuple[dict[str, Any] | None, str | None]:
    endpoint = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    try:
        with urllib.request.urlopen(endpoint, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - keep fallback failure visible in the inbox note.
        return None, f"YouTube oEmbed 备用解析失败：{exc!r}"

    title = data.get("title") or ""
    author = data.get("author_name") or ""
    author_url = data.get("author_url") or ""
    thumbnail = data.get("thumbnail_url") or ""
    if not title and not author and not thumbnail:
        return None, "YouTube oEmbed 未返回可用基础信息"

    return {
        "title": title,
        "uploader": author,
        "author_url": author_url,
        "thumbnail": thumbnail,
        "webpage_url": url,
        "original_url": url,
        "extractor_key": "YouTube oEmbed",
        "description": "",
        "tags": [],
        "categories": [],
    }, None


def format_ms(ms: Any) -> str:
    try:
        total = int(float(ms)) // 1000
    except (TypeError, ValueError):
        return ""
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def fetch_text(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or DEFAULT_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore")


def fetch_html(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[str, str]:
    request = urllib.request.Request(url, headers=headers or DEFAULT_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore"), response.geturl()


def strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(p|div|li|h[1-6])>", "\n", value, flags=re.I)
    value = re.sub(r"<.*?>", " ", value, flags=re.S)
    value = html_unescape(urllib.parse.unquote(value))
    value = value.replace("\\u0026", "&")
    value = value.replace("\\/", "/")
    return " ".join(value.split())


def clean_url(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = html_unescape(str(value)).replace("\\u002F", "/").replace("\\/", "/").strip()
    if text.startswith("//"):
        return "https:" + text
    return text


def parse_html_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r"([:\w-]+)\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s\"'>/]+))", tag):
        value = next(group for group in match.groups()[2:] if group is not None)
        attrs[match.group(1).lower()] = html_unescape(value)
    return attrs


def html_metadata(html: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.S | re.I)
    if title_match:
        metadata["title"] = strip_html(title_match.group(1))

    for match in re.finditer(r"<meta\b[^>]*>", html, flags=re.I):
        attrs = parse_html_attrs(match.group(0))
        key = (attrs.get("name") or attrs.get("property") or attrs.get("itemprop") or "").lower()
        content = attrs.get("content")
        if key and content and key not in metadata:
            metadata[key] = html_unescape(content).replace("\\u002F", "/").replace("\\/", "/").strip()

    for match in re.finditer(r"<link\b[^>]*>", html, flags=re.I):
        attrs = parse_html_attrs(match.group(0))
        rel = (attrs.get("rel") or "").lower()
        href = attrs.get("href")
        if href and "canonical" in rel:
            metadata["canonical"] = clean_url(href)
    return metadata


def first_html_image(html: str) -> str:
    for match in re.finditer(r"<img\b[^>]*>", html, flags=re.I):
        attrs = parse_html_attrs(match.group(0))
        src = attrs.get("src") or attrs.get("data-src")
        if src:
            return clean_url(src)
    return ""


def script_assignment_json(html: str, assignment: str) -> dict[str, Any] | None:
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, flags=re.S | re.I):
        script = match.group(1)
        index = script.find(assignment)
        if index < 0:
            continue
        payload = script[index + len(assignment) :].strip()
        if payload.startswith("="):
            payload = payload[1:].strip()
        payload = payload.rstrip("; \n\t")
        payload = re.sub(r"(?<=[:\[,])undefined(?=[,\}\]])", "null", payload)
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            continue
    return None


def first_item_from_key(obj: Any, key: str) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
        for child in obj.values():
            found = first_item_from_key(child, key)
            if found:
                return found
    elif isinstance(obj, list):
        for child in obj:
            found = first_item_from_key(child, key)
            if found:
                return found
    return None


def first_url_from_media(value: Any) -> str:
    if isinstance(value, dict):
        url = value.get("url")
        if url:
            return clean_url(url)
        url_list = value.get("url_list")
        if isinstance(url_list, list):
            for item in url_list:
                url = clean_url(item)
                if url:
                    return url
        info_list = value.get("infoList")
        if isinstance(info_list, list):
            for item in info_list:
                url = first_url_from_media(item)
                if url:
                    return url
        for nested_key in ("cover", "origin_cover", "dynamic_cover", "avatar_thumb", "avatar_medium"):
            url = first_url_from_media(value.get(nested_key))
            if url:
                return url
    elif isinstance(value, list):
        for item in value:
            url = first_url_from_media(item)
            if url:
                return url
    return ""


def timestamp_upload_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return ""
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return dt.datetime.fromtimestamp(timestamp, TZ).strftime("%Y%m%d")


def clean_social_title(value: str, suffix: str) -> str:
    title = strip_html(value)
    if suffix and title.endswith(suffix):
        title = title[: -len(suffix)].strip()
    return title


def extract_hashtags(text: str) -> list[str]:
    tags: list[str] = []
    for match in re.finditer(r"#([^#\s，,。；;：:!！?？]+)", text or ""):
        tag = match.group(1).replace("[话题]", "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def normalize_transcript_text(text: str) -> str:
    text = html_unescape(text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_cjk_text(text: str) -> str:
    text = html_unescape(text).replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    text = normalize_cjk_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    sentences: list[str] = []
    for part in parts:
        sentence = part.strip(" \t-•，,。")
        if len(sentence) >= 8:
            sentences.append(sentence)
    if len(sentences) <= 1:
        sentences = [chunk.strip() for chunk in re.split(r"(?<=\w[.!?])\s+", text) if len(chunk.strip()) >= 8]
    return sentences


def transcript_word_count(text: str) -> int:
    if re.search(r"[\u4e00-\u9fff]", text):
        return len(re.findall(r"[\u4e00-\u9fff]", text)) + len(re.findall(r"[A-Za-z0-9]+", text))
    return len(text.split())


def score_sentence(sentence: str, index: int) -> float:
    keywords = [
        "codex",
        "agent",
        "prompt",
        "workflow",
        "git",
        "github",
        "openai",
        "环境",
        "安装",
        "配置",
        "命令",
        "项目",
        "仓库",
        "代码",
        "提示词",
        "模型",
        "上下文",
        "任务",
        "工作流",
        "阶段",
        "验证",
        "自动化",
        "实践",
        "问题",
        "解决",
        "建议",
        "总结",
    ]
    lower = sentence.lower()
    score = 0.0
    for keyword in keywords:
        if keyword in lower:
            score += 2.0
    score += min(len(sentence), 120) / 60
    if index < 8:
        score += 1.2
    elif index < 20:
        score += 0.5
    return score


def summarize_transcript_text(text: str) -> dict[str, Any]:
    normalized = normalize_cjk_text(text)
    sentences = split_sentences(normalized)
    if not sentences:
        return {
            "summary": "",
            "key_points": [],
            "excerpt": excerpt(normalized, limit=1600, notice="（转写摘录过长，第一版仅保留前 1600 字。）"),
            "word_count": transcript_word_count(normalized),
        }
    summary_sentences = sentences[:TRANSCRIPT_SUMMARY_SENTENCE_COUNT]
    scored = sorted(
        ((score_sentence(sentence, index), index, sentence) for index, sentence in enumerate(sentences)),
        reverse=True,
    )
    selected_indices = sorted({index for _, index, _ in scored[:TRANSCRIPT_KEY_POINT_COUNT]})
    key_points = [sentences[index] for index in selected_indices]
    if not key_points:
        key_points = summary_sentences[:6]
    return {
        "summary": " ".join(summary_sentences),
        "key_points": key_points,
        "excerpt": excerpt(normalized, limit=1600, notice="（转写摘录过长，第一版仅保留前 1600 字。）"),
        "word_count": transcript_word_count(normalized),
    }


def parse_time_to_seconds(value: str) -> int:
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(float(parts[1]))
    except (TypeError, ValueError):
        return 0
    return 0


def build_transcript_timeline(segments: list[dict[str, str]], interval_seconds: int = TRANSCRIPT_TIMELINE_INTERVAL_SECONDS) -> list[dict[str, str]]:
    if not segments:
        return []
    buckets: dict[int, list[str]] = {}
    for segment in segments:
        seconds = parse_time_to_seconds(segment.get("time", ""))
        bucket = (seconds // interval_seconds) * interval_seconds
        text = normalize_cjk_text(segment.get("text", ""))
        if len(text) >= 8:
            buckets.setdefault(bucket, []).append(text)
    timeline: list[dict[str, str]] = []
    for bucket in sorted(buckets):
        joined = " ".join(buckets[bucket])
        timeline.append({"time": fmt_duration(bucket), "text": excerpt(joined, limit=180, notice="").replace("\n", " ")})
    return timeline


def parse_whisper_json(path: Path) -> tuple[str, list[dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments: list[dict[str, str]] = []
    if isinstance(data.get("segments"), list):
        for item in data["segments"]:
            if not isinstance(item, dict):
                continue
            text = normalize_cjk_text(str(item.get("text") or ""))
            if text:
                segments.append({"time": fmt_duration(item.get("start")), "text": text})
    text = normalize_cjk_text(str(data.get("text") or "\n".join(segment["text"] for segment in segments)))
    return text, segments


def parse_segment_text_file(path: Path) -> tuple[str, list[dict[str, str]]]:
    payload = path.read_text(encoding="utf-8", errors="ignore")
    segments: list[dict[str, str]] = []
    for line in payload.splitlines():
        match = re.match(r"\[?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\]?\s*(.+)", line.strip())
        if match:
            segments.append({"time": match.group(1).split(".", 1)[0], "text": normalize_cjk_text(match.group(2))})
    text = normalize_cjk_text("\n".join(segment["text"] for segment in segments) if segments else payload)
    return text, segments


def text_from_youtube_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "simpleText" in value:
            return str(value["simpleText"])
        if isinstance(value.get("runs"), list):
            return "".join(str(run.get("text", "")) for run in value["runs"])
        if "content" in value:
            return str(value["content"])
    return ""


def walk_values(obj: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(obj, dict):
        for current_key, value in obj.items():
            if current_key == key:
                found.append(value)
            found.extend(walk_values(value, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(walk_values(item, key))
    return found


def extract_json_assignment(html: str, name: str) -> dict[str, Any] | None:
    pattern = rf"{re.escape(name)}\s*=\s*({{.+?}});</script>"
    match = re.search(pattern, html, flags=re.S)
    if not match:
        pattern = rf"{re.escape(name)}\s*=\s*({{.+?}});"
        match = re.search(pattern, html, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def ytcfg_from_html(html: str) -> dict[str, Any] | None:
    match = re.search(r"ytcfg\.set\(({.+?})\);", html, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def youtube_description_urls(text: str) -> list[str]:
    text = text.replace("\\u0026", "&").replace("\\/", "/")
    text = urllib.parse.unquote(text)
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s<>\"]+", text):
        urls.append(match.group(0).rstrip(").,"))
    for match in re.finditer(r"q=(https%3A%2F%2F[^&\"\\]+)", text):
        urls.append(urllib.parse.unquote(match.group(1)))
    unique: list[str] = []
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.endswith("youtube.com") and parsed.path == "/redirect":
            query_url = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            if query_url:
                url = query_url
        if url not in unique:
            unique.append(url)
    return unique


def find_transcript_urls(text: str) -> list[str]:
    return [url for url in youtube_description_urls(text) if "transcript" in url.lower()]


def extract_chapter_list_from_html(entry_html: str) -> list[dict[str, str]]:
    chapters: list[dict[str, str]] = []
    for match in re.finditer(r'<li><a href="#chapter[^"]+">(.*?)</a></li>', entry_html, flags=re.S):
        text = strip_html(match.group(1))
        time_match = re.match(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*[–-]\s*(.+)", text)
        if time_match:
            chapters.append({"time": time_match.group(1), "title": time_match.group(2)})
        elif text:
            chapters.append({"time": "", "title": text})
    return chapters


def parse_lex_transcript(url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        html = fetch_text(url)
    except Exception as exc:  # noqa: BLE001
        return None, f"官方转录页读取失败：{exc!r}"

    title_match = re.search(r"<title>(.*?)</title>", html, flags=re.S | re.I)
    title = strip_html(title_match.group(1)) if title_match else ""
    published_match = re.search(r'property="article:published_time"\s+content="([^"]+)"', html)
    published = published_match.group(1) if published_match else ""
    start = html.find('<div class="entry-content">')
    end = html.find("</article>", start)
    entry = html[start:end] if start >= 0 and end > start else html
    chapters = extract_chapter_list_from_html(entry)
    segments = re.findall(r'<div class="ts-segment">(.*?)</div>', entry, flags=re.S)
    transcript_texts: list[str] = []
    for segment in segments:
        text_match = re.search(r'<span class="ts-text">(.*?)</span>', segment, flags=re.S)
        if text_match:
            transcript_texts.append(strip_html(text_match.group(1)))
    word_count = sum(len(text.split()) for text in transcript_texts)
    return {
        "source": "Lex Fridman 官方 transcript",
        "url": url if url.endswith("/") else url + "/",
        "title": title,
        "published": published,
        "chapters": chapters,
        "segment_count": len(transcript_texts),
        "word_count": word_count,
    }, None


def choose_subtitle_track(info: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    language_preferences = ["zh-Hans", "zh-CN", "zh", "zh-TW", "en", "en-US"]
    source_order = [("手动字幕", info.get("subtitles") or {}), ("自动字幕", info.get("automatic_captions") or {})]
    ext_preferences = ["json3", "srv3", "ttml", "vtt"]
    for source_name, subtitles in source_order:
        if not isinstance(subtitles, dict):
            continue
        languages = list(subtitles.keys())
        ordered_languages = [lang for lang in language_preferences if lang in subtitles]
        ordered_languages += [lang for lang in languages if lang not in ordered_languages]
        for language in ordered_languages:
            tracks = subtitles.get(language) or []
            if not isinstance(tracks, list):
                continue
            for ext in ext_preferences:
                for track in tracks:
                    if isinstance(track, dict) and track.get("url") and track.get("ext") == ext:
                        return source_name, language, track
    return None


def parse_json3_subtitle(payload: str) -> list[dict[str, str]]:
    data = json.loads(payload)
    segments: list[dict[str, str]] = []
    for event in data.get("events", []):
        pieces = event.get("segs") or []
        text = "".join(str(piece.get("utf8", "")) for piece in pieces if isinstance(piece, dict))
        text = normalize_transcript_text(text)
        if not text:
            continue
        segments.append(
            {
                "time": format_ms(event.get("tStartMs")),
                "text": text,
            }
        )
    return segments


def parse_vtt_subtitle(payload: str) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    blocks = re.split(r"\n\s*\n", payload.replace("\r", "\n"))
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0].upper().startswith("WEBVTT"):
            continue
        time = ""
        text_lines: list[str] = []
        for line in lines:
            if "-->" in line:
                time = line.split("-->", 1)[0].strip().split(".", 1)[0]
            elif not re.fullmatch(r"\d+", line):
                text_lines.append(re.sub(r"<[^>]+>", "", line))
        text = normalize_transcript_text("\n".join(text_lines))
        if text:
            segments.append({"time": time, "text": text})
    return segments


def parse_xml_subtitle(payload: str) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    for match in re.finditer(r"<text\b([^>]*)>(.*?)</text>", payload, flags=re.S):
        attrs = match.group(1)
        start_match = re.search(r'start="([^"]+)"', attrs)
        text = strip_html(match.group(2))
        if text:
            segments.append({"time": fmt_duration(start_match.group(1)) if start_match else "", "text": text})
    if segments:
        return segments
    text = strip_html(payload)
    return [{"time": "", "text": text}] if text else []


def summarize_subtitle_segments(
    *,
    source: str,
    language: str,
    ext: str,
    url: str,
    segments: list[dict[str, str]],
) -> dict[str, Any]:
    plain_text = normalize_transcript_text("\n".join(segment["text"] for segment in segments))
    return {
        "source": source,
        "language": language,
        "format": ext,
        "url": url,
        "segment_count": len(segments),
        "word_count": len(plain_text.split()),
    }


def fetch_subtitle_track(track: dict[str, Any]) -> tuple[list[dict[str, str]], str | None]:
    url = str(track.get("url") or "")
    ext = str(track.get("ext") or "")
    if not url:
        return [], "字幕轨道缺少 URL"
    try:
        payload = fetch_text(url, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return [], f"字幕 URL 读取失败：{exc!r}"
    try:
        if ext == "json3":
            return parse_json3_subtitle(payload), None
        if ext == "vtt":
            return parse_vtt_subtitle(payload), None
        if ext in {"srv3", "ttml", "xml"}:
            return parse_xml_subtitle(payload), None
        return parse_vtt_subtitle(payload) or parse_xml_subtitle(payload), None
    except Exception as exc:  # noqa: BLE001
        return [], f"字幕解析失败：{exc!r}"


def attach_yt_dlp_subtitle(info: dict[str, Any]) -> None:
    if info.get("youtube_subtitle"):
        return
    chosen = choose_subtitle_track(info)
    if not chosen:
        return
    source_name, language, track = chosen
    segments, error = fetch_subtitle_track(track)
    if error:
        info["youtube_subtitle_error"] = error
        return
    if segments:
        info["youtube_subtitle"] = summarize_subtitle_segments(
            source=source_name,
            language=language,
            ext=str(track.get("ext") or ""),
            url=str(track.get("url") or ""),
            segments=segments,
        )


def extract_innertube_transcript_params(html: str) -> str:
    match = re.search(r'"getTranscriptEndpoint"\s*:\s*\{"params":"([^"]+)"', html)
    return match.group(1) if match else ""


def parse_innertube_transcript_segments(data: dict[str, Any]) -> list[dict[str, str]]:
    renderers = walk_values(data, "transcriptSegmentRenderer")
    segments: list[dict[str, str]] = []
    for renderer in renderers:
        if not isinstance(renderer, dict):
            continue
        text = text_from_youtube_value(renderer.get("snippet"))
        start_ms = renderer.get("startMs")
        if not start_ms:
            start_ms = renderer.get("startTimeMs")
        text = normalize_transcript_text(text)
        if text:
            segments.append({"time": format_ms(start_ms), "text": text})
    return segments


def fetch_innertube_transcript(html: str, watch_url: str) -> tuple[dict[str, Any] | None, str | None]:
    cfg = ytcfg_from_html(html)
    params = extract_innertube_transcript_params(html)
    if not cfg or not params:
        return None, "YouTube 页面未暴露 transcript endpoint 参数"
    api_key = cfg.get("INNERTUBE_API_KEY")
    context = cfg.get("INNERTUBE_CONTEXT")
    if not api_key or not context:
        return None, "YouTube 页面缺少 Innertube API key 或 context"
    body = json.dumps({"context": context, "params": params}).encode("utf-8")
    endpoint = f"https://www.youtube.com/youtubei/v1/get_transcript?key={api_key}"
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://www.youtube.com",
            "Referer": watch_url,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        return None, f"YouTube 内置 transcript 接口失败：{exc!r}"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return None, f"YouTube 内置 transcript 返回非 JSON：{exc}"
    segments = parse_innertube_transcript_segments(data)
    if not segments:
        return None, "YouTube 内置 transcript 未返回可解析片段"
    return summarize_subtitle_segments(
        source="YouTube 内置 transcript",
        language="unknown",
        ext="innertube",
        url=watch_url,
        segments=segments,
    ), None


def youtube_page_info(url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        html = fetch_text(url)
    except Exception as exc:  # noqa: BLE001
        return None, f"YouTube 页面读取失败：{exc!r}"

    info: dict[str, Any] = {}
    initial_data = extract_json_assignment(html, "ytInitialData")
    if initial_data:
        descriptions = [text_from_youtube_value(value) for value in walk_values(initial_data, "attributedDescriptionBodyText")]
        descriptions += [text_from_youtube_value(value) for value in walk_values(initial_data, "attributedDescription")]
        descriptions = [desc for desc in descriptions if desc]
        if descriptions:
            info["description"] = max(descriptions, key=len)
        title_candidates = [text_from_youtube_value(value) for value in walk_values(initial_data, "title")]
        title_candidates = [title for title in title_candidates if title and len(title) > 20 and "Like this video" not in title]
        if title_candidates:
            info["title"] = title_candidates[0]

    innertube_transcript, innertube_error = fetch_innertube_transcript(html, url)
    if innertube_transcript:
        info["youtube_subtitle"] = innertube_transcript
    elif innertube_error:
        info["youtube_subtitle_error"] = innertube_error

    all_text = html
    if info.get("description"):
        all_text += "\n" + str(info["description"])
    transcript_urls = find_transcript_urls(all_text)
    if transcript_urls:
        info["external_transcript_url"] = transcript_urls[0]
        if "lexfridman.com" in transcript_urls[0]:
            transcript, transcript_error = parse_lex_transcript(transcript_urls[0])
            if transcript:
                info["external_transcript"] = transcript
            elif transcript_error:
                info["external_transcript_error"] = transcript_error

    return info or None, None


def douyin_page_info(url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        html, final_url = fetch_html(url, headers=DOUYIN_MOBILE_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return None, f"抖音公开页读取失败：{exc!r}"

    metadata = html_metadata(html)
    router_data = script_assignment_json(html, "window._ROUTER_DATA")
    item = first_item_from_key(router_data, "item_list") if router_data else None
    description = str(metadata.get("description") or "")
    title = clean_social_title(metadata.get("title") or "", " - 抖音")
    canonical = clean_url(metadata.get("canonical")) or final_url
    thumbnail = first_html_image(html)
    author = ""
    upload_date = ""
    like_count = ""
    comment_count = ""
    share_count = ""
    collect_count = ""
    duration = ""
    content_id = ""
    author_id = ""
    author_url = ""
    media_url = ""
    tags: list[str] = []

    if isinstance(item, dict):
        content_id = str(item.get("aweme_id") or item.get("group_id_str") or "")
        title = str(item.get("desc") or title).strip()
        description = title or description
        upload_date = timestamp_upload_date(item.get("create_time"))
        author_data = item.get("author") if isinstance(item.get("author"), dict) else {}
        author = str(author_data.get("nickname") or "")
        author_id = str(author_data.get("short_id") or author_data.get("unique_id") or author_data.get("sec_uid") or "")
        if author_data.get("sec_uid"):
            author_url = f"https://www.douyin.com/user/{author_data.get('sec_uid')}"
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        like_count = str(statistics.get("digg_count") or "")
        comment_count = str(statistics.get("comment_count") or "")
        share_count = str(statistics.get("share_count") or "")
        collect_count = str(statistics.get("collect_count") or "")
        video_data = item.get("video") if isinstance(item.get("video"), dict) else {}
        media_url = first_url_from_media(video_data.get("play_addr"))
        thumbnail = first_url_from_media(video_data.get("cover")) or first_url_from_media(item.get("images")) or thumbnail
        raw_duration = video_data.get("duration")
        if raw_duration not in (None, ""):
            try:
                duration_value = float(raw_duration)
                duration = str(duration_value / 1000 if duration_value > 10_000 else duration_value)
            except (TypeError, ValueError):
                duration = str(raw_duration)
        cha_list = item.get("cha_list") if isinstance(item.get("cha_list"), list) else []
        for cha in cha_list:
            if isinstance(cha, dict) and cha.get("cha_name"):
                tags.append(str(cha["cha_name"]))

    if not author and description:
        author_match = re.search(r"\s-\s(.+?)于(\d{8})发布在抖音", description)
        if author_match:
            author = author_match.group(1).strip()
            upload_date = upload_date or author_match.group(2)
    if not like_count and description:
        like_match = re.search(r"收获了(\d+)个喜欢", description)
        if like_match:
            like_count = like_match.group(1)

    tags = tags + [tag for tag in extract_hashtags(description or title) if tag not in tags]
    if not title and description:
        title = description.split(" - ", 1)[0].strip()
    if not content_id:
        id_match = re.search(r"/(?:share/)?video/(\d+)", canonical or final_url)
        content_id = id_match.group(1) if id_match else ""

    if not any([title, description, author, thumbnail, content_id]):
        return None, "抖音公开页未暴露可用基础信息"

    return {
        "id": content_id,
        "title": title or "抖音作品",
        "description": description,
        "uploader": author,
        "uploader_id": author_id,
        "uploader_url": author_url,
        "upload_date": upload_date,
        "duration": duration,
        "thumbnail": thumbnail,
        "media_url": media_url,
        "webpage_url": canonical,
        "original_url": url,
        "extractor_key": "Douyin HTML",
        "parse_tool": "公开页面 HTML",
        "tags": tags,
        "categories": [],
        "like_count": like_count,
        "comment_count": comment_count,
        "share_count": share_count,
        "collect_count": collect_count,
        "platform_meta": {
            "真实链接": final_url,
            "作品编号": content_id,
            "作者编号": author_id,
            "点赞数": like_count,
            "评论数": comment_count,
            "收藏数": collect_count,
            "分享数": share_count,
            "封面图": thumbnail,
        },
    }, None


def xiaohongshu_note_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    note_map = (((state.get("note") or {}).get("noteDetailMap") or {}) if isinstance(state, dict) else {})
    if isinstance(note_map, dict):
        for entry in note_map.values():
            if isinstance(entry, dict) and isinstance(entry.get("note"), dict):
                return entry["note"]
    return None


def xiaohongshu_image_url(note: dict[str, Any], metadata: dict[str, str]) -> str:
    image_list = note.get("imageList") if isinstance(note.get("imageList"), list) else []
    for image in image_list:
        url = first_url_from_media(image)
        if url:
            return url
    return clean_url(metadata.get("og:image") or "")


def xiaohongshu_tags(note: dict[str, Any], description: str) -> list[str]:
    tags: list[str] = []
    tag_list = note.get("tagList") if isinstance(note.get("tagList"), list) else []
    for item in tag_list:
        if isinstance(item, dict):
            tag = str(item.get("name") or item.get("title") or "").strip()
        else:
            tag = str(item).strip()
        if tag and tag not in tags:
            tags.append(tag)
    tags.extend(tag for tag in extract_hashtags(description) if tag not in tags)
    return tags


def xiaohongshu_page_info(url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        html, final_url = fetch_html(url, headers=XHS_DESKTOP_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return None, f"小红书公开页读取失败：{exc!r}"

    metadata = html_metadata(html)
    state = script_assignment_json(html, "window.__INITIAL_STATE__")
    note = xiaohongshu_note_from_state(state or {}) or {}
    interact = note.get("interactInfo") if isinstance(note.get("interactInfo"), dict) else {}
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    description = str(note.get("desc") or metadata.get("description") or "")
    title = str(note.get("title") or clean_social_title(metadata.get("og:title") or metadata.get("title") or "", " - 小红书"))
    note_id = str(note.get("noteId") or "")
    if not note_id:
        id_match = re.search(r"/(?:explore|discovery/item)/([0-9a-fA-F]+)", metadata.get("og:url") or final_url)
        note_id = id_match.group(1) if id_match else ""
    canonical = clean_url(metadata.get("og:url") or final_url)
    like_count = str(interact.get("likedCount") or metadata.get("og:xhs:note_like") or "")
    comment_count = str(interact.get("commentCount") or metadata.get("og:xhs:note_comment") or "")
    collect_count = str(interact.get("collectedCount") or metadata.get("og:xhs:note_collect") or "")
    share_count = str(interact.get("shareCount") or "")
    author = str(user.get("nickname") or "")
    author_id = str(user.get("userId") or "")
    author_url = f"https://www.xiaohongshu.com/user/profile/{author_id}" if author_id else ""
    thumbnail = xiaohongshu_image_url(note, metadata)
    upload_date = timestamp_upload_date(note.get("time"))
    note_type = str(note.get("type") or "")
    image_count = len(note.get("imageList") or []) if isinstance(note.get("imageList"), list) else ""
    tags = xiaohongshu_tags(note, description)

    if not any([title, description, author, thumbnail, note_id]):
        return None, "小红书公开页未暴露可用基础信息"

    return {
        "id": note_id,
        "title": title or "小红书笔记",
        "description": description,
        "uploader": author,
        "uploader_id": author_id,
        "uploader_url": author_url,
        "upload_date": upload_date,
        "thumbnail": thumbnail,
        "webpage_url": canonical,
        "original_url": url,
        "extractor_key": "XiaoHongShu HTML",
        "parse_tool": "公开页面 HTML",
        "tags": tags,
        "categories": [],
        "like_count": like_count,
        "comment_count": comment_count,
        "share_count": share_count,
        "collect_count": collect_count,
        "platform_meta": {
            "真实链接": final_url,
            "笔记编号": note_id,
            "作者编号": author_id,
            "笔记类型": note_type,
            "图片数": image_count,
            "点赞数": like_count,
            "评论数": comment_count,
            "收藏数": collect_count,
            "分享数": share_count,
            "封面图": thumbnail,
        },
    }, None


def media_duration_seconds(info: dict[str, Any]) -> int:
    try:
        return int(float(info.get("duration") or 0))
    except (TypeError, ValueError):
        return 0


def extract_audio(media_url: str, output_dir: Path) -> tuple[Path | None, str | None]:
    if not shutil.which("ffmpeg"):
        return None, "未找到 ffmpeg，无法从视频抽取音频"
    audio_path = output_dir / "media-audio.mp3"
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-user_agent",
        DOUYIN_MOBILE_HEADERS["User-Agent"],
        "-headers",
        "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8\r\n",
        "-i",
        media_url,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "48k",
        str(audio_path),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1800,
        check=False,
    )
    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        reason = result.stderr.strip() or result.stdout.strip() or f"ffmpeg 返回码 {result.returncode}"
        return None, f"音频抽取失败：{reason[-1200:]}"
    return audio_path, None


def transcribe_with_whisper_cli(
    audio_path: Path,
    work_dir: Path,
    *,
    model: str,
    language: str,
) -> tuple[str, list[dict[str, str]], dict[str, Any], str | None]:
    whisper = shutil.which("whisper")
    if not whisper:
        return "", [], {}, "未找到 whisper CLI"
    command = [
        whisper,
        str(audio_path),
        "--model",
        model,
        "--output_dir",
        str(work_dir),
        "--output_format",
        "json",
        "--fp16",
        "False",
    ]
    if language and language != "auto":
        command.extend(["--language", language])
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=7200,
        check=False,
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or f"whisper 返回码 {result.returncode}"
        return "", [], {}, f"whisper CLI 转写失败：{reason[-1200:]}"
    json_path = work_dir / f"{audio_path.stem}.json"
    if not json_path.exists():
        candidates = sorted(work_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        json_path = candidates[0] if candidates else json_path
    if not json_path.exists():
        return "", [], {}, "whisper CLI 未生成 JSON 转写结果"
    text, segments = parse_whisper_json(json_path)
    return text, segments, {"backend": "whisper-cli", "model": model, "language": language}, None


def transcribe_with_faster_whisper(
    audio_path: Path,
    *,
    model: str,
    language: str,
) -> tuple[str, list[dict[str, str]], dict[str, Any], str | None]:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        return transcribe_with_faster_whisper_subprocess(audio_path, model=model, language=language, import_error=exc)

    try:
        whisper_model = WhisperModel(model, device="auto", compute_type="int8")
        iterator, detected = whisper_model.transcribe(
            str(audio_path),
            language=None if language == "auto" else language,
            vad_filter=True,
        )
        segments: list[dict[str, str]] = []
        for segment in iterator:
            text = normalize_cjk_text(segment.text)
            if text:
                segments.append({"time": fmt_duration(segment.start), "text": text})
        text = normalize_cjk_text("\n".join(segment["text"] for segment in segments))
        detected_language = getattr(detected, "language", "") or language
        return text, segments, {"backend": "faster-whisper", "model": model, "language": detected_language}, None
    except Exception as exc:  # noqa: BLE001
        return "", [], {}, f"faster-whisper 转写失败：{exc!r}"


def candidate_faster_whisper_pythons() -> list[str]:
    candidates = [
        str(Path.home() / ".cache" / "my-mind" / "faster-whisper-venv" / "bin" / "python"),
        str(Path.home() / ".local" / "pipx" / "venvs" / "faster-whisper" / "bin" / "python"),
        shutil.which("python3.12") or "",
    ]
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique and Path(candidate).exists():
            unique.append(candidate)
    return unique


def transcribe_with_faster_whisper_subprocess(
    audio_path: Path,
    *,
    model: str,
    language: str,
    import_error: Exception,
) -> tuple[str, list[dict[str, str]], dict[str, Any], str | None]:
    helper = r'''
import json
import sys
from pathlib import Path
from faster_whisper import WhisperModel

audio_path, model_name, language, output_path = sys.argv[1:5]
whisper_model = WhisperModel(model_name, device="auto", compute_type="int8")
segments_iter, info = whisper_model.transcribe(
    audio_path,
    language=None if language == "auto" else language,
    vad_filter=True,
)
segments = []
for segment in segments_iter:
    text = (segment.text or "").strip()
    if text:
        segments.append({"start": segment.start, "text": text})
Path(output_path).write_text(
    json.dumps({"language": getattr(info, "language", language), "segments": segments}, ensure_ascii=False),
    encoding="utf-8",
)
'''
    attempted: list[str] = [f"当前 Python 未安装 faster-whisper：{import_error!r}"]
    with tempfile.TemporaryDirectory(prefix="my-mind-fw-") as temp_dir_name:
        output_path = Path(temp_dir_name) / "segments.json"
        for python in candidate_faster_whisper_pythons():
            probe = subprocess.run(
                [python, "-c", "import faster_whisper"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            if probe.returncode != 0:
                attempted.append(f"{python} 不可用：{(probe.stderr or probe.stdout).strip()[-300:]}")
                continue
            result = subprocess.run(
                [python, "-c", helper, str(audio_path), model, language, str(output_path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=7200,
                check=False,
            )
            if result.returncode != 0 or not output_path.exists():
                reason = result.stderr.strip() or result.stdout.strip() or f"返回码 {result.returncode}"
                attempted.append(f"{python} 转写失败：{reason[-1200:]}")
                continue
            data = json.loads(output_path.read_text(encoding="utf-8"))
            segments = [
                {"time": fmt_duration(item.get("start")), "text": normalize_cjk_text(str(item.get("text") or ""))}
                for item in data.get("segments", [])
                if isinstance(item, dict) and item.get("text")
            ]
            text = normalize_cjk_text("\n".join(segment["text"] for segment in segments))
            return text, segments, {"backend": "faster-whisper", "model": model, "language": data.get("language", language)}, None
    return "", [], {}, "未找到可用的 faster-whisper Python 环境。\n\n" + "\n\n".join(attempted)


def transcribe_with_custom_command(
    audio_path: Path,
    command_template: str,
) -> tuple[str, list[dict[str, str]], dict[str, Any], str | None]:
    if not command_template:
        return "", [], {}, "未提供自定义转写命令"
    command = command_template.format(audio=str(audio_path))
    result = subprocess.run(
        command,
        cwd=ROOT,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=7200,
        check=False,
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or f"自定义命令返回码 {result.returncode}"
        return "", [], {}, f"自定义转写命令失败：{reason[-1200:]}"
    output = normalize_cjk_text(result.stdout)
    maybe_path = Path(output)
    if maybe_path.exists() and maybe_path.is_file():
        if maybe_path.suffix == ".json":
            text, segments = parse_whisper_json(maybe_path)
        else:
            text, segments = parse_segment_text_file(maybe_path)
    else:
        text, segments = parse_segment_text_file(audio_path.with_suffix(".txt")) if audio_path.with_suffix(".txt").exists() else (output, [])
    return text, segments, {"backend": "custom-command", "model": command_template, "language": ""}, None


def transcribe_audio(
    audio_path: Path,
    work_dir: Path,
    *,
    backend: str,
    model: str,
    language: str,
    command_template: str,
) -> tuple[str, list[dict[str, str]], dict[str, Any], str | None]:
    errors: list[str] = []
    candidates = [backend] if backend != "auto" else ["whisper-cli", "faster-whisper"]
    if command_template and backend in {"auto", "custom"}:
        candidates.insert(0, "custom")
    for candidate in candidates:
        if candidate == "whisper-cli":
            text, segments, meta, error = transcribe_with_whisper_cli(audio_path, work_dir, model=model, language=language)
        elif candidate == "faster-whisper":
            text, segments, meta, error = transcribe_with_faster_whisper(audio_path, model=model, language=language)
        elif candidate == "custom":
            text, segments, meta, error = transcribe_with_custom_command(audio_path, command_template)
        else:
            text, segments, meta, error = "", [], {}, f"未知转写后端：{candidate}"
        if text:
            return text, segments, meta, None
        if error:
            errors.append(error)
    return "", [], {}, "没有可用的转写后端。\n\n" + "\n\n".join(errors)


def attach_content_extract(
    info: dict[str, Any],
    *,
    backend: str,
    model: str,
    language: str,
    command_template: str,
    max_seconds: int,
) -> None:
    if info.get("content_extract") or info.get("content_extract_error"):
        return
    media_url = first_non_empty(info, ["media_url"])
    if not media_url:
        info["content_extract_error"] = "当前链接未暴露可转写的公开视频地址"
        return
    duration_seconds = media_duration_seconds(info)
    if duration_seconds and duration_seconds > max_seconds:
        info["content_extract_error"] = f"视频时长 {fmt_duration(duration_seconds)} 超过转写上限 {fmt_duration(max_seconds)}"
        return

    with tempfile.TemporaryDirectory(prefix="my-mind-capture-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        audio_path, audio_error = extract_audio(media_url, temp_dir)
        if audio_error or not audio_path:
            info["content_extract_error"] = audio_error or "音频抽取失败"
            return
        text, segments, meta, transcribe_error = transcribe_audio(
            audio_path,
            temp_dir,
            backend=backend,
            model=model,
            language=language,
            command_template=command_template,
        )
    if transcribe_error or not text:
        info["content_extract_error"] = transcribe_error or "转写未返回文本"
        return

    summary = summarize_transcript_text(text)
    info["content_extract"] = {
        "source": "公开视频音频转写",
        "backend": meta.get("backend", backend),
        "model": meta.get("model", model),
        "language": meta.get("language", language),
        "quality_note": "tiny 模型适合快速验证链路，中文错字会偏多；重要内容建议换 small/medium 模型重跑。"
        if str(meta.get("model", model)).endswith("tiny") or str(meta.get("model", model)) == "tiny"
        else "",
        "segment_count": len(segments),
        "word_count": summary["word_count"],
        "summary": summary["summary"],
        "key_points": summary["key_points"],
        "timeline": build_transcript_timeline(segments),
        "excerpt": summary["excerpt"],
    }


def merge_info(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any] | None:
    if not base and not extra:
        return None
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
        elif key == "description" and len(str(value)) > len(str(merged[key])):
            merged[key] = value
        elif key in {"external_transcript", "external_transcript_url", "external_transcript_error", "youtube_subtitle", "youtube_subtitle_error"}:
            merged[key] = value
        elif key == "platform_meta" and isinstance(value, dict):
            current = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = {**current, **value}
    return merged


def fmt_duration(seconds: Any) -> str:
    if seconds in (None, ""):
        return ""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return str(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def fmt_upload_date(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if re.fullmatch(r"\d{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def excerpt(text: str, limit: int = 1000, notice: str = "（简介过长，第一版仅保留前 1000 字摘录。）") -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return text
    suffix = f"\n\n{notice}" if notice else "..."
    return text[:limit].rstrip() + suffix


def first_non_empty(info: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = info.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def subtitle_languages(info: dict[str, Any], key: str) -> list[str]:
    subtitles = info.get(key) or {}
    if not isinstance(subtitles, dict):
        return []
    return sorted(str(lang) for lang in subtitles.keys())


def build_note(url: str, info: dict[str, Any] | None, error: str | None) -> tuple[str, str]:
    info = info or {}
    platform = guess_platform(url, info)
    material_type = "社媒链接" if platform in {"抖音", "小红书", "X", "TikTok"} else "视频链接"
    title = first_non_empty(info, ["title", "fulltitle", "alt_title"]) or "未命名链接"
    author = first_non_empty(info, ["uploader", "channel", "creator", "artist", "display_id"])
    author_url = first_non_empty(info, ["author_url", "channel_url", "uploader_url"])
    webpage_url = first_non_empty(info, ["webpage_url", "original_url"]) or url
    publish_date = fmt_upload_date(first_non_empty(info, ["upload_date", "release_date", "timestamp"]))
    duration = fmt_duration(info.get("duration"))
    description = excerpt(str(info.get("description") or ""))
    thumbnail = first_non_empty(info, ["thumbnail"])
    extractor = first_non_empty(info, ["extractor_key", "extractor"])
    parse_tool = first_non_empty(info, ["parse_tool"]) or ("yt-dlp" if shutil.which("yt-dlp") else "")
    content_id = first_non_empty(info, ["id", "display_id", "aweme_id", "note_id"])
    author_id = first_non_empty(info, ["uploader_id", "channel_id", "creator_id"])
    like_count = first_non_empty(info, ["like_count", "digg_count"])
    comment_count = first_non_empty(info, ["comment_count"])
    collect_count = first_non_empty(info, ["collect_count", "repost_count"])
    share_count = first_non_empty(info, ["share_count"])
    platform_meta = info.get("platform_meta") if isinstance(info.get("platform_meta"), dict) else {}
    categories = info.get("categories") if isinstance(info.get("categories"), list) else []
    tags = info.get("tags") if isinstance(info.get("tags"), list) else []
    subtitles = subtitle_languages(info, "subtitles")
    automatic_captions = subtitle_languages(info, "automatic_captions")
    youtube_subtitle = info.get("youtube_subtitle") if isinstance(info.get("youtube_subtitle"), dict) else None
    youtube_subtitle_error = first_non_empty(info, ["youtube_subtitle_error"])
    external_transcript = info.get("external_transcript") if isinstance(info.get("external_transcript"), dict) else None
    external_transcript_url = first_non_empty(info, ["external_transcript_url"])
    external_transcript_error = first_non_empty(info, ["external_transcript_error"])
    content_extract = info.get("content_extract") if isinstance(info.get("content_extract"), dict) else None
    content_extract_error = first_non_empty(info, ["content_extract_error"])
    parse_notice = first_non_empty(info, ["parse_notice"])
    parse_status = "已解析" if info and not error else "解析失败"
    if info and error:
        parse_status = "部分解析"
    if external_transcript and info:
        parse_status = "已解析"
    if youtube_subtitle and info:
        parse_status = "已解析"

    filename = sanitize_filename_part(f"{now_date()} {platform} - {title}") + ".md"
    body = [
        "---",
        "类别: 收件箱",
        f"资料类型: {yaml_scalar(material_type)}",
        f"来源平台: {yaml_scalar(platform)}",
        f"标题: {yaml_scalar(title)}",
        f"作者或频道: {yaml_scalar(author)}",
        f"作者编号: {yaml_scalar(author_id)}",
        f"发布时间: {yaml_scalar(publish_date)}",
        f"捕获时间: {yaml_scalar(now_datetime())}",
        f"来源链接: {yaml_scalar(webpage_url)}",
        f"原始链接: {yaml_scalar(url)}",
        f"内容编号: {yaml_scalar(content_id)}",
        f"时长: {yaml_scalar(duration)}",
        f"封面图: {yaml_scalar(thumbnail)}",
        f"点赞数: {yaml_scalar(like_count)}",
        f"评论数: {yaml_scalar(comment_count)}",
        f"收藏数: {yaml_scalar(collect_count)}",
        f"分享数: {yaml_scalar(share_count)}",
        f"解析工具: {yaml_scalar(parse_tool)}",
        f"解析器: {yaml_scalar(extractor)}",
        f"解析状态: {yaml_scalar(parse_status)}",
        f"外部转录链接: {yaml_scalar(external_transcript_url or (external_transcript or {}).get('url', ''))}",
        f"外部转录来源: {yaml_scalar((external_transcript or {}).get('source', ''))}",
        f"字幕来源: {yaml_scalar((youtube_subtitle or {}).get('source', ''))}",
        f"字幕语言: {yaml_scalar((youtube_subtitle or {}).get('language', ''))}",
        f"内容摘录来源: {yaml_scalar((content_extract or {}).get('source', ''))}",
        f"内容摘录后端: {yaml_scalar((content_extract or {}).get('backend', ''))}",
        f"内容摘录字数: {yaml_scalar((content_extract or {}).get('word_count', ''))}",
        "处理状态: 待分拣",
        "关联项目: []",
        "关联领域: []",
        "主题: []",
        "标签:",
        *yaml_list([material_type, platform]),
        "敏感状态: 未知",
        "---",
        "",
        f"# {title}",
        "",
        "## 基础信息",
        "",
        f"- 来源平台：{platform}",
        f"- 作者或频道：{author or '未知'}",
        f"- 作者编号：{author_id or '未知'}",
        f"- 作者主页：{author_url or '未知'}",
        f"- 发布时间：{publish_date or '未知'}",
        f"- 时长：{duration or '未知'}",
        f"- 来源链接：{webpage_url}",
        f"- 内容编号：{content_id or '未知'}",
        f"- 缩略图：{thumbnail or '无'}",
        f"- 解析状态：{parse_status}",
    ]

    if parse_notice:
        body.extend(["", "## 解析说明", "", parse_notice.strip()])

    platform_lines = [
        ("真实链接", platform_meta.get("真实链接")),
        ("作品编号", platform_meta.get("作品编号")),
        ("笔记编号", platform_meta.get("笔记编号")),
        ("作者编号", platform_meta.get("作者编号") or author_id),
        ("笔记类型", platform_meta.get("笔记类型")),
        ("图片数", platform_meta.get("图片数")),
        ("点赞数", platform_meta.get("点赞数") or like_count),
        ("评论数", platform_meta.get("评论数") or comment_count),
        ("收藏数", platform_meta.get("收藏数") or collect_count),
        ("分享数", platform_meta.get("分享数") or share_count),
        ("封面图", platform_meta.get("封面图") or thumbnail),
    ]
    platform_lines = [(label, value) for label, value in platform_lines if value not in (None, "", [])]
    if platform_lines:
        body.extend(["", "## 平台信息", ""])
        for label, value in platform_lines:
            body.append(f"- {label}：{value}")

    if error:
        heading = "解析说明" if info else "解析失败原因"
        body.extend(["", f"## {heading}", ""])
        if info:
            body.append("主解析失败，但备用解析补充了部分基础信息。")
            body.append("")
        body.append(error.strip())

    excerpt_heading = "文案摘录" if platform in {"抖音", "小红书", "X", "TikTok"} else "简介摘录"
    body.extend(["", f"## {excerpt_heading}", "", description or "暂无。"])

    if content_extract:
        body.extend(
            [
                "",
                "## 视频内容摘录",
                "",
                f"- 来源：{content_extract.get('source', '')}",
                f"- 后端：{content_extract.get('backend', '')}",
                f"- 模型：{content_extract.get('model', '')}",
                f"- 语言：{content_extract.get('language', '')}",
                f"- 转写片段数：{content_extract.get('segment_count', 0)}",
                f"- 估算字数：{content_extract.get('word_count', 0)}",
                "",
                "说明：这里保存的是基于转写结果生成的短摘录和关键点，不保存完整逐字稿。",
            ]
        )
        if content_extract.get("quality_note"):
            body.append(str(content_extract["quality_note"]))
        if content_extract.get("summary"):
            body.extend(["", "### 摘要", "", str(content_extract["summary"])])
        key_points = content_extract.get("key_points") if isinstance(content_extract.get("key_points"), list) else []
        if key_points:
            body.extend(["", "### 关键点", ""])
            body.extend(f"- {point}" for point in key_points[:TRANSCRIPT_KEY_POINT_COUNT])
        timeline = content_extract.get("timeline") if isinstance(content_extract.get("timeline"), list) else []
        if timeline:
            body.extend(["", "### 时间线摘录", ""])
            for item in timeline[:30]:
                if isinstance(item, dict):
                    body.append(f"- {item.get('time', '')}：{item.get('text', '')}")
        if content_extract.get("excerpt"):
            body.extend(["", "### 转写摘录", "", str(content_extract["excerpt"])])
    elif content_extract_error:
        body.extend(["", "## 视频内容摘录失败", "", content_extract_error])

    if youtube_subtitle:
        body.extend(
            [
                "",
                "## YouTube 字幕解析",
                "",
                f"- 来源：{youtube_subtitle.get('source', '')}",
                f"- 语言：{youtube_subtitle.get('language', '')}",
                f"- 格式：{youtube_subtitle.get('format', '')}",
                f"- 字幕片段数：{youtube_subtitle.get('segment_count', 0)}",
                f"- 估算词数：{youtube_subtitle.get('word_count', 0)}",
                "",
                "说明：已成功解析字幕轨道。为避免在收件箱阶段复制长篇原文或歌词，这里只保留字幕来源和统计信息；后续整理时基于该来源生成摘要、关键观点和待验证事实。",
            ]
        )
    elif youtube_subtitle_error:
        body.extend(["", "## YouTube 字幕解析失败", "", youtube_subtitle_error])

    if external_transcript:
        body.extend(
            [
                "",
                "## 官方转录来源",
                "",
                f"- 来源：{external_transcript.get('source', '')}",
                f"- 链接：{external_transcript.get('url', external_transcript_url)}",
                f"- 发布时间：{external_transcript.get('published', '') or '未知'}",
                f"- 章节数：{len(external_transcript.get('chapters', []))}",
                f"- 转录片段数：{external_transcript.get('segment_count', 0)}",
                f"- 估算词数：{external_transcript.get('word_count', 0)}",
                "",
                "说明：已找到可追溯的官方转录来源。为避免在收件箱中复制超长原文，这里保留来源、目录和解析入口；后续整理时基于该来源生成摘要、关键观点和待验证事实。",
            ]
        )
        chapters = external_transcript.get("chapters", [])
        if chapters:
            body.extend(["", "## 章节目录", ""])
            for chapter in chapters:
                prefix = f"{chapter.get('time')} - " if chapter.get("time") else ""
                body.append(f"- {prefix}{chapter.get('title', '')}")
    elif external_transcript_error:
        body.extend(["", "## 外部转录解析失败", "", external_transcript_error])

    body.extend(["", "## 标签和分类", ""])
    body.append("### 分类")
    if categories:
        body.extend(f"- {item}" for item in categories)
    else:
        body.append("- 暂无")
    body.append("")
    body.append("### 标签")
    if tags:
        body.extend(f"- {item}" for item in tags[:80])
    else:
        body.append("- 暂无")

    body.extend(["", "## 字幕可用性", ""])
    body.append("### 手动字幕")
    if subtitles:
        body.extend(f"- {lang}" for lang in subtitles)
    else:
        body.append("- 未发现")
    body.append("")
    body.append("### 自动字幕")
    if automatic_captions:
        body.extend(f"- {lang}" for lang in automatic_captions)
    else:
        body.append("- 未发现")

    body.extend(
        [
            "",
            "## 为什么保存",
            "",
            "待补充。",
            "",
            "## 初步想法",
            "",
            "待补充。",
            "",
            "## 后续处理建议",
            "",
            "- 判断是否需要进入资料库。",
            "- 如果有字幕或后续转写，再进行摘要、关键观点和待验证事实提取。",
            "- 如果与项目相关，再萃取到项目上下文、任务清单或问题清单。",
            "",
            "## 原始链接",
            "",
            webpage_url,
        ]
    )
    return filename, "\n".join(body).rstrip() + "\n"


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


def capture_url(
    url: str,
    inbox: Path,
    *,
    dry_run: bool = False,
    extract_content: bool = False,
    transcribe_backend: str = "auto",
    transcribe_model: str = "small",
    transcribe_language: str = "zh",
    transcribe_command: str = "",
    max_transcribe_seconds: int = 7200,
) -> Path:
    info, error = run_yt_dlp(url)
    platform = guess_platform(url, info)
    if info and platform == "YouTube":
        attach_yt_dlp_subtitle(info)
    if info is None and guess_platform(url) == "YouTube":
        fallback_info, fallback_error = youtube_oembed(url)
        page_info, page_error = youtube_page_info(url)
        fallback_info = merge_info(fallback_info, page_info)
        if fallback_info is not None:
            info = fallback_info
            if error:
                error = f"yt-dlp 解析失败：{error}\n\n已使用 YouTube oEmbed 备用解析补充基础信息。"
            else:
                error = "已使用 YouTube oEmbed 备用解析补充基础信息。"
            if page_error:
                error = f"{error}\n\n{page_error}"
        elif fallback_error:
            error = f"{error}\n\n{fallback_error}" if error else fallback_error
    elif platform == "YouTube":
        page_info, page_error = youtube_page_info(url)
        info = merge_info(info, page_info)
        if info:
            attach_yt_dlp_subtitle(info)
        if page_error:
            error = f"{error}\n\n{page_error}" if error else page_error
    elif platform in {"抖音", "小红书"} or guess_platform(url) in {"抖音", "小红书"}:
        platform = platform if platform in {"抖音", "小红书"} else guess_platform(url)
        if platform == "抖音":
            page_info, page_error = douyin_page_info(url)
        else:
            page_info, page_error = xiaohongshu_page_info(url)
        if page_info is not None:
            info = merge_info(info, page_info)
            note = f"已使用{platform}公开页面备用解析补充基础信息。"
            if error:
                info = info or {}
                info["parse_notice"] = f"yt-dlp 解析失败：{error}\n\n{note}"
                error = None
            elif info and first_non_empty(info, ["parse_tool"]) == "公开页面 HTML":
                error = None
        elif page_error:
            error = f"{error}\n\n{page_error}" if error else page_error
    if extract_content and info and not dry_run:
        attach_content_extract(
            info,
            backend=transcribe_backend,
            model=transcribe_model,
            language=transcribe_language,
            command_template=transcribe_command,
            max_seconds=max_transcribe_seconds,
        )
    filename, note = build_note(url, info, error)
    output_path = unique_path(inbox, filename)
    if not dry_run:
        inbox.mkdir(parents=True, exist_ok=True)
        output_path.write_text(note, encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture links into 00_收件箱 as Markdown notes.")
    parser.add_argument("urls", nargs="+", help="Links to capture")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Inbox directory")
    parser.add_argument("--dry-run", action="store_true", help="Print target paths without writing files")
    parser.add_argument(
        "--extract-content",
        action="store_true",
        help="Extract public media audio and generate a short content excerpt when a transcription backend is available",
    )
    parser.add_argument(
        "--transcribe-backend",
        default="auto",
        choices=["auto", "whisper-cli", "faster-whisper", "custom"],
        help="Transcription backend used by --extract-content",
    )
    parser.add_argument("--transcribe-model", default="small", help="Whisper/faster-whisper model name")
    parser.add_argument("--transcribe-language", default="zh", help="Transcription language, or auto")
    parser.add_argument(
        "--transcribe-command",
        default="",
        help="Custom transcription command template; use {audio} as the extracted audio path",
    )
    parser.add_argument(
        "--max-transcribe-seconds",
        type=int,
        default=7200,
        help="Skip transcription when known media duration exceeds this many seconds",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inbox = Path(args.inbox)
    if not inbox.is_absolute():
        inbox = ROOT / inbox

    created: list[Path] = []
    for url in args.urls:
        created.append(
            capture_url(
                url,
                inbox,
                dry_run=args.dry_run,
                extract_content=args.extract_content,
                transcribe_backend=args.transcribe_backend,
                transcribe_model=args.transcribe_model,
                transcribe_language=args.transcribe_language,
                transcribe_command=args.transcribe_command,
                max_transcribe_seconds=args.max_transcribe_seconds,
            )
        )

    for path in created:
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
