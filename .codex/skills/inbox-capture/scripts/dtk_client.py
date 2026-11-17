#!/usr/bin/env python3
"""自建 dtk（Douyin_TikTok_Download_API）客户端。

职责：作品解析（含异步任务轮询）、媒体断点下载、ffprobe 校验、缓存目录清理。
仅用 Python 标准库；所有错误消息为中文，且绝不泄露 api_key 的任何片段。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DTK_CONFIG_PATH = Path.home() / ".config" / "my-mind" / "dtk.json"
MEDIA_CACHE_ROOT = Path.home() / ".cache" / "my-mind" / "media" / "douyin"
PARSE_WAIT_SECONDS = 25
TASK_POLL_MAX_SECONDS = 30
TASK_POLL_INTERVAL_SECONDS = 1.5
HTTP_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_BYTES = 256 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300
FFPROBE_TIMEOUT_SECONDS = 60
DEFAULT_MAX_MEDIA_BYTES = 1024 * 1024 * 1024
DOUYIN_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.douyin.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_HTTP_STATUS_LABELS = {
    400: "请求参数错误",
    401: "API Key 无效或未提供",
    403: "没有访问权限",
    404: "接口不存在",
    405: "请求方法不被允许",
    429: "请求过于频繁",
    500: "服务内部错误",
    502: "网关错误",
    503: "服务暂不可用",
    504: "网关超时",
}
_SAFE_CONTENT_ID_RE = re.compile(r"[0-9A-Za-z._-]+")


class DtkError(Exception):
    """自建 dtk 服务调用失败；unavailable=True 表示服务未就绪（未配置/连不上/超时），调用方可降级。"""

    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        super().__init__(message)
        self.unavailable = unavailable


def redact_secrets(text: str, secrets: list[str]) -> str:
    """把消息里出现的密钥片段替换为 ***，保证错误消息可安全写进笔记。"""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def load_config(path: Path = DTK_CONFIG_PATH) -> dict[str, str]:
    """读取 ~/.config/my-mind/dtk.json，返回 base_url 与 api_key；缺失或非法时抛中文错误。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DtkError(f"未找到 dtk 配置文件 {path}（需包含 base_url 与 api_key），无法调用自建服务。", unavailable=True) from None
    except OSError as exc:
        raise DtkError(f"读取 dtk 配置文件失败：{path}（{exc.strerror or exc}）。", unavailable=True) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DtkError(f"dtk 配置文件不是有效 JSON：{path}（{exc}）。", unavailable=True) from exc
    if not isinstance(data, dict):
        raise DtkError(f"dtk 配置文件顶层必须是 JSON 对象：{path}。", unavailable=True)
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    api_key = str(data.get("api_key") or "").strip()
    if not base_url or not api_key:
        raise DtkError(f"dtk 配置文件缺少 base_url 或 api_key：{path}。", unavailable=True)
    return {"base_url": base_url, "api_key": api_key}


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
    """发起 HTTP 请求并把响应解析为 JSON 信封；连接类失败抛 unavailable 的 DtkError。"""
    api_key = str(config.get("api_key") or "")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if api_key:
        request.add_header("X-API-Key", api_key)
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
        raise DtkError(
            f"无法连接自建 dtk 服务（{reason}）。请确认本机服务已启动（docker compose -p dtk up -d）。",
            unavailable=True,
        ) from exc

    text = redact_secrets(raw.decode("utf-8", "ignore"), [api_key])
    if not text.strip():
        raise DtkError(f"自建 dtk 服务返回了空响应（HTTP {status}）。")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise DtkError(f"自建 dtk 服务返回了非 JSON 响应（HTTP {status}）：{text.strip()[:200]}。") from None
    if not isinstance(payload, dict):
        raise DtkError(f"自建 dtk 服务返回的顶层不是 JSON 对象（HTTP {status}）：{text.strip()[:200]}。")
    return status, payload


def _envelope_data(status: int, payload: dict[str, Any], *, context: str) -> dict[str, Any]:
    """校验统一信封 {success, data, error, meta}，返回其中的 data；失败抛中文 DtkError。"""
    raw_error = payload.get("error")
    error = raw_error if isinstance(raw_error, dict) else {}
    error_code = str(error.get("code") or "")
    error_message = str(error.get("message") or ("" if isinstance(raw_error, dict) else raw_error or ""))
    if status not in {200, 201, 202}:
        label = _HTTP_STATUS_LABELS.get(status, "请求失败")
        detail = f"（{error_code}：{error_message}）" if error_code or error_message else ""
        raise DtkError(f"{context}失败：HTTP {status} {label}{detail}")
    if payload.get("success") is False:
        code = error_code or f"HTTP_{status}"
        message = f"：{error_message}" if error_message else "。"
        raise DtkError(f"{context}返回错误（{code}）{message}")
    data = payload.get("data")
    if data is None:
        raise DtkError(f"{context}未返回 data 字段（HTTP {status}）。")
    if not isinstance(data, dict):
        raise DtkError(f"{context}返回的 data 不是 JSON 对象（HTTP {status}）。")
    return data


def _poll_task(
    config: dict[str, str],
    task_id: str,
    *,
    poll_max_seconds: int = TASK_POLL_MAX_SECONDS,
) -> dict[str, Any]:
    """轮询 GET /api/v1/tasks/<task_id> 直至 done/failed；上限 poll_max_seconds 秒。"""
    attempts = max(1, int(poll_max_seconds / TASK_POLL_INTERVAL_SECONDS))
    endpoint = f"{config['base_url']}/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}"
    last_state = ""
    for attempt in range(attempts):
        status, payload = _request_json(config, "GET", endpoint)
        data = _envelope_data(status, payload, context=f"自建 dtk 任务 {task_id} 查询")
        last_state = str(data.get("state") or "")
        if last_state == "done":
            result = data.get("data")
            if not isinstance(result, dict):
                raise DtkError("自建 dtk 服务任务已完成，但没有返回解析结果数据。")
            return result
        if last_state == "failed":
            raw_error = data.get("error")
            error = raw_error if isinstance(raw_error, dict) else {}
            code = str(error.get("code") or "TASK_FAILED")
            message = str(error.get("message") or ("" if isinstance(raw_error, dict) else raw_error) or "无错误说明")
            raise DtkError(f"自建 dtk 服务解析任务失败（{code}）：{message}")
        if attempt < attempts - 1:
            time.sleep(TASK_POLL_INTERVAL_SECONDS)
    raise DtkError(
        f"自建 dtk 服务任务轮询超时：任务 {task_id} 在 {poll_max_seconds} 秒内未完成（最后状态：{last_state or '未知'}）。"
    )


def parse(
    url: str,
    *,
    config: dict[str, str] | None = None,
    wait_seconds: int = PARSE_WAIT_SECONDS,
    poll_max_seconds: int = TASK_POLL_MAX_SECONDS,
) -> dict[str, Any]:
    """POST /api/v1/parse?wait=25 解析作品；202/未完成时轮询任务接口，返回规范化 data。"""
    cfg = config if isinstance(config, dict) and config.get("base_url") and config.get("api_key") else load_config()
    endpoint = f"{cfg['base_url']}/api/v1/parse?wait={wait_seconds}"
    body = json.dumps({"url": url}, ensure_ascii=False).encode("utf-8")
    status, payload = _request_json(cfg, "POST", endpoint, body=body)
    data = _envelope_data(status, payload, context="自建 dtk 解析请求")
    nested = data.get("data")
    if str(data.get("state") or "") == "done" and isinstance(nested, dict):
        return nested
    if status == 202 or str(data.get("state") or "") in {"queued", "running"}:
        task_id = str(data.get("task_id") or "")
        if not task_id:
            raise DtkError("自建 dtk 服务返回 202 但缺少 task_id，无法轮询任务结果。")
        return _poll_task(cfg, task_id, poll_max_seconds=poll_max_seconds)
    return data


def sanitize_media_filename(value: str) -> str:
    """从 URL 路径或显式文件名生成安全的本地文件名。"""
    name = os.path.basename(str(value or "").strip())
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
    """下载媒体到 dest_dir，支持断点续传（Range: bytes=<已下载>-）；超出 max_bytes 时中止。"""
    if not url:
        raise DtkError("媒体下载失败：下载地址为空。")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / sanitize_media_filename(filename or urllib.parse.urlparse(url).path)
    existing = dest.stat().st_size if dest.exists() else 0
    request_headers = dict(headers or DOUYIN_DOWNLOAD_HEADERS)
    if existing > 0:
        request_headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(url, headers=request_headers, method="GET")

    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status == 206:
                mode, total = "ab", existing
            elif status == 200:
                mode, total = "wb", 0
            else:
                label = _HTTP_STATUS_LABELS.get(status, "")
                raise DtkError(f"媒体下载失败：HTTP {status} {label}".rstrip())
            with open(dest, mode) as output:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise DtkError(
                            f"媒体下载失败：已下载 {total} 字节，超过上限 {max_bytes} 字节，已停止（本地保留部分文件，可断点续传）。"
                        )
                    output.write(chunk)
    except urllib.error.HTTPError as exc:
        # urllib 对 4xx/5xx 一律抛 HTTPError（不会以 416 状态正常返回）：
        # 续传完成场景（本地已有完整文件 + Range: bytes=N- → 416）在此解释，本地文件视为完整。
        if int(exc.code) == 416:
            if existing > 0:
                return dest
            raise DtkError("媒体下载失败：HTTP 416 且本地没有可续传的部分文件。") from exc
        label = _HTTP_STATUS_LABELS.get(int(exc.code), "")
        raise DtkError(f"媒体下载失败：HTTP {exc.code} {label}".rstrip()) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DtkError(f"媒体下载失败：无法连接媒体地址（{_connection_reason(exc)}）。") from exc

    if total <= 0:
        dest.unlink(missing_ok=True)
        raise DtkError("媒体下载失败：下载结果为空。")
    return dest


def ffprobe_check(path: Path) -> dict[str, Any]:
    """ffprobe 校验媒体格式与时长；返回 {format_name, duration_seconds, size_bytes}。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise DtkError("未找到 ffprobe，无法校验媒体文件格式与时长。")
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
        raise DtkError(f"媒体校验失败：ffprobe 超过 {FFPROBE_TIMEOUT_SECONDS} 秒未返回。") from exc
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "").strip() or f"返回码 {result.returncode}"
        raise DtkError(f"媒体校验失败：ffprobe 无法识别该文件：{reason[-500:]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DtkError(f"媒体校验失败：ffprobe 输出不是 JSON：{exc!r}") from exc
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    format_name = str(fmt.get("format_name") or "").strip()
    if not format_name:
        raise DtkError("媒体校验失败：ffprobe 未返回格式信息，文件可能已损坏。")
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


def media_cache_dir(content_id: str) -> Path:
    """返回 ~/.cache/my-mind/media/douyin/<content_id>/；内容编号不合法时抛错防止路径逃逸。"""
    content_id = str(content_id or "").strip()
    if not content_id or not _SAFE_CONTENT_ID_RE.fullmatch(content_id):
        raise DtkError(f"内容编号不合法，无法定位媒体缓存目录：{content_id!r}。")
    return MEDIA_CACHE_ROOT / content_id


def cleanup_media(content_id: str) -> str:
    """删除 ~/.cache/my-mind/media/douyin/<content_id>/ 整目录；返回中文说明（不抛错）。"""
    try:
        target = media_cache_dir(content_id)
    except DtkError as exc:
        return str(exc)
    if not target.exists():
        return f"媒体缓存目录不存在，无需清理：{target}"
    shutil.rmtree(target, ignore_errors=True)
    if target.exists():
        return f"媒体缓存目录删除未完全成功，请手动检查：{target}"
    return f"已删除媒体缓存目录：{target}"
