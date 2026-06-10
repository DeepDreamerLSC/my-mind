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
    parse_status = "已解析" if info and not error else "解析失败"
    if info and error:
        parse_status = "部分解析"

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
    if info is None and guess_platform(url) == "YouTube":
        fallback_info, fallback_error = youtube_oembed(url)
        if fallback_info is not None:
            info = fallback_info
            if error:
                error = f"yt-dlp 解析失败：{error}\n\n已使用 YouTube oEmbed 备用解析补充基础信息。"
            else:
                error = "已使用 YouTube oEmbed 备用解析补充基础信息。"
        elif fallback_error:
            error = f"{error}\n\n{fallback_error}" if error else fallback_error
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
