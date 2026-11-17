from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".codex/skills/inbox-capture/scripts"
DTK_SCRIPT = SCRIPTS / "dtk_client.py"
CAPTURE_SCRIPT = SCRIPTS / "capture_link.py"

API_KEY = "dtk-secret-key-0123456789"
CONFIG = {"base_url": "http://127.0.0.1:8666", "api_key": API_KEY}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: object = None, status: int = 200, raw: bytes | None = None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._stream = io.BytesIO(raw)
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class DtkClientConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dtk = load_module(DTK_SCRIPT, "test_dtk_client_config_module")

    def test_load_config_reads_json_and_normalizes_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dtk.json"
            path.write_text(json.dumps({"base_url": "http://127.0.0.1:8000/", "api_key": "abc"}), encoding="utf-8")
            config = self.dtk.load_config(path)
        self.assertEqual({"base_url": "http://127.0.0.1:8000", "api_key": "abc"}, config)

    def test_missing_config_file_is_unavailable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.load_config(Path(temp_dir) / "nope.json")
        self.assertTrue(ctx.exception.unavailable)
        self.assertIn("未找到 dtk 配置文件", str(ctx.exception))

    def test_config_missing_keys_is_unavailable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dtk.json"
            path.write_text(json.dumps({"base_url": "http://127.0.0.1:8000"}), encoding="utf-8")
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.load_config(path)
        self.assertTrue(ctx.exception.unavailable)
        self.assertIn("缺少 base_url 或 api_key", str(ctx.exception))


class DtkClientParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dtk = load_module(DTK_SCRIPT, "test_dtk_client_parse_module")

    def test_parse_success_returns_data_with_auth_header(self) -> None:
        result = {"kind": "video", "content_id": "7708714176931188002"}
        with mock.patch.object(self.dtk.urllib.request, "urlopen", return_value=FakeResponse({"success": True, "data": result})) as urlopen:
            data = self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG)
        self.assertEqual(result, data)
        request = urlopen.call_args.args[0]
        self.assertEqual("POST", request.method)
        self.assertIn("/api/v1/parse?wait=25", request.full_url)
        self.assertEqual(json.dumps({"url": "https://v.douyin.com/abc/"}, ensure_ascii=False).encode("utf-8"), request.data)
        self.assertTrue(any(value == API_KEY for value in request.headers.values()))

    def test_parse_202_polls_task_until_done(self) -> None:
        responses = [
            FakeResponse({"success": True, "data": {"task_id": "t-1", "state": "running"}}, status=202),
            FakeResponse({"success": True, "data": {"task_id": "t-1", "state": "running"}}),
            FakeResponse({"success": True, "data": {"task_id": "t-1", "state": "done", "data": {"kind": "image_album"}}}),
        ]
        with mock.patch.object(self.dtk.urllib.request, "urlopen", side_effect=responses) as urlopen, mock.patch.object(self.dtk, "time") as fake_time:
            data = self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG)
        self.assertEqual({"kind": "image_album"}, data)
        self.assertEqual(3, urlopen.call_count)
        self.assertIn("/api/v1/tasks/t-1", urlopen.call_args_list[1].args[0].full_url)
        fake_time.sleep.assert_called()

    def test_parse_task_failed_reports_code_and_message(self) -> None:
        responses = [
            FakeResponse({"success": True, "data": {"task_id": "t-2", "state": "queued"}}, status=202),
            FakeResponse(
                {
                    "success": True,
                    "data": {
                        "task_id": "t-2",
                        "state": "failed",
                        "error": {"code": "UPSTREAM_ERROR", "message": "cookie 失效"},
                    },
                }
            ),
        ]
        with mock.patch.object(self.dtk.urllib.request, "urlopen", side_effect=responses), mock.patch.object(self.dtk, "time"):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG)
        message = str(ctx.exception)
        self.assertIn("UPSTREAM_ERROR", message)
        self.assertIn("cookie 失效", message)
        self.assertFalse(ctx.exception.unavailable)

    def test_parse_envelope_error_raises_with_code(self) -> None:
        payload = {"success": False, "data": None, "error": {"code": "INVALID_URL", "message": "链接不支持"}}
        with mock.patch.object(self.dtk.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG)
        self.assertIn("INVALID_URL", str(ctx.exception))
        self.assertIn("链接不支持", str(ctx.exception))

    def test_parse_http_error_status_is_traceable(self) -> None:
        error_body = json.dumps({"success": False, "data": None, "error": {"code": "UNAUTHORIZED", "message": "key 无效"}}).encode("utf-8")
        http_error = urllib.error.HTTPError("http://127.0.0.1:8666/api/v1/parse", 401, "Unauthorized", None, io.BytesIO(error_body))
        with mock.patch.object(self.dtk.urllib.request, "urlopen", side_effect=http_error):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG)
        message = str(ctx.exception)
        self.assertIn("401", message)
        self.assertIn("API Key 无效", message)
        self.assertIn("UNAUTHORIZED", message)

    def test_parse_poll_timeout(self) -> None:
        running = FakeResponse({"success": True, "data": {"task_id": "t-3", "state": "running"}})
        with mock.patch.object(self.dtk.urllib.request, "urlopen", side_effect=lambda *a, **k: FakeResponse({"success": True, "data": {"task_id": "t-3", "state": "running"}})):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG, poll_max_seconds=0)
        self.assertIn("轮询超时", str(ctx.exception))
        self.assertIn("t-3", str(ctx.exception))
        del running

    def test_connection_refused_is_unavailable(self) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        with mock.patch.object(self.dtk.urllib.request, "urlopen", side_effect=refused):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG)
        self.assertTrue(ctx.exception.unavailable)
        self.assertIn("无法连接自建 dtk 服务", str(ctx.exception))

    def test_error_message_never_leaks_api_key(self) -> None:
        echo_body = json.dumps(
            {"success": False, "data": None, "error": {"code": "BOOM", "message": f"rejected {API_KEY}"}},
            ensure_ascii=False,
        ).encode("utf-8")
        with mock.patch.object(self.dtk.urllib.request, "urlopen", return_value=FakeResponse(raw=echo_body)):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.parse("https://v.douyin.com/abc/", config=CONFIG)
        self.assertNotIn(API_KEY, str(ctx.exception))
        self.assertNotIn(API_KEY[:12], str(ctx.exception))
        self.assertIn("***", str(ctx.exception))


class DtkClientDownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dtk = load_module(DTK_SCRIPT, "test_dtk_client_download_module")

    def test_fresh_download_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "douyin" / "123"
            payload = b"fake-image-bytes-0123456789"
            with mock.patch.object(self.dtk.urllib.request, "urlopen", return_value=FakeResponse(raw=payload)):
                path = self.dtk.download_media("https://cdn.douyin.test/img.jpeg", dest_dir, filename="douyin-image-01.jpeg")
            self.assertEqual(payload, path.read_bytes())
            self.assertEqual("douyin-image-01.jpeg", path.name)

    def test_resume_uses_range_header_and_appends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "douyin" / "123"
            dest_dir.mkdir(parents=True)
            dest = dest_dir / "douyin-image-01.jpeg"
            dest.write_bytes(b"hello")
            with mock.patch.object(self.dtk.urllib.request, "urlopen", return_value=FakeResponse(raw=b" world", status=206)) as urlopen:
                path = self.dtk.download_media("https://cdn.douyin.test/img.jpeg", dest_dir, filename="douyin-image-01.jpeg")
            self.assertEqual(b"hello world", path.read_bytes())
            request = urlopen.call_args.args[0]
            self.assertEqual("bytes=5-", request.headers.get("Range"))

    def test_server_ignoring_range_restarts_from_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "douyin" / "123"
            dest_dir.mkdir(parents=True)
            (dest_dir / "douyin-image-01.jpeg").write_bytes(b"old-content")
            with mock.patch.object(self.dtk.urllib.request, "urlopen", return_value=FakeResponse(raw=b"new")):
                path = self.dtk.download_media("https://cdn.douyin.test/img.jpeg", dest_dir, filename="douyin-image-01.jpeg")
            self.assertEqual(b"new", path.read_bytes())

    def test_http_416_with_existing_part_keeps_file(self) -> None:
        # urllib 对 4xx 一律抛 HTTPError，续传完成（本地已是完整文件）场景在异常分支解释为成功。
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "douyin" / "123"
            dest_dir.mkdir(parents=True)
            dest = dest_dir / "video.mp4"
            dest.write_bytes(b"complete-media")
            error = urllib.error.HTTPError("https://cdn.douyin.test/video.mp4", 416, "Range Not Satisfiable", None, io.BytesIO(b""))
            with mock.patch.object(self.dtk.urllib.request, "urlopen", side_effect=error) as urlopen:
                path = self.dtk.download_media("https://cdn.douyin.test/video.mp4", dest_dir, filename="video.mp4")
            self.assertEqual(dest, path)
            self.assertEqual(b"complete-media", path.read_bytes())
            self.assertEqual("bytes=14-", urlopen.call_args.args[0].headers.get("Range"))

    def test_http_416_without_existing_part_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "douyin" / "123"
            error = urllib.error.HTTPError("https://cdn.douyin.test/video.mp4", 416, "Range Not Satisfiable", None, io.BytesIO(b""))
            with mock.patch.object(self.dtk.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(self.dtk.DtkError) as ctx:
                    self.dtk.download_media("https://cdn.douyin.test/video.mp4", dest_dir, filename="video.mp4")
            self.assertIn("416", str(ctx.exception))
            self.assertIn("没有可续传的部分文件", str(ctx.exception))
            self.assertFalse(list(dest_dir.iterdir()))

    def test_download_over_max_bytes_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "douyin" / "123"
            with mock.patch.object(self.dtk.urllib.request, "urlopen", return_value=FakeResponse(raw=b"x" * 64)):
                with self.assertRaises(self.dtk.DtkError) as ctx:
                    self.dtk.download_media("https://cdn.douyin.test/img.jpeg", dest_dir, max_bytes=16, filename="douyin-image-01.jpeg")
            self.assertIn("超过上限", str(ctx.exception))


class DtkClientFfprobeCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dtk = load_module(DTK_SCRIPT, "test_dtk_client_probe_module")

    def test_ffprobe_check_returns_format_and_duration(self) -> None:
        probe_json = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "61.5", "size": "55296"}}
        with mock.patch.object(self.dtk.shutil, "which", return_value="/usr/bin/ffprobe"), mock.patch.object(
            self.dtk.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(probe_json), ""),
        ) as run:
            result = self.dtk.ffprobe_check(Path("/tmp/fake.mp4"))
        self.assertEqual("mov,mp4,m4a,3gp,3g2,mj2", result["format_name"])
        self.assertEqual(61.5, result["duration_seconds"])
        self.assertEqual(55296, result["size_bytes"])
        self.assertIn("-show_format", run.call_args.args[0])

    def test_ffprobe_missing_binary_raises_chinese_error(self) -> None:
        with mock.patch.object(self.dtk.shutil, "which", return_value=None):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.ffprobe_check(Path("/tmp/fake.mp4"))
        self.assertIn("未找到 ffprobe", str(ctx.exception))

    def test_ffprobe_bad_file_raises_error(self) -> None:
        with mock.patch.object(self.dtk.shutil, "which", return_value="/usr/bin/ffprobe"), mock.patch.object(
            self.dtk.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", "Invalid data found when processing input"),
        ):
            with self.assertRaises(self.dtk.DtkError) as ctx:
                self.dtk.ffprobe_check(Path("/tmp/fake.mp4"))
        self.assertIn("媒体校验失败", str(ctx.exception))
        self.assertIn("Invalid data", str(ctx.exception))

    def test_cleanup_media_removes_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(self.dtk, "MEDIA_CACHE_ROOT", Path(temp_dir)):
                target = Path(temp_dir) / "7708714176931188002"
                (target / "sub").mkdir(parents=True)
                (target / "sub" / "video.mp4").write_bytes(b"x" * 16)
                message = self.dtk.cleanup_media("7708714176931188002")
                self.assertFalse(target.exists())
                self.assertIn("已删除", message)

    def test_cleanup_media_rejects_unsafe_content_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "safe").mkdir()
            with mock.patch.object(self.dtk, "MEDIA_CACHE_ROOT", root):
                message = self.dtk.cleanup_media("../../etc")
            self.assertIn("内容编号不合法", message)
            self.assertTrue((root / "safe").exists())

    def test_cleanup_media_missing_directory_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(self.dtk, "MEDIA_CACHE_ROOT", Path(temp_dir)):
                message = self.dtk.cleanup_media("404")
            self.assertIn("无需清理", message)


class FakeDtkError(Exception):
    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        super().__init__(message)
        self.unavailable = unavailable


class FakeDtkClient:
    """替换 capture_link.dtk_client 的假客户端：不发真实网络请求。"""

    DtkError = FakeDtkError

    def __init__(self, parse_result: dict | None = None, parse_error: Exception | None = None, fail_image_urls: set[str] | None = None):
        self.parse_result = parse_result or {}
        self.parse_error = parse_error
        self.fail_image_urls = fail_image_urls or set()
        self.cache_root = Path(tempfile.mkdtemp(prefix="my-mind-dtk-test-"))
        self.downloaded: list[str] = []
        self.probed: list[Path] = []
        self.cleaned: list[str] = []

    def parse(self, url: str) -> dict:
        if self.parse_error:
            raise self.parse_error
        return json.loads(json.dumps(self.parse_result, ensure_ascii=False))

    def media_cache_dir(self, content_id: str) -> Path:
        return self.cache_root / content_id

    def download_media(self, url: str, dest_dir: Path, max_bytes: int = 0, *, filename: str = "", **_kwargs) -> Path:
        if url in self.fail_image_urls:
            raise FakeDtkError(f"媒体下载失败：HTTP 403 没有访问权限（{url}）")
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / (filename or "media.bin")
        path.write_bytes(b"fake-media-bytes-" + url.encode("utf-8")[:32])
        self.downloaded.append(url)
        return path

    def ffprobe_check(self, path: Path) -> dict:
        self.probed.append(path)
        return {"format_name": "mp4", "duration_seconds": 61.5, "size_bytes": 55296}

    def cleanup_media(self, content_id: str) -> str:
        self.cleaned.append(content_id)
        return "已删除媒体缓存目录（测试）"


LONG_DESCRIPTION = "这是一段足够长的抖音分享文案，用来满足内容质量门禁对正文的最低要求。" * 4
OCR_LINE = "第几张图片的识别文字内容包括产品名称、功能说明和使用建议，用于凑足字数门槛的要求。"


def fake_paddle_text_ocr(image_paths):
    records = [
        {"path": str(path), "lines": [f"{OCR_LINE}（{index}）"], "backend": "PaddleOCR", "model": "PP-OCRv5"}
        for index, path in enumerate(image_paths, start=1)
    ]
    return records, None


class CaptureDouyinDtkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capture = load_module(CAPTURE_SCRIPT, "test_dtk_capture_link_module")

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.inbox = Path(temp_dir.name) / "inbox"
        self.url = "https://www.douyin.com/video/7708714176931188002"

    def _run_capture(
        self,
        fake_dtk: FakeDtkClient,
        *,
        extract_content_mode: str = "auto",
        image_ocr: bool = True,
        page_info=None,
        page_error=None,
        attach_extract=None,
        extra_patchers=None,
    ):
        run_yt_dlp = mock.patch.object(self.capture, "run_yt_dlp", return_value=(None, None))
        douyin_page_info = mock.patch.object(self.capture, "douyin_page_info", return_value=(page_info, page_error))
        dtk_patched = mock.patch.object(self.capture, "dtk_client", fake_dtk)
        paddle = mock.patch.object(self.capture, "run_paddle_text_ocr", side_effect=fake_paddle_text_ocr)
        attach = mock.patch.object(self.capture, "attach_content_extract", side_effect=attach_extract)
        with run_yt_dlp, douyin_page_info, dtk_patched, paddle, attach as attach_mock:
            with contextlib.ExitStack() as stack:
                for patcher in extra_patchers or []:
                    stack.enter_context(patcher)
                path = self.capture.capture_url(
                    self.url,
                    self.inbox,
                    extract_content_mode=extract_content_mode,
                    image_ocr=image_ocr,
                )
                note = path.read_text(encoding="utf-8")
        return note, attach_mock

    @staticmethod
    def _attach_extract_stub(info, **_kwargs):
        info["content_extract"] = {
            "source": "公开视频音频转写",
            "backend": "faster-whisper",
            "model": "small",
            "language": "zh",
            "segment_count": 3,
            "word_count": 500,
            "summary": "",
            "key_points": [],
            "timeline": [],
            "excerpt": "",
        }

    def test_image_album_maps_to_image_note_with_full_ocr_and_no_transcription(self) -> None:
        fake_dtk = FakeDtkClient(
            parse_result={
                "kind": "image_album",
                "content_id": "7708714176931188002",
                "title": "图文作品",
                "desc": LONG_DESCRIPTION,
                "images": [{"url": f"https://cdn.douyin.test/img-{index}.jpeg"} for index in range(1, 4)],
            }
        )
        note, attach_mock = self._run_capture(fake_dtk, extract_content_mode="always", page_info=None, page_error="抖音公开页读取失败：超时")

        attach_mock.assert_not_called()
        self.assertIn("作品类型: 图文", note)
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=3/3 | 转写=不适用 | 画面解析=未开始", note)
        self.assertIn("图片文字 OCR", note)
        self.assertIn("抖音图集图片 OCR", note)
        self.assertIn("图文作品按规则不触发转写", note)
        self.assertNotIn("## 视频内容摘录\n", note)
        self.assertIn("内容质量: 可推送", note)
        self.assertEqual(3, len(fake_dtk.downloaded))
        self.assertEqual(["7708714176931188002"], fake_dtk.cleaned)

    def test_partial_ocr_coverage_keeps_needs_more_parsing(self) -> None:
        fail_url = "https://cdn.douyin.test/img-2.jpeg"
        fake_dtk = FakeDtkClient(
            parse_result={
                "kind": "image_album",
                "content_id": "7708714176931188002",
                "title": "图文作品",
                "desc": LONG_DESCRIPTION,
                "images": [{"url": f"https://cdn.douyin.test/img-{index}.jpeg"} for index in range(1, 6)],
            },
            fail_image_urls={fail_url},
        )
        note, attach_mock = self._run_capture(fake_dtk)

        attach_mock.assert_not_called()
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=4/5 | 转写=不适用 | 画面解析=未开始", note)
        self.assertIn("内容质量: 需继续解析", note)
        self.assertIn("图片尚未全部处理", note)
        self.assertEqual(["7708714176931188002"], fake_dtk.cleaned)

    def test_video_type_keyword_heuristics(self) -> None:
        for title, expected in (("普通口播分享", "口播视频"), ("新界面操作演示教程", "UI演示视频")):
            with self.subTest(title=title):
                fake_dtk = FakeDtkClient(
                    parse_result={
                        "kind": "video",
                        "content_id": "7708714176931188002",
                        "title": title,
                        "desc": title + LONG_DESCRIPTION,
                        "video": {"url": "https://cdn.douyin.test/video-1.mp4", "duration": 61},
                    }
                )
                note, attach_mock = self._run_capture(fake_dtk, attach_extract=self._attach_extract_stub)
                self.assertIn(f"作品类型: {expected}", note)
                self.assertIn("转写=已完成", note)
                attach_mock.assert_called_once()

    def test_video_media_lifecycle_downloads_validates_and_cleans_up(self) -> None:
        fake_dtk = FakeDtkClient(
            parse_result={
                "kind": "video",
                "content_id": "7708714176931188002",
                "title": "口播视频",
                "desc": LONG_DESCRIPTION,
                "video": {"url": "https://cdn.douyin.test/video-1.mp4", "duration": 61},
            }
        )
        seen: dict[str, object] = {}

        def record_attach(info, **kwargs):
            seen["local_media_path"] = info.get("local_media_path")
            seen["media_url"] = info.get("media_url")
            self._attach_extract_stub(info, **kwargs)

        note, attach_mock = self._run_capture(fake_dtk, attach_extract=record_attach)

        attach_mock.assert_called_once()
        self.assertEqual("https://cdn.douyin.test/video-1.mp4", seen["media_url"])
        self.assertTrue(str(seen["local_media_path"]).endswith("7708714176931188002.mp4"))
        self.assertEqual(["https://cdn.douyin.test/video-1.mp4"], fake_dtk.downloaded)
        self.assertEqual(1, len(fake_dtk.probed))
        self.assertEqual(["7708714176931188002"], fake_dtk.cleaned)
        self.assertIn("媒体校验：mp4", note)
        self.assertIn("https://cdn.douyin.test/video-1.mp4", note)
        self.assertIn("转写=已完成", note)

    def test_unknown_kind_skips_transcription_and_stays_needs_more_parsing(self) -> None:
        fake_dtk = FakeDtkClient(
            parse_result={
                "kind": "",
                "content_id": "7708714176931188002",
                "title": "无法判定类型",
                "desc": LONG_DESCRIPTION,
            }
        )
        note, attach_mock = self._run_capture(fake_dtk, extract_content_mode="always")

        attach_mock.assert_not_called()
        self.assertIn("作品类型: 未知", note)
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=未开始 | 转写=未开始 | 画面解析=未开始", note)
        self.assertIn("内容质量: 需继续解析", note)
        self.assertIn("跳过转写", note)
        self.assertEqual([], fake_dtk.downloaded)
        self.assertEqual([], fake_dtk.cleaned)

    def test_dtk_unavailable_degrades_to_public_page_result(self) -> None:
        fake_dtk = FakeDtkClient(parse_error=FakeDtkError("无法连接自建 dtk 服务（ConnectionRefusedError(61, 'Connection refused')）。", unavailable=True))
        html_info = {
            "id": "7708714176931188002",
            "title": "公开页标题",
            "description": "公开页文案",
            "webpage_url": self.url,
            "original_url": self.url,
            "extractor_key": "Douyin HTML",
            "parse_tool": "公开页面 HTML",
        }
        note, attach_mock = self._run_capture(fake_dtk, page_info=html_info, page_error=None)

        self.assertIn("公开页标题", note)
        self.assertIn("自建服务不可用", note)
        self.assertIn("已按公开页/yt-dlp 结果降级处理", note)
        self.assertIn("作品类型: 未知", note)
        self.assertIn("内容质量: 需继续解析", note)
        attach_mock.assert_not_called()
        self.assertEqual([], fake_dtk.downloaded)

    def test_dtk_api_error_degrades_without_unavailable_label(self) -> None:
        fake_dtk = FakeDtkClient(parse_error=FakeDtkError("自建 dtk 服务解析任务失败（UPSTREAM_ERROR）：cookie 失效。"))
        note, _ = self._run_capture(fake_dtk, page_info=None, page_error="抖音公开页读取失败：403")

        self.assertIn("自建 dtk 服务解析失败", note)
        self.assertIn("UPSTREAM_ERROR", note)
        self.assertIn("解析失败原因", note)

    def test_no_extract_content_skips_video_download_and_uses_metadata(self) -> None:
        """M6 回归：never 模式不下载视频/ffprobe；媒体链接与时长直接取 dtk 元数据，也不清理缓存。"""
        fake_dtk = FakeDtkClient(
            parse_result={
                "kind": "video",
                "content_id": "7708714176931188002",
                "title": "口播视频",
                "desc": LONG_DESCRIPTION,
                "duration": 61,
                "video": {"url": "https://cdn.douyin.test/video-1.mp4"},
            }
        )
        note, attach_mock = self._run_capture(fake_dtk, extract_content_mode="never")

        attach_mock.assert_not_called()
        self.assertEqual([], fake_dtk.downloaded)  # 不再全量下载视频后又在 finally 删除。
        self.assertEqual([], fake_dtk.probed)
        self.assertEqual([], fake_dtk.cleaned)  # 无下载则无清理。
        self.assertIn("视频媒体链接", note)
        self.assertIn("https://cdn.douyin.test/video-1.mp4", note)
        self.assertIn("时长：01:01", note)  # 时长来自 dtk 元数据 duration=61。
        self.assertIn("转写=未开始", note)
        self.assertIn("内容质量: 需继续解析", note)

    def test_no_image_ocr_skips_douyin_album_download_and_ocr(self) -> None:
        """M5 回归：--no-image-ocr 时不下载图集图片、不触发逐张 OCR，其余媒体信息不受影响。"""
        fake_dtk = FakeDtkClient(
            parse_result={
                "kind": "image_album",
                "content_id": "7708714176931188002",
                "title": "图文作品",
                "desc": LONG_DESCRIPTION,
                "images": [{"url": f"https://cdn.douyin.test/img-{index}.jpeg"} for index in range(1, 4)],
            }
        )
        attach_album_ocr = mock.patch.object(self.capture, "attach_douyin_image_ocr", new=mock.Mock())
        note, attach_mock = self._run_capture(
            fake_dtk,
            image_ocr=False,
            extra_patchers=[attach_album_ocr],
        )

        attach_mock.assert_not_called()
        # attach_douyin_image_ocr 未被调用、图片未下载。
        attach_album_ocr.new.assert_not_called()
        self.assertEqual([], fake_dtk.downloaded)
        self.assertEqual([], fake_dtk.cleaned)
        self.assertIn("已按 --no-image-ocr 参数跳过图片 OCR", note)
        self.assertIn("内容质量: 需继续解析", note)

    def test_prepare_failure_still_cleans_up_media_cache(self) -> None:
        """L11 回归：prepare（OCR 子进程等）中途异常时，finally 仍清理媒体缓存目录。"""
        fake_dtk = FakeDtkClient(
            parse_result={
                "kind": "video",
                "content_id": "7708714176931188002",
                "title": "口播视频",
                "desc": LONG_DESCRIPTION,
                "video": {"url": "https://cdn.douyin.test/video-1.mp4", "duration": 61},
            }
        )
        run_yt_dlp = mock.patch.object(self.capture, "run_yt_dlp", return_value=(None, None))
        page = mock.patch.object(self.capture, "douyin_page_info", return_value=(None, "抖音公开页读取失败：超时"))
        dtk_patched = mock.patch.object(self.capture, "dtk_client", fake_dtk)
        failing = mock.patch.object(self.capture, "prepare_douyin_media", side_effect=RuntimeError("OCR 子进程启动失败"))
        cleaned = mock.patch.object(self.capture, "cleanup_douyin_media_cache")
        with run_yt_dlp, page, dtk_patched, failing as prepare_mock, cleaned as cleanup_mock:
            with self.assertRaises(RuntimeError):
                self.capture.capture_url(self.url, self.inbox)
        prepare_mock.assert_called_once()
        cleanup_mock.assert_called_once_with("7708714176931188002")

    def test_dtk_duration_seconds_units_follow_field_names(self) -> None:
        """L7 回归：duration_ms 按毫秒换算；裸 duration 按秒保留，>10000 秒的超长视频不再被误除。"""
        self.assertEqual(490.846, self.capture.dtk_duration_seconds({"duration_ms": 490846}))
        self.assertEqual(61.0, self.capture.dtk_duration_seconds({"duration": 61}))
        self.assertEqual(10500.0, self.capture.dtk_duration_seconds({"duration": 10500}))  # 约 2.9 小时，保持原值。
        self.assertEqual(36000.0, self.capture.dtk_duration_seconds({"duration": 36000}))  # 10 小时，保持原值。
        self.assertEqual(61.0, self.capture.dtk_duration_seconds({"media": {"video": {"duration": 61}}}))
        self.assertEqual(0.0, self.capture.dtk_duration_seconds({"duration_ms": "not-a-number"}))
        self.assertEqual(0.0, self.capture.dtk_duration_seconds({}))
        # 时长进笔记时按秒取整：10000 秒出头不被误除后仍保持小时级。
        info = self.capture.douyin_info_from_dtk(
            {"kind": "video", "content_id": "1", "title": "超长视频", "duration": 10100},
            "https://www.douyin.com/video/1",
        )
        assert info is not None
        self.assertEqual("10100", info["duration"])

    def test_douyin_info_from_dtk_real_payload_shape(self) -> None:
        payload = {
            "kind": "video",
            "platform": "douyin",
            "content_id": "7705357448701431040",
            "title": "界面操作演示",
            "description": "分享文案",
            "duration_ms": 490846,
            "created_at": "2026-11-07T11:30:00Z",
            "web_url": "https://www.douyin.com/video/7705357448701431040",
            "author": {"uid": "author-uid", "sec_uid": "sec-uid", "nickname": "作者", "web_url": "https://www.douyin.com/user/sec-uid"},
            "stats": {"digg_count": 24612, "comment_count": 259, "share_count": 3836, "collect_count": 26930},
            "media": {
                "video": {"url": "https://cdn.douyin.test/video.mp4", "urls": ["https://cdn.douyin.test/video.mp4"], "watermark": False, "size_bytes": 55296},
                "covers": [{"url": "https://cdn.douyin.test/cover.jpg"}],
                "images": [],
                "streams": [],
            },
            "tags": [{"name": "标签一"}, "标签二"],
        }
        info = self.capture.douyin_info_from_dtk(payload, "https://www.douyin.com/video/7705357448701431040")
        assert info is not None
        self.assertEqual("video", info["dtk_kind"])
        self.assertEqual("7705357448701431040", info["id"])
        self.assertEqual("490", info["duration"])
        self.assertEqual("20261107", info["upload_date"])
        self.assertEqual("https://cdn.douyin.test/video.mp4", info["media_url"])
        self.assertEqual("https://cdn.douyin.test/video.mp4", info["video_media_url"])
        self.assertEqual("https://cdn.douyin.test/cover.jpg", info["thumbnail"])
        self.assertEqual("作者", info["uploader"])
        self.assertEqual("24612", info["like_count"])
        self.assertEqual(["标签一", "标签二"], info["tags"])
        self.assertEqual(self.capture.douyin_work_type(info), "UI演示视频")

    def test_douyin_info_from_dtk_image_album_media_images(self) -> None:
        payload = {
            "kind": "image_album",
            "content_id": "7708714176931188002",
            "title": "图文作品",
            "description": "图文文案",
            "created_at": "2026-11-16T10:28:10Z",
            "media": {
                "video": None,
                "images": [{"url": f"https://cdn.douyin.test/img-{index}.webp"} for index in range(1, 4)],
            },
        }
        info = self.capture.douyin_info_from_dtk(payload, "https://www.douyin.com/video/7708714176931188002")
        assert info is not None
        self.assertEqual(["https://cdn.douyin.test/img-1.webp", "https://cdn.douyin.test/img-2.webp", "https://cdn.douyin.test/img-3.webp"], info["image_urls"])
        self.assertEqual("", info["media_url"])
        self.assertEqual(self.capture.douyin_work_type(info), "图文")

    def test_douyin_work_type_mapping_rules(self) -> None:
        cases = [
            ({"dtk_kind": "image_album"}, "图文"),
            ({"dtk_kind": "video", "title": "普通分享"}, "口播视频"),
            ({"dtk_kind": "video", "title": "界面操作演示"}, "UI演示视频"),
            ({"dtk_kind": "video", "description": "How to use this tutorial"}, "UI演示视频"),
            ({"dtk_kind": "video", "description": "how to 配置"}, "UI演示视频"),
            ({}, "未知"),
            (None, "未知"),
            ({"image_urls": ["https://cdn.douyin.test/1.jpeg"]}, "图文"),
            ({"image_urls": ["https://cdn.douyin.test/1.jpeg"], "media_url": "https://cdn.douyin.test/v.mp4"}, "口播视频"),
            ({"duration": 61, "media_url": "https://cdn.douyin.test/v.mp4"}, "口播视频"),
            ({"duration": 61}, "口播视频"),
            ({"description": "只有文案"}, "未知"),
        ]
        for info, expected in cases:
            with self.subTest(info=info):
                self.assertEqual(expected, self.capture.douyin_work_type(info))

    def test_assess_content_quality_gates_douyin_by_work_type(self) -> None:
        quality, _ = self.capture.assess_content_quality(
            platform="抖音",
            material_type="社媒链接",
            parse_status="已解析",
            description=LONG_DESCRIPTION,
            platform_meta={},
            content_extract=None,
            content_extract_error="",
            image_ocr=None,
            image_ocr_error="",
            youtube_subtitle=None,
            external_transcript=None,
            work_type="图文",
        )
        self.assertEqual("需继续解析", quality)

        quality, _ = self.capture.assess_content_quality(
            platform="抖音",
            material_type="社媒链接",
            parse_status="已解析",
            description="",
            platform_meta={},
            content_extract=None,
            content_extract_error="",
            image_ocr=None,
            image_ocr_error="",
            youtube_subtitle=None,
            external_transcript=None,
            work_type="未知",
        )
        self.assertEqual("需继续解析", quality)

        complete_ocr = {"word_count": 200, "processed_count": 3, "image_count": 3, "errors": []}
        quality, _ = self.capture.assess_content_quality(
            platform="抖音",
            material_type="社媒链接",
            parse_status="已解析",
            description=LONG_DESCRIPTION,
            platform_meta={},
            content_extract=None,
            content_extract_error="",
            image_ocr=complete_ocr,
            image_ocr_error="",
            youtube_subtitle=None,
            external_transcript=None,
            work_type="图文",
        )
        self.assertEqual("可推送", quality)

        # M4 回归：图文文案缺失（OCR 全覆盖也）不可推送，规则表要求 文案完整 且 OCR N/N。
        quality, reason = self.capture.assess_content_quality(
            platform="抖音",
            material_type="社媒链接",
            parse_status="已解析",
            description="",
            platform_meta={},
            content_extract=None,
            content_extract_error="",
            image_ocr=complete_ocr,
            image_ocr_error="",
            youtube_subtitle=None,
            external_transcript=None,
            work_type="图文",
        )
        self.assertEqual("需继续解析", quality)
        self.assertIn("分享文案缺失", reason)

    def test_assess_content_quality_gates_ui_demo_before_visual_batch(self) -> None:
        """M3 回归：批次 5 视觉分析落地前，UI 演示视频转写完成也停在 需继续解析；人工改判回口播视频后可推送。"""
        complete_extract = {"source": "公开视频音频转写", "model": "small", "word_count": 500}
        base = dict(
            platform="抖音",
            material_type="社媒链接",
            parse_status="已解析",
            description=LONG_DESCRIPTION,
            platform_meta={},
            content_extract=complete_extract,
            content_extract_error="",
            image_ocr=None,
            image_ocr_error="",
            youtube_subtitle=None,
            external_transcript=None,
        )

        quality, reason = self.capture.assess_content_quality(work_type="UI演示视频", **base)
        self.assertEqual("需继续解析", quality)
        self.assertIn("画面解析尚未执行", reason)
        self.assertIn("UI 演示视频", reason)

        base["work_type"] = "口播视频"  # 用户人工改判回口播视频：完整转写即恢复可推送路径。
        quality, reason = self.capture.assess_content_quality(**base)
        self.assertEqual("可推送", quality)

        quality, reason = self.capture.assess_content_quality(
            platform="小红书",
            material_type="社媒链接",
            parse_status="已解析",
            description=LONG_DESCRIPTION,
            platform_meta={},
            content_extract=complete_extract,
            content_extract_error="",
            image_ocr=None,
            image_ocr_error="",
            youtube_subtitle=None,
            external_transcript=None,
            work_type="UI演示视频",
        )
        self.assertEqual("需继续解析", quality)
        self.assertIn("画面解析尚未执行", reason)


if __name__ == "__main__":
    unittest.main()
