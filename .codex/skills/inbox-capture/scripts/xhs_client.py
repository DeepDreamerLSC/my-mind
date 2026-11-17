#!/usr/bin/env python3
"""自建 XHS-Downloader 服务客户端。

职责：笔记详情解析（POST /xhs/detail）、媒体下载、ffprobe 校验、缓存目录清理。
仅用 Python 标准库；所有错误消息为中文，且绝不泄露 bearer_token 的任何片段。
模式对齐 dtk_client.py（抖音批次已验证的同一套生命周期）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


XHS_CONFIG_PATH = Path.home() / ".config" / "my-mind" / "xhs.json"
MEDIA_CACHE_ROOT = Path.home() / ".cache" / "my-mind" / "media" / "xhs"
DETAIL_SUCCESS_MESSAGE = "获取小红书作品数据成功"
HTTP_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_BYTES = 256 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300
FFPROBE_TIMEOUT_SECONDS = 60
DEFAULT_MAX_MEDIA_BYTES = 1024 * 1024 * 1024
XHS_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_HTTP_STATUS_LABELS = {
    400: "请求参数错误",
    401: "Bearer Token 无效或未提供",
    403: "没有访问权限",
    404: "接口不存在",
    405: "请求方法不被允许",
    429: "请求过于频繁",
    500: "服务内部错误",
    502: "网关错误",
    503: "服务暂不可用",
    504: "网关超时",
}
_SAFE_NOTE_ID_RE = re.compile(r"[0-9A-Za-z._-]+")


class XhsError(Exception):
    """自建 XHS-Downloader 服务调用失败；unavailable=True 表示服务未就绪（未配置/连不上/超时），调用方可降级。"""

    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        super().__init__(message)
        self.unavailable = unavailable


def redact_secrets(text: str, secrets: list[str]) -> str:
    """把消息里出现的密钥片段替换为 ***，保证错误消息可安全写进笔记。"""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def load_config(path: Path = XHS_CONFIG_PATH) -> dict[str, str]:
    """读取 ~/.config/my-mind/xhs.json，返回 base_url 与 bearer_token；缺失或非法时抛中文错误。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise XhsError(f"未找到小红书配置文件 {path}（需包含 base_url 与 bearer_token），无法调用自建服务。", unavailable=True) from None
    except OSError as exc:
        raise XhsError(f"读取小红书配置文件失败：{path}（{exc.strerror or exc}）。", unavailable=True) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise XhsError(f"小红书配置文件不是有效 JSON：{path}（{exc}）。", unavailable=True) from exc
    if not isinstance(data, dict):
        raise XhsError(f"小红书配置文件顶层必须是 JSON 对象：{path}。", unavailable=True)
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    bearer_token = str(data.get("bearer_token") or "").strip()
    if not base_url or not bearer_token:
        raise XhsError(f"小红书配置文件缺少 base_url 或 bearer_token：{path}。", unavailable=True)
    return {"base_url": base_url, "bearer_token": bearer_token}


def _connection_reason(exc: Exception) -> str:
    """提取连接失败的可读原因（连接拒绝/超时/DNS 等），用于中文错误消息。"""
    reason = getattr(exc, "reason", None)
    if reason is not None:
        return repr(reason)
    return repr(exc)


def _request_json(
    config: dict[str, str],
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    timeout: int = HTTP_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    """发起 HTTP 请求并把响应解析为 JSON 信封；连接类失败抛 unavailable 的 XhsError。"""
    bearer_token = str(config.get("bearer_token") or "")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if bearer_token:
        request.add_header("Authorization", f"Bearer {bearer_token}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001 - HTTPError body 读取失败时退回空响应。
            raw = b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = _connection_reason(exc)
        raise XhsError(
            f"无法连接自建 XHS-Downloader 服务（{reason}）。请确认本机服务已启动（默认 http://127.0.0.1:18601）。",
            unavailable=True,
        ) from exc

    text = redact_secrets(raw.decode("utf-8", "ignore"), [bearer_token])
    if not text.strip():
        raise XhsError(f"自建 XHS-Downloader 服务返回了空响应（HTTP {status}）。")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise XhsError(f"自建 XHS-Downloader 服务返回了非 JSON 响应（HTTP {status}）：{text.strip()[:200]}。") from None
    if not isinstance(payload, dict):
        raise XhsError(f"自建 XHS-Downloader 服务返回的顶层不是 JSON 对象（HTTP {status}）：{text.strip()[:200]}。")
    return status, payload


def fetch_detail(url: str, *, config: dict[str, str] | None = None) -> dict[str, Any]:
    """POST /xhs/detail 解析笔记；校验 message 信封与 data，返回中文键的 data 字典。

    响应信封：{"message": ..., "params": ..., "data": {...}}；成功 message=获取小红书作品数据成功。
    """
    cfg = config if isinstance(config, dict) and config.get("base_url") and config.get("bearer_token") else load_config()
    endpoint = f"{cfg['base_url']}/xhs/detail"
    body = json.dumps(
        {"url": url, "download": False, "index": None, "cookie": None, "proxy": None, "check_record": True},
        ensure_ascii=False,
    ).encode("utf-8")
    status, payload = _request_json(cfg, "POST", endpoint, body=body)

    message = str(payload.get("message") or "").strip()
    data = payload.get("data")
    if status not in {200, 201}:
        label = _HTTP_STATUS_LABELS.get(status, "请求失败")
        detail = f"：{message}" if message else ""
        raise XhsError(f"自建 XHS-Downloader 服务详情请求失败：HTTP {status} {label}{detail}")
    if message != DETAIL_SUCCESS_MESSAGE:
        raise XhsError(f"自建 XHS-Downloader 服务返回失败消息（HTTP {status}）：{message or '服务未返回 message 字段'}。")
    if not isinstance(data, dict):
        raise XhsError(f"自建 XHS-Downloader 服务未返回可用的 data 字段（HTTP {status}）。")
    return data


def sanitize_media_filename(value: str) -> str:
    """从 URL 路径或显式文件名生成安全的本地文件名。"""
    name = urllib.parse.urlparse(str(value or "").strip()).path or str(value or "").strip()
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("._")
    return name[:120] or "media.bin"


def download_media(
    url: str,
    dest_dir: Path,
    max_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
    *,
    filename: str = "",
    headers: dict[str, str] | None = None,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    """下载媒体到 dest_dir；超出 max_bytes 时中止并删除不完整文件。"""
    if not url:
        raise XhsError("媒体下载失败：下载地址为空。")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / sanitize_media_filename(filename or url)
    request = urllib.request.Request(url, headers=dict(headers or XHS_DOWNLOAD_HEADERS), method="GET")

    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status not in {200, 206}:
                label = _HTTP_STATUS_LABELS.get(status, "")
                raise XhsError(f"媒体下载失败：HTTP {status} {label}".rstrip())
            with open(dest, "wb") as output:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise XhsError(
                            f"媒体下载失败：已下载 {total} 字节，超过上限 {max_bytes} 字节，已停止。"
                        )
                    output.write(chunk)
    except urllib.error.HTTPError as exc:
        label = _HTTP_STATUS_LABELS.get(int(exc.code), "")
        raise XhsError(f"媒体下载失败：HTTP {exc.code} {label}".rstrip()) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise XhsError(f"媒体下载失败：无法连接媒体地址（{_connection_reason(exc)}）。") from exc
    except XhsError:
        dest.unlink(missing_ok=True)
        raise

    if total <= 0:
        dest.unlink(missing_ok=True)
        raise XhsError("媒体下载失败：下载结果为空。")
    return dest


def ffprobe_check(path: Path) -> dict[str, Any]:
    """ffprobe 校验媒体格式与时长；返回 {format_name, duration_seconds, size_bytes}。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise XhsError("未找到 ffprobe，无法校验媒体文件格式与时长。")
    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFPROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise XhsError(f"媒体校验失败：ffprobe 超过 {FFPROBE_TIMEOUT_SECONDS} 秒未返回。") from exc
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "").strip() or f"返回码 {result.returncode}"
        raise XhsError(f"媒体校验失败：ffprobe 无法识别该文件：{reason[-500:]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise XhsError(f"媒体校验失败：ffprobe 输出不是 JSON：{exc!r}") from exc
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    format_name = str(fmt.get("format_name") or "").strip()
    if not format_name:
        raise XhsError("媒体校验失败：ffprobe 未返回格式信息，文件可能已损坏。")
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    size_bytes = 0
    try:
        size_bytes = int(float(fmt.get("size") or 0))
    except (TypeError, ValueError):
        size_bytes = 0
    return {"format_name": format_name, "duration_seconds": duration, "size_bytes": size_bytes}


def media_cache_dir(note_id: str) -> Path:
    """返回 ~/.cache/my-mind/media/xhs/<note_id>/；笔记编号不合法时抛错防止路径逃逸。"""
    note_id = str(note_id or "").strip()
    if not note_id or not _SAFE_NOTE_ID_RE.fullmatch(note_id):
        raise XhsError(f"笔记编号不合法，无法定位媒体缓存目录：{note_id!r}。")
    return MEDIA_CACHE_ROOT / note_id


def cleanup_media(note_id: str) -> str:
    """删除 ~/.cache/my-mind/media/xhs/<note_id>/ 整目录；返回中文说明（不抛错）。"""
    try:
        target = media_cache_dir(note_id)
    except XhsError as exc:
        return str(exc)
    if not target.exists():
        return f"媒体缓存目录不存在，无需清理：{target}"
    shutil.rmtree(target, ignore_errors=True)
    if target.exists():
        return f"媒体缓存目录删除未完全成功，请手动检查：{target}"
    return f"已删除媒体缓存目录：{target}"
