from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".codex/skills/inbox-capture/scripts/env_check.py"
SAMPLES = ROOT / ".codex/skills/inbox-capture/references/samples.json"

EXPECTED_CHECK_NAMES = [
    "docker",
    "dtk",
    "xhs",
    "ffmpeg",
    "ffprobe",
    "dtk_config",
    "xhs_config",
]


def load_module():
    spec = importlib.util.spec_from_file_location("test_env_check_module", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def patch_all_pass(env, *, missing_binary: str | None = None):
    """返回一组 patcher：docker/ffmpeg/ffprobe/两个服务探活全部可控。"""
    def fake_which(name: str):
        if name == missing_binary:
            return None
        return f"/opt/homebrew/bin/{name}"

    docker_ok = subprocess.CompletedProcess(args=["docker", "info"], returncode=0)
    return [
        mock.patch.object(env.shutil, "which", side_effect=fake_which),
        mock.patch.object(env.subprocess, "run", return_value=docker_ok),
        mock.patch.object(
            env.urllib.request,
            "urlopen",
            return_value=FakeResponse(200),
        ),
    ]


class EnvCheckJudgementTest(unittest.TestCase):
    """检查项判定逻辑：全部 mock，无网络、无真实依赖。"""

    def test_health_check_timeout_constant_is_five_seconds(self) -> None:
        env = load_module()
        self.assertEqual(5, env.HEALTH_CHECK_TIMEOUT_SECONDS)

    def test_docker_passes_when_binary_and_daemon_available(self) -> None:
        env = load_module()
        with mock.patch.object(env.shutil, "which", return_value="/usr/local/bin/docker"), mock.patch.object(
            env.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["docker", "info"], returncode=0),
        ):
            passed, detail = env.check_docker()
        self.assertTrue(passed)
        self.assertIn("守护进程", detail)

    def test_docker_fails_without_binary(self) -> None:
        env = load_module()
        with mock.patch.object(env.shutil, "which", return_value=None):
            passed, detail = env.check_docker()
        self.assertFalse(passed)
        self.assertIn("未找到 docker", detail)

    def test_docker_fails_when_daemon_not_running(self) -> None:
        env = load_module()
        with mock.patch.object(env.shutil, "which", return_value="/usr/local/bin/docker"), mock.patch.object(
            env.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["docker", "info"], returncode=1),
        ):
            passed, detail = env.check_docker()
        self.assertFalse(passed)
        self.assertIn("守护进程未运行", detail)

    def test_docker_fails_on_timeout(self) -> None:
        env = load_module()
        with mock.patch.object(env.shutil, "which", return_value="/usr/local/bin/docker"), mock.patch.object(
            env.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="docker info", timeout=10),
        ):
            passed, detail = env.check_docker()
        self.assertFalse(passed)
        self.assertIn("超时", detail)

    def test_http_service_passes_on_success_with_five_second_timeout(self) -> None:
        env = load_module()
        with mock.patch.object(env.urllib.request, "urlopen", return_value=FakeResponse(200)) as urlopen:
            passed, detail = env.check_http_service("http://127.0.0.1:18600/")
        self.assertTrue(passed)
        self.assertIn("HTTP 200", detail)
        urlopen.assert_called_once_with("http://127.0.0.1:18600/", timeout=5)

    def test_http_service_passes_when_server_returns_http_error(self) -> None:
        env = load_module()
        error = urllib.error.HTTPError("http://127.0.0.1:18601/", 404, "Not Found", None, None)
        with mock.patch.object(env.urllib.request, "urlopen", side_effect=error):
            passed, detail = env.check_http_service("http://127.0.0.1:18601/")
        self.assertTrue(passed)
        self.assertIn("HTTP 404", detail)

    def test_http_service_fails_on_connection_refused(self) -> None:
        env = load_module()
        error = urllib.error.URLError(OSError(61, "Connection refused"))
        with mock.patch.object(env.urllib.request, "urlopen", side_effect=error):
            passed, detail = env.check_http_service("http://127.0.0.1:18600/")
        self.assertFalse(passed)
        self.assertIn("无法连接", detail)

    def test_http_service_fails_on_timeout(self) -> None:
        env = load_module()
        with mock.patch.object(env.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            passed, detail = env.check_http_service("http://127.0.0.1:18601/")
        self.assertFalse(passed)
        self.assertIn("超时", detail)

    def test_config_file_missing_fails(self) -> None:
        env = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            passed, detail = env.check_config_file(Path(temp_dir) / "dtk.json")
        self.assertFalse(passed)
        self.assertIn("不存在", detail)

    def test_config_file_invalid_json_fails(self) -> None:
        env = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dtk.json"
            path.write_text("{not-json", encoding="utf-8")
            passed, detail = env.check_config_file(path)
        self.assertFalse(passed)
        self.assertIn("不是合法 JSON", detail)

    def test_config_file_valid_json_passes_without_leaking_values(self) -> None:
        env = load_module()
        secret = "sk-SECRET-TOKEN-do-not-leak-0001"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "xhs.json"
            path.write_text(json.dumps({"token": secret}), encoding="utf-8")
            passed, detail = env.check_config_file(path)
        self.assertTrue(passed)
        self.assertIn("存在且可解析", detail)
        self.assertNotIn(secret, detail)


class EnvCheckRunTest(unittest.TestCase):
    """run_checks 汇总与 --skip-network 行为。"""

    def test_run_checks_all_pass(self) -> None:
        env = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            for name in ("dtk.json", "xhs.json"):
                (config_dir / name).write_text("{}", encoding="utf-8")
            patchers = patch_all_pass(env)
            with contextlib.ExitStack() as stack:
                for patcher in patchers:
                    stack.enter_context(patcher)
                results = env.run_checks(config_dir=config_dir)
        self.assertEqual(EXPECTED_CHECK_NAMES, [item["name"] for item in results])
        self.assertTrue(all(item["passed"] for item in results))
        self.assertEqual({"total": 7, "passed": 7, "failed": 0, "skipped": 0}, env.summarize(results))

    def test_run_checks_failure_is_reported(self) -> None:
        env = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            for name in ("dtk.json", "xhs.json"):
                (config_dir / name).write_text("{}", encoding="utf-8")
            patchers = patch_all_pass(env, missing_binary="ffmpeg")
            with contextlib.ExitStack() as stack:
                for patcher in patchers:
                    stack.enter_context(patcher)
                results = env.run_checks(config_dir=config_dir)
        failed = [item for item in results if not item["passed"]]
        self.assertEqual(["ffmpeg"], [item["name"] for item in failed])
        self.assertEqual(1, env.summarize(results)["failed"])

    def test_skip_network_skips_service_probes_without_failure(self) -> None:
        env = load_module()
        probe = mock.Mock(side_effect=AssertionError("离线模式不应发起服务探活"))
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            for name in ("dtk.json", "xhs.json"):
                (config_dir / name).write_text("{}", encoding="utf-8")
            with mock.patch.object(env.shutil, "which", return_value="/opt/homebrew/bin/tool"), mock.patch.object(
                env.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=["docker", "info"], returncode=0),
            ), mock.patch.object(env, "check_http_service", probe):
                results = env.run_checks(skip_network=True, config_dir=config_dir)
        probe.assert_not_called()
        skipped = {item["name"] for item in results if item["skipped"]}
        self.assertEqual({"dtk", "xhs"}, skipped)
        self.assertEqual(0, env.summarize(results)["failed"])

    def test_config_service_url_reads_base_url_with_fallbacks(self) -> None:
        """L9：探活地址优先取配置 base_url；缺失/非法/非 http(s) 时回退默认。"""
        env = load_module()
        default = "http://127.0.0.1:18600/"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dtk.json"
            path.write_text(json.dumps({"base_url": "http://127.0.0.1:28600/"}), encoding="utf-8")
            self.assertEqual("http://127.0.0.1:28600/", env.config_service_url(path, default))
            path.write_text(json.dumps({"base_url": "http://127.0.0.1:28600"}), encoding="utf-8")
            self.assertEqual("http://127.0.0.1:28600/", env.config_service_url(path, default))
            path.write_text(json.dumps({"base_url": "ftp://bad-scheme"}), encoding="utf-8")
            self.assertEqual(default, env.config_service_url(path, default))
            path.write_text(json.dumps({"bearer_token": "abc"}), encoding="utf-8")
            self.assertEqual(default, env.config_service_url(path, default))
            path.write_text("{not-json", encoding="utf-8")
            self.assertEqual(default, env.config_service_url(path, default))
            path.unlink()
            self.assertEqual(default, env.config_service_url(path, default))

    def test_probe_uses_configured_base_url_when_present(self) -> None:
        env = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            (config_dir / "dtk.json").write_text(json.dumps({"base_url": "http://127.0.0.1:28600"}), encoding="utf-8")
            (config_dir / "xhs.json").write_text(json.dumps({"base_url": "http://127.0.0.1:28601/"}), encoding="utf-8")
            patchers = patch_all_pass(env)
            with contextlib.ExitStack() as stack:
                for patcher in patchers:
                    stack.enter_context(patcher)
                with mock.patch.object(env, "check_http_service", wraps=env.check_http_service) as probe:
                    results = env.run_checks(config_dir=config_dir)
        self.assertEqual(
            ["http://127.0.0.1:28600/", "http://127.0.0.1:28601/"],
            [call.args[0] for call in probe.call_args_list],
        )
        self.assertTrue(all(item["passed"] for item in results if item["name"] in {"dtk", "xhs"}))

    def test_probe_falls_back_to_default_ports_without_valid_base_url(self) -> None:
        env = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            (config_dir / "dtk.json").write_text("{not-json", encoding="utf-8")  # 非法 JSON → 默认端口
            (config_dir / "xhs.json").write_text(json.dumps({"bearer_token": "abc"}), encoding="utf-8")  # 缺 base_url → 默认端口
            patchers = patch_all_pass(env)
            with contextlib.ExitStack() as stack:
                for patcher in patchers:
                    stack.enter_context(patcher)
                with mock.patch.object(env, "check_http_service", wraps=env.check_http_service) as probe:
                    env.run_checks(config_dir=config_dir)
        self.assertEqual(
            ["http://127.0.0.1:18600/", "http://127.0.0.1:18601/"],
            [call.args[0] for call in probe.call_args_list],
        )


class EnvCheckCliTest(unittest.TestCase):
    """命令行输出结构、密钥脱敏与退出码。"""

    def run_cli(self, argv: list[str], config_dir: Path) -> tuple[int, str]:
        env = load_module()
        stdout = io.StringIO()
        patchers = patch_all_pass(env)
        patchers.append(mock.patch.object(env, "DEFAULT_CONFIG_DIR", config_dir))
        with contextlib.ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            with contextlib.redirect_stdout(stdout):
                code = env.main(argv)
        return code, stdout.getvalue()

    def test_json_output_structure_when_all_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            for name in ("dtk.json", "xhs.json"):
                (config_dir / name).write_text("{}", encoding="utf-8")
            code, output = self.run_cli(["--json"], config_dir)
        self.assertEqual(0, code)
        payload = json.loads(output)
        self.assertIn("ok", payload)
        self.assertTrue(payload["ok"])
        self.assertIn("summary", payload)
        self.assertEqual({"total": 7, "passed": 7, "failed": 0, "skipped": 0}, payload["summary"])
        self.assertEqual(EXPECTED_CHECK_NAMES, [item["name"] for item in payload["checks"]])
        for item in payload["checks"]:
            self.assertEqual({"name", "label", "passed", "skipped", "detail"}, set(item.keys()))

    def test_human_output_is_chinese_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            for name in ("dtk.json", "xhs.json"):
                (config_dir / name).write_text("{}", encoding="utf-8")
            code, output = self.run_cli([], config_dir)
        self.assertEqual(0, code)
        self.assertIn("采集环境自检", output)
        self.assertIn("[通过]", output)
        self.assertIn("结论：采集环境检查全部通过。", output)

    def test_exit_code_one_when_any_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)  # 不写入配置文件，制造失败项
            code, output = self.run_cli(["--json"], config_dir)
        self.assertEqual(1, code)
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertGreaterEqual(payload["summary"]["failed"], 2)

    def test_exit_code_zero_with_skip_network(self) -> None:
        env = load_module()
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            for name in ("dtk.json", "xhs.json"):
                (config_dir / name).write_text("{}", encoding="utf-8")
            with mock.patch.object(env.shutil, "which", return_value="/opt/homebrew/bin/tool"), mock.patch.object(
                env.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=["docker", "info"], returncode=0),
            ), mock.patch.object(env.urllib.request, "urlopen") as urlopen, mock.patch.object(
                env,
                "DEFAULT_CONFIG_DIR",
                config_dir,
            ), contextlib.redirect_stdout(stdout):
                code = env.main(["--json", "--skip-network"])
        urlopen.assert_not_called()
        self.assertEqual(0, code)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(2, payload["summary"]["skipped"])

    def test_secret_values_never_leak_into_any_output(self) -> None:
        env = load_module()
        secret_dtk = "sk-DTK-SECRET-a1b2c3d4e5f6"
        secret_xhs = "xhs-cookie-SECRET-z9y8x7w6v5u4"
        outputs = []
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            (config_dir / "dtk.json").write_text(
                json.dumps({"cookie": secret_dtk, "aigsid": "SECRET-aigsid-123456"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (config_dir / "xhs.json").write_text(
                json.dumps({"authorization": secret_xhs}, ensure_ascii=False),
                encoding="utf-8",
            )
            patchers = patch_all_pass(env)
            patchers.append(mock.patch.object(env, "DEFAULT_CONFIG_DIR", config_dir))
            with contextlib.ExitStack() as stack:
                for patcher in patchers:
                    stack.enter_context(patcher)
                for argv in ([], ["--json"]):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        env.main(argv)
                    outputs.append(stdout.getvalue())
        for output in outputs:
            self.assertNotIn(secret_dtk, output)
            self.assertNotIn(secret_xhs, output)
            self.assertNotIn("SECRET-aigsid-123456", output)


class SamplesRegistryTest(unittest.TestCase):
    """samples.json 登记清单的 schema 校验。"""

    def test_samples_registry_has_18_valid_entries(self) -> None:
        data = json.loads(SAMPLES.read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertEqual(18, len(data))
        required = {
            "sample_id",
            "platform",
            "url",
            "content_id",
            "expect_type",
            "source",
            "registered_at",
            "last_verified_at",
            "evidence_path",
            "notes",
        }
        ids = [item["sample_id"] for item in data]
        self.assertEqual(18, len(set(ids)))
        for item in data:
            self.assertEqual(required, set(item.keys()))
            self.assertIn(item["platform"], {"douyin", "xhs"})
            self.assertIn(item["expect_type"], {"图文", "短视频", "长视频", "UI演示", "未知"})
            self.assertIn(item["source"], {"issue#1已提供", "用户提供"})
            self.assertEqual("2026-11-17", item["registered_at"])
            # 内容编号回填：抖音从 URL /video/ 提取，小红书取实测笔记的 内容编号；找不到的允许为 null。
            self.assertTrue(
                item["content_id"] is None
                or (isinstance(item["content_id"], str) and item["content_id"].strip())
            )
            # 登记时为 null，验收实测后回填日期与证据路径（见 85_运行记录/采集能力升级-*实测）。
            self.assertTrue(
                item["last_verified_at"] is None
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["last_verified_at"])
            )
            self.assertTrue(
                item["evidence_path"] is None
                or (isinstance(item["evidence_path"], str) and item["evidence_path"].startswith("85_运行记录/"))
            )

    def test_samples_registry_platform_split(self) -> None:
        data = json.loads(SAMPLES.read_text(encoding="utf-8"))
        douyin = [item for item in data if item["platform"] == "douyin"]
        xhs = [item for item in data if item["platform"] == "xhs"]
        self.assertEqual(11, len(douyin))
        self.assertEqual(7, len(xhs))
        for item in xhs:
            self.assertEqual("未知", item["expect_type"])
            self.assertEqual("用户提供", item["source"])
        self.assertEqual(
            "https://www.douyin.com/video/7708714176931188002",
            next(item["url"] for item in douyin if item["sample_id"] == "douyin-01"),
        )

    def test_samples_registry_content_ids_backfilled(self) -> None:
        """L8 回归：抖音 content_id 与 URL /video/ 后的编号一致；小红书 content_id 回填自实测笔记的 内容编号。"""
        data = json.loads(SAMPLES.read_text(encoding="utf-8"))
        for item in data:
            if item["platform"] == "douyin":
                expected = item["url"].rsplit("/video/", 1)[-1].split("?")[0].strip("/")
                self.assertEqual(expected, item["content_id"], item["sample_id"])
        xhs_ids = {item["sample_id"]: item["content_id"] for item in data if item["platform"] == "xhs"}
        self.assertEqual(7, len([value for value in xhs_ids.values() if value]))
        # 抽查 2026-11-17 复测笔记回填的 内容编号（00_收件箱/2026-11-17 小红书 - *）。
        self.assertEqual("6afbb178000000002601061c", xhs_ids["xhs-01"])
        self.assertEqual("6ae9fad4000000000e036489", xhs_ids["xhs-07"])


if __name__ == "__main__":
    unittest.main()
