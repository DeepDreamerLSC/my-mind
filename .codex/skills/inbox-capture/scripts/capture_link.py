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
    if "douyin" in extractor_l or "douyin.com" in host:
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


def fetch_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore")


def strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(p|div|li|h[1-6])>", "\n", value, flags=re.I)
    value = re.sub(r"<.*?>", " ", value, flags=re.S)
    value = html_unescape(urllib.parse.unquote(value))
    value = value.replace("\\u0026", "&")
    value = value.replace("\\/", "/")
    return " ".join(value.split())


def normalize_transcript_text(text: str) -> str:
    text = html_unescape(text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def excerpt(text: str, limit: int = 1000) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n（简介过长，第一版仅保留前 1000 字摘录。）"


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
    title = first_non_empty(info, ["title", "fulltitle", "alt_title"]) or "未命名链接"
    author = first_non_empty(info, ["uploader", "channel", "creator", "artist", "display_id"])
    author_url = first_non_empty(info, ["author_url", "channel_url", "uploader_url"])
    webpage_url = first_non_empty(info, ["webpage_url", "original_url"]) or url
    publish_date = fmt_upload_date(first_non_empty(info, ["upload_date", "release_date", "timestamp"]))
    duration = fmt_duration(info.get("duration"))
    description = excerpt(str(info.get("description") or ""))
    thumbnail = first_non_empty(info, ["thumbnail"])
    extractor = first_non_empty(info, ["extractor_key", "extractor"])
    categories = info.get("categories") if isinstance(info.get("categories"), list) else []
    tags = info.get("tags") if isinstance(info.get("tags"), list) else []
    subtitles = subtitle_languages(info, "subtitles")
    automatic_captions = subtitle_languages(info, "automatic_captions")
    youtube_subtitle = info.get("youtube_subtitle") if isinstance(info.get("youtube_subtitle"), dict) else None
    youtube_subtitle_error = first_non_empty(info, ["youtube_subtitle_error"])
    external_transcript = info.get("external_transcript") if isinstance(info.get("external_transcript"), dict) else None
    external_transcript_url = first_non_empty(info, ["external_transcript_url"])
    external_transcript_error = first_non_empty(info, ["external_transcript_error"])
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
        "资料类型: 视频链接",
        f"来源平台: {yaml_scalar(platform)}",
        f"标题: {yaml_scalar(title)}",
        f"作者或频道: {yaml_scalar(author)}",
        f"发布时间: {yaml_scalar(publish_date)}",
        f"捕获时间: {yaml_scalar(now_datetime())}",
        f"来源链接: {yaml_scalar(webpage_url)}",
        f"原始链接: {yaml_scalar(url)}",
        f"时长: {yaml_scalar(duration)}",
        f"解析工具: {yaml_scalar('yt-dlp' if shutil.which('yt-dlp') else '')}",
        f"解析器: {yaml_scalar(extractor)}",
        f"解析状态: {yaml_scalar(parse_status)}",
        f"外部转录链接: {yaml_scalar(external_transcript_url or (external_transcript or {}).get('url', ''))}",
        f"外部转录来源: {yaml_scalar((external_transcript or {}).get('source', ''))}",
        f"字幕来源: {yaml_scalar((youtube_subtitle or {}).get('source', ''))}",
        f"字幕语言: {yaml_scalar((youtube_subtitle or {}).get('language', ''))}",
        "处理状态: 待分拣",
        "关联项目: []",
        "关联领域: []",
        "主题: []",
        "标签:",
        *yaml_list(["视频链接", platform]),
        "敏感状态: 未知",
        "---",
        "",
        f"# {title}",
        "",
        "## 基础信息",
        "",
        f"- 来源平台：{platform}",
        f"- 作者或频道：{author or '未知'}",
        f"- 作者主页：{author_url or '未知'}",
        f"- 发布时间：{publish_date or '未知'}",
        f"- 时长：{duration or '未知'}",
        f"- 来源链接：{webpage_url}",
        f"- 缩略图：{thumbnail or '无'}",
        f"- 解析状态：{parse_status}",
    ]

    if error:
        heading = "解析说明" if info else "解析失败原因"
        body.extend(["", f"## {heading}", ""])
        if info:
            body.append("主解析失败，但备用解析补充了部分基础信息。")
            body.append("")
        body.append(error.strip())

    body.extend(["", "## 简介摘录", "", description or "暂无。"])

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


def capture_url(url: str, inbox: Path, dry_run: bool = False) -> Path:
    info, error = run_yt_dlp(url)
    if info and guess_platform(url, info) == "YouTube":
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
    elif guess_platform(url, info) == "YouTube":
        page_info, page_error = youtube_page_info(url)
        info = merge_info(info, page_info)
        if info:
            attach_yt_dlp_subtitle(info)
        if page_error:
            error = f"{error}\n\n{page_error}" if error else page_error
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inbox = Path(args.inbox)
    if not inbox.is_absolute():
        inbox = ROOT / inbox

    created: list[Path] = []
    for url in args.urls:
        created.append(capture_url(url, inbox, dry_run=args.dry_run))

    for path in created:
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
