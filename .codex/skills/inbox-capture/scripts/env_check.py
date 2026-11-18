#!/usr/bin/env python3
"""采集环境自检：检查 docker、本机采集服务、ffmpeg、采集配置与视觉解析后端。

仅使用标准库（视觉配置解析复用同目录 keyframe_vision.load_vision_config，保证缺省语义一致）。
输出不包含任何密钥/token 明文：配置文件只报告存在性与可解析性，不外显字段值
（视觉后端 glm 的 api_key 只报「已配置」，不显示脱敏尾号，对齐 dtk/xhs 惯例）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


# L4：视觉配置解析直接复用 keyframe_vision 的 load_vision_config（含默认值/夹取/损坏报错），
# 消除两套解析的缺省语义分叉（缺 backend 字段=默认 local，不算失败）。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from keyframe_vision import VisionConfigError, load_vision_config  # noqa: E402


TZ = ZoneInfo("Asia/Shanghai")
HEALTH_CHECK_TIMEOUT_SECONDS = 5
DOCKER_INFO_TIMEOUT_SECONDS = 10
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "my-mind"
SERVICE_URLS = {
    "dtk": "http://127.0.0.1:18600/",
    "xhs": "http://127.0.0.1:18601/",
}
SERVICE_LABELS = {
    "dtk": "dtk 服务（抖音解析/下载，127.0.0.1:18600）",
    "xhs": "XHS-Downloader 服务（小红书下载/兜底，127.0.0.1:18601）",
}
DEFAULT_VISION_LOCAL_BASE_URL = "http://127.0.0.1:8304"
VISION_CHECK_LABEL = "视觉解析后端（~/.config/my-mind/vision.json，画面解析探活）"
RESULT_KEYS = ("name", "label", "passed", "skipped", "detail")


def check_docker() -> tuple[bool, str]:
    """检查 docker 命令与守护进程是否可用。"""
    if shutil.which("docker") is None:
        return False, "未找到 docker 命令，请先安装 Docker Desktop"
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=DOCKER_INFO_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return False, "docker 命令无法执行"
    except subprocess.TimeoutExpired:
        return False, f"docker info 超时（超过 {DOCKER_INFO_TIMEOUT_SECONDS} 秒）"
    if proc.returncode == 0:
        return True, "docker 命令可用，守护进程已运行"
    return False, "docker 守护进程未运行，请启动 Docker Desktop 后重试"


def check_binary(name: str) -> tuple[bool, str]:
    """检查命令行工具是否已安装。"""
    resolved = shutil.which(name)
    if resolved is None:
        return False, f"未找到 {name}，请通过 brew install {name} 安装"
    return True, f"{name} 已安装（{resolved}）"


def check_http_service(url: str, timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """探活本机 HTTP 服务：有任何 HTTP 响应即视为健康（含 4xx）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = response.status
        return True, f"服务有响应（HTTP {status}）"
    except urllib.error.HTTPError as error:
        return True, f"服务有响应（HTTP {error.code}）"
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", None)
        detail = f"{reason}" if reason else "连接失败"
        return False, f"无法连接（{detail}）"
    except TimeoutError:
        return False, f"连接超时（超过 {HEALTH_CHECK_TIMEOUT_SECONDS} 秒）"


def check_config_file(path: Path) -> tuple[bool, str]:
    """检查凭据配置文件是否存在且可解析；只报告存在性，不输出任何字段值。"""
    if not path.is_file():
        return False, f"配置文件不存在：{path}"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return False, f"配置文件无法读取：{path}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False, f"配置文件存在但不是合法 JSON：{path.name}"
    if isinstance(data, dict):
        return True, f"配置文件存在且可解析（{len(data)} 个顶层字段，值不外显）"
    return True, "配置文件存在且可解析"


def config_service_url(path: Path, default_url: str) -> str:
    """探活地址优先取配置文件里的合法 base_url；文件缺失/不可解析/缺 base_url 时回退默认端口地址。

    只用于本机探活请求，地址值不进入任何输出；配置字段值仍不外显。
    """
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return default_url
    if not isinstance(data, dict):
        return default_url
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return default_url
    return f"{base_url}/"


def mask_secret(value: str) -> str:
    """密钥脱敏：只保留末 4 位；过短时整体打码，不外显完整值。"""
    text = str(value).strip()
    if len(text) <= 4:
        return "****"
    return f"****{text[-4:]}"


def vision_local_base_url(config: dict[str, Any]) -> str:
    """从 load_vision_config 结果取 local 探活 base_url（已带默认值）；防御缺失时回退默认 8304。"""
    local = config.get("local")
    base_url = str(local.get("base_url") or "").strip().rstrip("/") if isinstance(local, dict) else ""
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return DEFAULT_VISION_LOCAL_BASE_URL
    return base_url


def check_vision_backend(config_path: Path, *, skip_network: bool = False) -> dict[str, Any]:
    """视觉解析后端探活。

    - vision.json 不存在：视为功能未启用，跳过且不算失败（输出「未配置（画面解析功能未启用）」）。
    - 配置解析复用 keyframe_vision.load_vision_config（L4）：缺 backend 字段=默认 local，不算失败。
    - backend=local：GET {local.base_url}/health（默认 http://127.0.0.1:8304），5 秒超时；
      --skip-network 下跳过探活（与 dtk/xhs 探活语义一致）；服务不可达计为失败。
    - backend=glm：只检查 glm.api_key 字段存在性，不发任何外网请求；只报「已配置」，
      不显示脱敏尾号（L5，对齐 dtk/xhs 只报存在性的惯例）。
    """
    if not config_path.is_file():
        return build_check("vision", VISION_CHECK_LABEL, True, "未配置（画面解析功能未启用）", skipped=True)
    try:
        config = load_vision_config(config_path)
    except VisionConfigError as exc:
        return build_check("vision", VISION_CHECK_LABEL, False, str(exc))
    except OSError as exc:
        return build_check("vision", VISION_CHECK_LABEL, False, f"配置文件无法读取：{exc}")

    backend = str(config.get("backend") or "").strip().lower()
    if backend == "local":
        if skip_network:
            return build_check("vision", VISION_CHECK_LABEL, True, "离线模式：已跳过视觉服务探活", skipped=True)
        base_url = vision_local_base_url(config)
        passed, detail = check_http_service(f"{base_url}/health")
        if not passed:
            detail = f"后端 local：探活 {base_url}/health {detail}；请先启动专用 llama-server（见 README「采集环境依赖-视觉解析服务」）"
        else:
            detail = f"后端 local：探活 {base_url}/health {detail}"
        return build_check("vision", VISION_CHECK_LABEL, passed, detail)
    if backend == "glm":
        glm = config.get("glm")
        api_key = glm.get("api_key") if isinstance(glm, dict) else None
        if isinstance(api_key, str) and api_key.strip():
            return build_check("vision", VISION_CHECK_LABEL, True, "后端 glm：api_key 已配置，仅检查存在性，未发外网请求")
        return build_check("vision", VISION_CHECK_LABEL, False, "后端 glm：glm.api_key 缺失或为空，请补齐后重试")
    return build_check("vision", VISION_CHECK_LABEL, False, f"backend 字段不识别（{backend or '空'}，仅支持 local/glm）")


def build_check(name: str, label: str, passed: bool, detail: str, *, skipped: bool = False) -> dict[str, Any]:
    return {"name": name, "label": label, "passed": passed, "skipped": skipped, "detail": detail}


def run_checks(
    *,
    skip_network: bool = False,
    config_dir: Path | str | None = None,
    service_urls: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """执行全部检查项，返回结果列表。skip_network=True 时跳过本机服务探活（含视觉服务 8304）。"""
    resolved_config_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    urls = dict(service_urls) if service_urls else dict(SERVICE_URLS)
    results: list[dict[str, Any]] = []

    passed, detail = check_docker()
    results.append(build_check("docker", "Docker（dtk compose 容器运行时）", passed, detail))

    for key in ("dtk", "xhs"):
        if skip_network:
            results.append(
                build_check(key, SERVICE_LABELS[key], True, "离线模式：已跳过本机服务探活", skipped=True)
            )
            continue
        url = urls.get(key, SERVICE_URLS[key])
        if service_urls is None or key not in service_urls:
            # 未显式指定探活地址时读配置：配置里含合法 base_url 就探配置值，否则用默认 18600/18601。
            url = config_service_url(resolved_config_dir / f"{key}.json", SERVICE_URLS[key])
        passed, detail = check_http_service(url)
        results.append(build_check(key, SERVICE_LABELS[key], passed, detail))

    for binary in ("ffmpeg", "ffprobe"):
        passed, detail = check_binary(binary)
        results.append(build_check(binary, f"{binary}（音视频处理）", passed, detail))

    for key in ("dtk", "xhs"):
        passed, detail = check_config_file(resolved_config_dir / f"{key}.json")
        results.append(
            build_check(
                f"{key}_config",
                f"~/.config/my-mind/{key}.json（凭据配置，仅检查存在性）",
                passed,
                detail,
            )
        )

    results.append(check_vision_backend(resolved_config_dir / "vision.json", skip_network=skip_network))

    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    """汇总通过/失败/跳过数量；跳过项不计入失败。"""
    failed = [item for item in results if not item["passed"] and not item["skipped"]]
    skipped = [item for item in results if item["skipped"]]
    passed = [item for item in results if item["passed"] and not item["skipped"]]
    return {"total": len(results), "passed": len(passed), "failed": len(failed), "skipped": len(skipped)}


def render_human(results: list[dict[str, Any]]) -> str:
    """渲染人类可读的中文报告。"""
    lines = ["采集环境自检："]
    for item in results:
        if item["skipped"]:
            mark = "跳过"
        elif item["passed"]:
            mark = "通过"
        else:
            mark = "失败"
        lines.append(f"[{mark}] {item['label']}：{item['detail']}")
    summary = summarize(results)
    lines.append(
        "汇总："
        f"通过 {summary['passed']}/{summary['total']} 项，"
        f"失败 {summary['failed']} 项，跳过 {summary['skipped']} 项"
    )
    if summary["failed"]:
        lines.append("结论：存在未通过项，请按上方提示补齐依赖后重试。")
    else:
        lines.append("结论：采集环境检查全部通过。")
    return "\n".join(lines)


def render_json(results: list[dict[str, Any]]) -> str:
    """渲染结构化 JSON 结果。"""
    summary = summarize(results)
    payload: dict[str, Any] = {
        "ok": summary["failed"] == 0,
        "checked_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "summary": summary,
        "checks": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集环境自检：检查 docker、本机服务健康、ffmpeg、采集配置与视觉解析后端")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON 结果（默认输出人类可读中文报告）")
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="离线模式：跳过 18600/18601 与视觉服务（8304）的本机探活，仅检查本地依赖",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = run_checks(skip_network=args.skip_network)
    if args.json:
        print(render_json(results))
    else:
        print(render_human(results))
    summary = summarize(results)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
