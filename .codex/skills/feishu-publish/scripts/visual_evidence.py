#!/usr/bin/env python3
"""Prepare source-backed visual evidence for Feishu reading pages."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, TypeVar

import reading_version as reading


MAX_VISUALS = 4
MAX_IMAGE_BYTES = 12 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
IMAGE_SUFFIXES = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
IMAGE_CONTENT_TYPES = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
T = TypeVar("T")


@dataclass(frozen=True)
class VisualEvidence:
    """A planned source visual and its stable Feishu insertion anchor."""

    evidence_id: str
    kind: str
    caption: str
    anchor: str
    source_url: str = ""
    timecode: str = ""
    end_timecode: str = ""
    seconds: int = 0
    local_path: str = ""
    media_url: str = ""

    def record(self) -> dict[str, object]:
        return {
            "id": self.evidence_id,
            "kind": self.kind,
            "caption": self.caption,
            "anchor": self.anchor,
            "source_url": self.source_url,
            "timecode": self.timecode,
            "end_timecode": self.end_timecode,
            "seconds": self.seconds,
        }


@dataclass(frozen=True)
class MaterializedVisual:
    evidence: VisualEvidence
    asset_path: Path | None = None
    error: str = ""

    def record(self) -> dict[str, object]:
        result = self.evidence.record()
        result.update(
            {
                "asset_path": str(self.asset_path) if self.asset_path else "",
                "error": self.error,
            }
        )
        return result


def safe_http_url(value: object) -> str:
    """Return a safe public HTTP(S) URL, rejecting obvious local targets."""

    raw = str(value or "").strip().strip("<>")
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        return ""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        return ""
    return raw


def complete_caption_text(value: object, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?:\.{3,}|…+)\s*$", "", text).rstrip("，,、；;：: ")
    if len(text) > limit:
        sentences = reading.sentence_units(text)
        selected = ""
        for sentence in sentences:
            if selected and len(selected + sentence) > limit:
                break
            selected += sentence
        text = selected or reading.complete_statement(text[: limit - 1])
    return reading.complete_statement(text)


def parse_timestamp(value: object) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        return 0
    values = [int(part) for part in text.split(":")]
    if len(values) == 2:
        minutes, seconds = values
        return minutes * 60 + seconds
    hours, minutes, seconds = values
    return hours * 3600 + minutes * 60 + seconds


def format_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def evidence_id(kind: str, source: str, timecode: str = "") -> str:
    payload = f"{kind}\n{source}\n{timecode}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def extract_timeline_entries(body: str) -> list[tuple[str, int, str]]:
    heading = re.search(r"^### 时间线摘录\s*$", body, flags=re.M)
    if not heading:
        return []
    remainder = body[heading.end() :]
    next_heading = re.search(r"^#{1,3}\s+", remainder, flags=re.M)
    section = remainder[: next_heading.start()] if next_heading else remainder
    entries: list[tuple[str, int, str]] = []
    for match in re.finditer(r"^-\s*(\d{1,2}:\d{2}(?::\d{2})?)：\s*(.+)$", section, flags=re.M):
        timecode = match.group(1)
        seconds = parse_timestamp(timecode)
        text = complete_caption_text(match.group(2))
        if text:
            entries.append((format_timestamp(seconds), seconds, text))
    return entries


def select_distributed(items: Sequence[T], limit: int) -> list[T]:
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    indexes = {
        round(position * (len(items) - 1) / (limit - 1))
        for position in range(limit)
    }
    return [items[index] for index in sorted(indexes)]


def extract_markdown_images(body: str, source_path: Path | None) -> tuple[list[str], list[Path]]:
    remote_urls: list[str] = []
    local_paths: list[Path] = []
    for raw in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", body):
        target = raw.strip().strip("<>")
        remote = safe_http_url(target)
        if remote:
            if remote not in remote_urls:
                remote_urls.append(remote)
            continue
        if not source_path or not target or urllib.parse.urlparse(target).scheme:
            continue
        decoded = urllib.parse.unquote(target)
        candidate = (source_path.parent / decoded).resolve()
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES and candidate not in local_paths:
            local_paths.append(candidate)
    return remote_urls, local_paths


def extract_declared_image_urls(body: str) -> list[str]:
    urls: list[str] = []
    for line in body.splitlines():
        if not any(label in line for label in ("来源图片", "原始图片", "封面图")):
            continue
        for raw in re.findall(r"https?://[^\s<>)]+", line):
            url = safe_http_url(raw.rstrip("，。；,;"))
            if url and url not in urls:
                urls.append(url)
    return urls


def source_url(metadata: Mapping[str, str], original_url: str) -> str:
    for candidate in (original_url, metadata.get("来源链接", ""), metadata.get("原始链接", "")):
        url = safe_http_url(candidate)
        if url:
            return url
    return ""


def build_visual_evidence(
    *,
    metadata: Mapping[str, str],
    body: str,
    source_path: Path | None,
    original_url: str,
    max_visuals: int = MAX_VISUALS,
    reading_version: reading.ReadingVersion | None = None,
) -> list[VisualEvidence]:
    """Create a bounded, source-only visual plan for one reading item."""

    limit = max(0, min(int(max_visuals), MAX_VISUALS))
    if not limit:
        return []

    result: list[VisualEvidence] = []
    seen_sources: set[str] = set()
    origin = source_url(metadata, original_url)

    def append(
        *,
        kind: str,
        caption: str,
        source: str = "",
        timecode: str = "",
        end_timecode: str = "",
        seconds: int = 0,
        local: Path | None = None,
        media_url: str = "",
    ) -> None:
        if len(result) >= limit:
            return
        identity_source = source or (str(local) if local else "")
        if not identity_source:
            return
        dedupe_key = f"{kind}:{identity_source}:{timecode}"
        if dedupe_key in seen_sources:
            return
        seen_sources.add(dedupe_key)
        number = len(result) + 1
        result.append(
            VisualEvidence(
                evidence_id=evidence_id(kind, identity_source, timecode),
                kind=kind,
                caption=caption,
                anchor=f"媒体证据 {number:02d}",
                source_url=source,
                timecode=timecode,
                end_timecode=end_timecode,
                seconds=seconds,
                local_path=str(local) if local else "",
                media_url=media_url,
            )
        )

    cover = safe_http_url(metadata.get("封面图", ""))
    if cover:
        append(kind="cover", caption="来源封面", source=cover)

    video_media_url = safe_http_url(metadata.get("视频媒体链接", ""))
    if reading_version and reading_version.claims and origin:
        distinct_claims: list[reading.Claim] = []
        seen_ranges: set[tuple[int, int]] = set()
        for claim in reading_version.claims:
            time_range = (claim.start_seconds, claim.end_seconds)
            if time_range in seen_ranges:
                continue
            seen_ranges.add(time_range)
            distinct_claims.append(claim)
        for claim in select_distributed(distinct_claims, limit - len(result)):
            timecode = format_timestamp(claim.start_seconds)
            end_timecode = format_timestamp(claim.end_seconds)
            append(
                kind="video_frame",
                caption=f"关键画面 {timecode}-{end_timecode}：{claim.statement}",
                source=origin,
                timecode=timecode,
                end_timecode=end_timecode,
                seconds=max(1, claim.start_seconds),
                media_url=video_media_url,
            )
        return result

    timeline = extract_timeline_entries(body)
    if timeline and origin:
        for timecode, seconds, text in select_distributed(timeline, limit - len(result)):
            append(
                kind="video_frame",
                caption=f"关键画面 {timecode}：{text}",
                source=origin,
                timecode=timecode,
                seconds=max(1, seconds),
                media_url=video_media_url,
            )
        return result

    markdown_urls, local_paths = extract_markdown_images(body, source_path)
    image_urls = [*extract_declared_image_urls(body), *markdown_urls]
    for local in local_paths:
        append(kind="local_image", caption=f"来源图片：{local.name}", local=local)
    for image_url in image_urls:
        if image_url == cover:
            continue
        append(kind="source_image", caption="来源原始图片", source=image_url)
    return result


def manifest_hash(evidence: Sequence[VisualEvidence]) -> str:
    payload = [item.record() for item in evidence]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def image_suffix(content_type: str, url: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in IMAGE_CONTENT_TYPES:
        return IMAGE_CONTENT_TYPES[normalized]
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return suffix
    guessed = mimetypes.guess_extension(normalized) if normalized else ""
    return guessed if guessed in IMAGE_SUFFIXES else ".jpg"


def existing_asset(output_dir: Path, evidence_id_value: str) -> Path | None:
    for path in sorted(output_dir.glob(f"{evidence_id_value}.*")):
        if path.is_file() and path.stat().st_size >= 128:
            return path
    return None


def download_image(url: str, output_dir: Path, evidence_id_value: str, timeout_seconds: int) -> tuple[Path | None, str]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - safe_http_url limits the scheme.
            content_type = str(response.headers.get("Content-Type") or "")
            declared_length = response.headers.get("Content-Length")
            if declared_length and int(declared_length) > MAX_IMAGE_BYTES:
                return None, "图片超过 12MB 限制。"
            payload = response.read(MAX_IMAGE_BYTES + 1)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return None, f"图片下载失败：{exc!r}"
    if len(payload) < 128:
        return None, "图片内容为空或过小。"
    if not content_type.lower().startswith("image/") and not payload.startswith((b"\xff\xd8", b"\x89PNG", b"GIF8", b"RIFF")):
        return None, f"来源不是图片内容：{content_type or '未知'}"
    destination = output_dir / f"{evidence_id_value}{image_suffix(content_type, url)}"
    destination.write_bytes(payload)
    return destination, ""


def resolve_video_stream(source: str, preferred_media_url: str, timeout_seconds: int) -> tuple[str, str]:
    preferred = safe_http_url(preferred_media_url)
    if preferred:
        return preferred, ""
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        return "", "未找到 yt-dlp，无法解析公开视频流。"
    try:
        result = subprocess.run(
            [yt_dlp, "--dump-single-json", "--no-playlist", "--skip-download", source],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"视频流解析失败：{exc!r}"
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or f"返回码 {result.returncode}").strip()
        return "", f"视频流解析失败：{reason[-500:]}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return "", f"视频流解析结果不是 JSON：{exc!r}"
    formats = payload.get("requested_formats") or payload.get("formats") or []
    if isinstance(formats, list):
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            if str(fmt.get("vcodec") or "").lower() in {"", "none"}:
                continue
            url = safe_http_url(fmt.get("url"))
            if url:
                return url, ""
    direct = safe_http_url(payload.get("url"))
    if direct:
        return direct, ""
    return "", "视频解析未返回可用画面流。"


def extract_video_frame(video_url: str, seconds: int, output_dir: Path, evidence_id_value: str, timeout_seconds: int) -> tuple[Path | None, str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None, "未找到 ffmpeg，无法抽取视频画面。"
    destination = output_dir / f"{evidence_id_value}.jpg"
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(1, seconds)),
        "-i",
        video_url,
        "-frames:v",
        "1",
        "-vf",
        "scale='min(1440,iw)':-2",
        "-q:v",
        "3",
        str(destination),
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(10, timeout_seconds),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"视频截图失败：{exc!r}"
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size < 128:
        reason = (result.stderr or result.stdout or f"返回码 {result.returncode}").strip()
        return None, f"视频截图失败：{reason[-500:]}"
    return destination, ""


def materialize_visual_evidence(
    evidence: Sequence[VisualEvidence],
    *,
    output_dir: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[MaterializedVisual]:
    """Fetch source images and extract bounded video frames into a local cache."""

    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[MaterializedVisual] = []
    video_stream_url = ""
    video_stream_error = ""
    for item in evidence:
        if item.local_path:
            path = Path(item.local_path)
            if path.exists() and path.is_file() and path.stat().st_size >= 128:
                results.append(MaterializedVisual(evidence=item, asset_path=path))
            else:
                results.append(MaterializedVisual(evidence=item, error="本地图片不存在或内容为空。"))
            continue

        cached = existing_asset(output_dir, item.evidence_id)
        if cached:
            results.append(MaterializedVisual(evidence=item, asset_path=cached))
            continue

        if item.kind == "video_frame":
            if not video_stream_url and not video_stream_error:
                video_stream_url, video_stream_error = resolve_video_stream(
                    item.source_url,
                    item.media_url,
                    timeout_seconds,
                )
            if not video_stream_url:
                results.append(MaterializedVisual(evidence=item, error=video_stream_error or "未找到可用视频流。"))
                continue
            asset_path, error = extract_video_frame(
                video_stream_url,
                item.seconds,
                output_dir,
                item.evidence_id,
                timeout_seconds,
            )
        else:
            asset_path, error = download_image(item.source_url, output_dir, item.evidence_id, timeout_seconds)
        results.append(MaterializedVisual(evidence=item, asset_path=asset_path, error=error))
    return results
