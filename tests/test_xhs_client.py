from __future__ import annotations

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
XHS_SCRIPT = SCRIPTS / "xhs_client.py"
CAPTURE_SCRIPT = SCRIPTS / "capture_link.py"

BEARER_TOKEN = "xhs-bearer-secret-0123456789abcdef"
CONFIG = {"base_url": "http://127.0.0.1:18601", "bearer_token": BEARER_TOKEN}
DETAIL_SUCCESS = "获取小红书作品数据成功"


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


class XhsClientConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.xhs = load_module(XHS_SCRIPT, "test_xhs_client_config_module")

    def test_load_config_reads_json_and_normalizes_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "xhs.json"
            path.write_text(json.dumps({"base_url": "http://127.0.0.1:18601/", "bearer_token": "abc"}), encoding="utf-8")
            config = self.xhs.load_config(path)
        self.assertEqual({"base_url": "http://127.0.0.1:18601", "bearer_token": "abc"}, config)

    def test_missing_config_file_is_unavailable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.load_config(Path(temp_dir) / "nope.json")
        self.assertTrue(ctx.exception.unavailable)
        self.assertIn("未找到小红书配置文件", str(ctx.exception))

    def test_config_missing_keys_is_unavailable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "xhs.json"
            path.write_text(json.dumps({"base_url": "http://127.0.0.1:18601"}), encoding="utf-8")
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.load_config(path)
        self.assertTrue(ctx.exception.unavailable)
        self.assertIn("缺少 base_url 或 bearer_token", str(ctx.exception))


class XhsClientDetailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.xhs = load_module(XHS_SCRIPT, "test_xhs_client_detail_module")

    def test_fetch_detail_success_returns_data_with_bearer_header(self) -> None:
        data = {"作品类型": "图集", "作品ID": "65f1a2b3000000001234abcd"}
        envelope = {"message": DETAIL_SUCCESS, "params": {"url": "https://xhslink.com/abc"}, "data": data}
        with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(envelope)) as urlopen:
            result = self.xhs.fetch_detail("https://xhslink.com/abc", config=CONFIG)
        self.assertEqual(data, result)
        request = urlopen.call_args.args[0]
        self.assertEqual("POST", request.method)
        self.assertEqual("http://127.0.0.1:18601/xhs/detail", request.full_url)
        expected_body = {
            "url": "https://xhslink.com/abc",
            "download": False,
            "index": None,
            "cookie": None,
            "proxy": None,
            "check_record": True,
        }
        self.assertEqual(json.dumps(expected_body, ensure_ascii=False).encode("utf-8"), request.data)
        self.assertTrue(any(value == f"Bearer {BEARER_TOKEN}" for value in request.headers.values()))

    def test_fetch_detail_message_failure_is_traceable(self) -> None:
        envelope = {"message": "作品链接错误", "params": {}, "data": None}
        with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(envelope)):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.fetch_detail("https://xhslink.com/bad", config=CONFIG)
        message = str(ctx.exception)
        self.assertIn("作品链接错误", message)
        self.assertFalse(ctx.exception.unavailable)

    def test_fetch_detail_http_403_reports_status_label(self) -> None:
        error_body = json.dumps({"message": "token invalid", "data": None}, ensure_ascii=False).encode("utf-8")
        http_error = urllib.error.HTTPError("http://127.0.0.1:18601/xhs/detail", 403, "Forbidden", None, io.BytesIO(error_body))
        with mock.patch.object(self.xhs.urllib.request, "urlopen", side_effect=http_error):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.fetch_detail("https://xhslink.com/abc", config=CONFIG)
        message = str(ctx.exception)
        self.assertIn("403", message)
        self.assertIn("没有访问权限", message)
        self.assertIn("token invalid", message)
        self.assertFalse(ctx.exception.unavailable)

    def test_fetch_detail_data_not_object_raises(self) -> None:
        envelope = {"message": DETAIL_SUCCESS, "params": {}, "data": []}
        with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(envelope)):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.fetch_detail("https://xhslink.com/abc", config=CONFIG)
        self.assertIn("未返回可用的 data 字段", str(ctx.exception))

    def test_fetch_detail_non_json_response_raises(self) -> None:
        with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(raw=b"<html>502</html>")):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.fetch_detail("https://xhslink.com/abc", config=CONFIG)
        self.assertIn("非 JSON 响应", str(ctx.exception))

    def test_connection_refused_is_unavailable(self) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        with mock.patch.object(self.xhs.urllib.request, "urlopen", side_effect=refused):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.fetch_detail("https://xhslink.com/abc", config=CONFIG)
        self.assertTrue(ctx.exception.unavailable)
        self.assertIn("无法连接自建 XHS-Downloader 服务", str(ctx.exception))

    def test_error_message_never_leaks_bearer_token(self) -> None:
        echo_body = json.dumps({"message": f"rejected {BEARER_TOKEN}", "data": None}, ensure_ascii=False).encode("utf-8")
        with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(raw=echo_body)):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.fetch_detail("https://xhslink.com/abc", config=CONFIG)
        self.assertNotIn(BEARER_TOKEN, str(ctx.exception))
        self.assertNotIn(BEARER_TOKEN[:12], str(ctx.exception))
        self.assertIn("***", str(ctx.exception))


class XhsClientDownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.xhs = load_module(XHS_SCRIPT, "test_xhs_client_download_module")

    def test_fresh_download_writes_file_with_explicit_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "xhs" / "65f1abcd"
            payload = b"fake-video-bytes-0123456789"
            with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(raw=payload)):
                path = self.xhs.download_media("https://sns-video.xhscdn.test/stream/110.mp4", dest_dir, filename="65f1abcd.mp4")
            self.assertEqual(payload, path.read_bytes())
            self.assertEqual("65f1abcd.mp4", path.name)

    def test_download_filename_falls_back_to_url_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(raw=b"image-bytes")):
                path = self.xhs.download_media("https://ci.xiaohongshu.test/note/a1b2c3.jpg?token=x", Path(temp_dir))
            self.assertEqual("a1b2c3.jpg", path.name)

    def test_download_over_max_bytes_aborts_and_removes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "xhs" / "65f1abcd"
            with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(raw=b"x" * 64)):
                with self.assertRaises(self.xhs.XhsError) as ctx:
                    self.xhs.download_media("https://sns-video.xhscdn.test/stream/110.mp4", dest_dir, max_bytes=16, filename="v.mp4")
            self.assertIn("超过上限", str(ctx.exception))
            self.assertEqual([], list(dest_dir.iterdir()))

    def test_download_empty_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_dir = Path(temp_dir) / "xhs" / "65f1abcd"
            with mock.patch.object(self.xhs.urllib.request, "urlopen", return_value=FakeResponse(raw=b"")):
                with self.assertRaises(self.xhs.XhsError) as ctx:
                    self.xhs.download_media("https://sns-video.xhscdn.test/stream/110.mp4", dest_dir)
            self.assertIn("下载结果为空", str(ctx.exception))
            self.assertEqual([], list(dest_dir.iterdir()))

    def test_download_empty_url_raises(self) -> None:
        with self.assertRaises(self.xhs.XhsError) as ctx:
            self.xhs.download_media("", Path(tempfile.gettempdir()))
        self.assertIn("下载地址为空", str(ctx.exception))


class XhsClientFfprobeCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.xhs = load_module(XHS_SCRIPT, "test_xhs_client_probe_module")

    def test_ffprobe_check_returns_format_and_duration(self) -> None:
        probe_json = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "45.2", "size": "452096"}}
        with mock.patch.object(self.xhs.shutil, "which", return_value="/usr/bin/ffprobe"), mock.patch.object(
            self.xhs.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(probe_json), ""),
        ) as run:
            result = self.xhs.ffprobe_check(Path("/tmp/fake.mp4"))
        self.assertEqual("mov,mp4,m4a,3gp,3g2,mj2", result["format_name"])
        self.assertEqual(45.2, result["duration_seconds"])
        self.assertEqual(452096, result["size_bytes"])
        self.assertIn("-show_format", run.call_args.args[0])

    def test_ffprobe_missing_binary_raises_chinese_error(self) -> None:
        with mock.patch.object(self.xhs.shutil, "which", return_value=None):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.ffprobe_check(Path("/tmp/fake.mp4"))
        self.assertIn("未找到 ffprobe", str(ctx.exception))

    def test_ffprobe_bad_file_raises_error(self) -> None:
        with mock.patch.object(self.xhs.shutil, "which", return_value="/usr/bin/ffprobe"), mock.patch.object(
            self.xhs.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", "Invalid data found when processing input"),
        ):
            with self.assertRaises(self.xhs.XhsError) as ctx:
                self.xhs.ffprobe_check(Path("/tmp/fake.mp4"))
        self.assertIn("媒体校验失败", str(ctx.exception))
        self.assertIn("Invalid data", str(ctx.exception))

    def test_cleanup_media_removes_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(self.xhs, "MEDIA_CACHE_ROOT", Path(temp_dir)):
                target = Path(temp_dir) / "65f1a2b3000000001234abcd"
                (target / "sub").mkdir(parents=True)
                (target / "sub" / "video.mp4").write_bytes(b"x" * 16)
                message = self.xhs.cleanup_media("65f1a2b3000000001234abcd")
                self.assertFalse(target.exists())
                self.assertIn("已删除", message)

    def test_cleanup_media_rejects_unsafe_note_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "safe").mkdir()
            with mock.patch.object(self.xhs, "MEDIA_CACHE_ROOT", root):
                message = self.xhs.cleanup_media("../../etc")
            self.assertIn("笔记编号不合法", message)
            self.assertTrue((root / "safe").exists())

    def test_cleanup_media_missing_directory_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(self.xhs, "MEDIA_CACHE_ROOT", Path(temp_dir)):
                message = self.xhs.cleanup_media("404")
            self.assertIn("无需清理", message)


class FakeXhsError(Exception):
    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        super().__init__(message)
        self.unavailable = unavailable


class FakeXhsClient:
    """替换 capture_link.xhs_client 的假客户端：不发真实网络请求。"""

    XhsError = FakeXhsError

    def __init__(self, detail_result: dict | None = None, detail_error: Exception | None = None):
        self.detail_result = detail_result or {}
        self.detail_error = detail_error
        self.cache_root = Path(tempfile.mkdtemp(prefix="my-mind-xhs-test-"))
        self.fetched: list[str] = []
        self.downloaded: list[str] = []
        self.probed: list[Path] = []
        self.cleaned: list[str] = []

    def fetch_detail(self, url: str) -> dict:
        self.fetched.append(url)
        if self.detail_error:
            raise self.detail_error
        return json.loads(json.dumps(self.detail_result, ensure_ascii=False))

    def media_cache_dir(self, note_id: str) -> Path:
        return self.cache_root / note_id

    def download_media(self, url: str, dest_dir: Path, max_bytes: int = 0, *, filename: str = "", **_kwargs) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / (filename or "media.bin")
        path.write_bytes(b"fake-media-bytes-" + url.encode("utf-8")[:32])
        self.downloaded.append(url)
        return path

    def ffprobe_check(self, path: Path) -> dict:
        self.probed.append(path)
        return {"format_name": "mp4", "duration_seconds": 45.2, "size_bytes": 452096}

    def cleanup_media(self, note_id: str) -> str:
        self.cleaned.append(note_id)
        return "已删除媒体缓存目录（测试）"


LONG_DESCRIPTION = "这是一段足够长的小红书分享文案，用来满足内容质量门禁对正文的最低要求。" * 4
OCR_LINE = "第几张图片的识别文字内容包括产品名称、功能说明和使用建议，用于凑足字数门槛的要求。"
NOTE_URL = "https://www.xiaohongshu.com/explore/65f1a2b3000000001234abcd"


def fake_paddle_text_ocr(image_paths):
    records = [
        {"path": str(path), "lines": [f"{OCR_LINE}（{index}）"], "backend": "PaddleOCR", "model": "PP-OCRv5"}
        for index, path in enumerate(image_paths, start=1)
    ]
    return records, None


class CaptureXhsServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capture = load_module(CAPTURE_SCRIPT, "test_xhs_capture_link_module")

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.inbox = Path(temp_dir.name) / "inbox"
        self.url = NOTE_URL

    def _run_capture(
        self,
        fake_xhs: FakeXhsClient,
        *,
        extract_content_mode: str = "auto",
        image_ocr: bool = True,
        page_info=None,
        page_error=None,
        attach_extract=None,
        fail_download_urls: set[str] | None = None,
        track_download_ocr: mock.Mock | None = None,
    ):
        run_yt_dlp = mock.patch.object(self.capture, "run_yt_dlp", return_value=(None, None))
        page = mock.patch.object(self.capture, "xiaohongshu_page_info", return_value=(page_info, page_error))
        xhs_patched = mock.patch.object(self.capture, "xhs_client", fake_xhs)

        def fake_download_ocr_image(url: str, output_path: Path):
            if url in (fail_download_urls or set()):
                return f"图片下载失败：HTTP 403（{url}）"
            output_path.write_bytes(b"fake-xhs-image-" + url.encode("utf-8")[:24])
            return None

        if track_download_ocr is not None:
            track_download_ocr.side_effect = fake_download_ocr_image
            download = mock.patch.object(self.capture, "download_ocr_image", new=track_download_ocr)
        else:
            download = mock.patch.object(self.capture, "download_ocr_image", side_effect=fake_download_ocr_image)
        paddle = mock.patch.object(self.capture, "run_paddle_text_ocr", side_effect=fake_paddle_text_ocr)
        attach = mock.patch.object(self.capture, "attach_content_extract", side_effect=attach_extract)
        with run_yt_dlp, page, xhs_patched, download, paddle, attach as attach_mock:
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

    @staticmethod
    def _album_detail(image_count: int) -> dict:
        return {
            "作品类型": "图集",
            "作品ID": "65f1a2b3000000001234abcd",
            "作品标题": "图集笔记",
            "作品描述": LONG_DESCRIPTION,
            "发布时间": "2026-11-15 10:28:10",
            "作者昵称": "作者",
            "作者ID": "5ff0e6410000000001008400",
            "标签": ["标签一", "标签二"],
            "下载地址": [f"https://ci.xiaohongshu.test/img-{index}.jpg" for index in range(1, image_count + 1)],
            "封面地址": "https://ci.xiaohongshu.test/img-1.jpg",
            "点赞数量": 1234,
            "收藏数量": 567,
            "评论数量": 89,
            "分享数量": 12,
        }

    @staticmethod
    def _video_detail(title: str = "口播分享") -> dict:
        return {
            "作品类型": "视频",
            "作品ID": "65f1a2b3000000005678efab",
            "作品标题": title,
            "作品描述": LONG_DESCRIPTION,
            "发布时间": "2026-11-16 18:00:00",
            "作者昵称": "作者",
            "作者ID": "5ff0e6410000000001008400",
            "标签": [],
            "下载地址": ["https://sns-video.xhscdn.test/stream/110.mp4"],
            "封面地址": "https://ci.xiaohongshu.test/cover.jpg",
            "点赞数量": 22,
            "收藏数量": 33,
            "评论数量": 4,
            "分享数量": 5,
        }

    def test_album_maps_to_image_note_with_full_ocr_and_no_transcription(self) -> None:
        fake_xhs = FakeXhsClient(detail_result=self._album_detail(3))
        note, attach_mock = self._run_capture(fake_xhs, extract_content_mode="always", page_info=None, page_error="小红书公开页读取失败：超时")

        attach_mock.assert_not_called()
        self.assertEqual([self.url], fake_xhs.fetched)
        self.assertIn("作品类型: 图文", note)
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=3/3 | 转写=不适用 | 画面解析=未开始", note)
        self.assertIn("小红书公开图片 OCR", note)
        self.assertIn("图文作品按规则不触发转写", note)
        self.assertNotIn("## 视频内容摘录\n", note)
        self.assertIn("内容质量: 可推送", note)
        self.assertIn("已使用自建 XHS-Downloader 服务兜底", note)
        self.assertEqual([], fake_xhs.downloaded)  # 图文经临时目录下载，不走媒体缓存。
        self.assertEqual([], fake_xhs.cleaned)

    def test_partial_ocr_coverage_keeps_needs_more_parsing(self) -> None:
        fail_url = "https://ci.xiaohongshu.test/img-2.jpg"
        fake_xhs = FakeXhsClient(detail_result=self._album_detail(5))
        note, attach_mock = self._run_capture(fake_xhs, fail_download_urls={fail_url})

        attach_mock.assert_not_called()
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=4/5 | 转写=不适用 | 画面解析=未开始", note)
        self.assertIn("内容质量: 需继续解析", note)
        self.assertIn("图片尚未全部处理", note)

    def test_full_ocr_respects_total_cap_of_20_images(self) -> None:
        fake_xhs = FakeXhsClient(detail_result=self._album_detail(22))
        note, _ = self._run_capture(fake_xhs)

        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=20/22 | 转写=不适用 | 画面解析=未开始", note)
        self.assertIn("内容质量: 需继续解析", note)
        self.assertIn("图片尚未全部处理", note)

    def test_public_page_complete_skips_service_fallback(self) -> None:
        fake_xhs = FakeXhsClient(detail_result=self._album_detail(3))
        page_info = {
            "id": "65f1a2b3000000001234abcd",
            "title": "公开页图文",
            "description": LONG_DESCRIPTION,
            "image_urls": [f"https://ci.xiaohongshu.test/pub-{index}.jpg" for index in range(1, 4)],
            "webpage_url": self.url,
            "original_url": self.url,
            "extractor_key": "XiaoHongShu HTML",
            "parse_tool": "公开页面 HTML",
            "platform_meta": {"笔记类型": "normal", "图片数": 3},
        }
        note, _ = self._run_capture(fake_xhs, page_info=page_info, page_error=None)

        self.assertEqual([], fake_xhs.fetched)  # 公开页图片列表完整时不强制走兜底。
        self.assertIn("作品类型: 图文", note)
        self.assertIn("图片OCR=3/3", note)
        self.assertNotIn("自建 XHS-Downloader 服务兜底", note)

    def test_video_type_keyword_heuristics_shared_with_douyin(self) -> None:
        for title, expected in (("普通口播分享", "口播视频"), ("新界面操作演示教程", "UI演示视频")):
            with self.subTest(title=title):
                fake_xhs = FakeXhsClient(detail_result=self._video_detail(title))
                note, attach_mock = self._run_capture(fake_xhs, attach_extract=self._attach_extract_stub)
                self.assertIn(f"作品类型: {expected}", note)
                self.assertIn("转写=已完成", note)
                attach_mock.assert_called_once()

    def test_video_media_lifecycle_downloads_validates_and_cleans_up(self) -> None:
        fake_xhs = FakeXhsClient(detail_result=self._video_detail())
        seen: dict[str, object] = {}

        def record_attach(info, **kwargs):
            seen["local_media_path"] = info.get("local_media_path")
            seen["media_url"] = info.get("media_url")
            self._attach_extract_stub(info, **kwargs)

        note, attach_mock = self._run_capture(fake_xhs, attach_extract=record_attach)

        attach_mock.assert_called_once()
        self.assertEqual("https://sns-video.xhscdn.test/stream/110.mp4", seen["media_url"])
        self.assertTrue(str(seen["local_media_path"]).endswith("65f1a2b3000000005678efab.mp4"))
        self.assertEqual(["https://sns-video.xhscdn.test/stream/110.mp4"], fake_xhs.downloaded)
        self.assertEqual(1, len(fake_xhs.probed))
        self.assertEqual(["65f1a2b3000000005678efab"], fake_xhs.cleaned)
        self.assertIn("媒体校验：mp4", note)
        self.assertIn("https://sns-video.xhscdn.test/stream/110.mp4", note)
        self.assertIn("作品类型: 口播视频", note)
        self.assertIn("转写=已完成", note)

    def test_unknown_kind_skips_transcription_and_stays_needs_more_parsing(self) -> None:
        fake_xhs = FakeXhsClient(
            detail_result={
                "作品类型": "未知",
                "作品ID": "65f1a2b3000000001234abcd",
                "作品标题": "无法判定类型",
                "作品描述": LONG_DESCRIPTION,
            }
        )
        note, attach_mock = self._run_capture(fake_xhs, extract_content_mode="always")

        attach_mock.assert_not_called()
        self.assertIn("作品类型: 未知", note)
        self.assertIn("模态覆盖: 文案=完整 | 图片OCR=未开始 | 转写=未开始 | 画面解析=未开始", note)
        self.assertIn("内容质量: 需继续解析", note)
        self.assertIn("跳过转写", note)
        self.assertEqual([], fake_xhs.downloaded)
        self.assertEqual([], fake_xhs.cleaned)

    def test_service_unavailable_degrades_to_public_page_result(self) -> None:
        fake_xhs = FakeXhsClient(
            detail_error=FakeXhsError("无法连接自建 XHS-Downloader 服务（ConnectionRefusedError(61, 'Connection refused')）。", unavailable=True)
        )
        page_info = {
            "id": "65f1a2b3000000001234abcd",
            "title": "公开页标题",
            "description": "公开页文案",
            "image_urls": ["https://ci.xiaohongshu.test/pub-1.jpg"],
            "webpage_url": self.url,
            "original_url": self.url,
            "extractor_key": "XiaoHongShu HTML",
            "parse_tool": "公开页面 HTML",
            "platform_meta": {"笔记类型": "normal", "图片数": 3},
        }
        note, attach_mock = self._run_capture(fake_xhs, page_info=page_info, page_error=None)

        self.assertIn("公开页标题", note)
        self.assertIn("小红书兜底服务不可用", note)
        self.assertIn("已按公开页结果降级处理", note)
        self.assertEqual([], fake_xhs.downloaded)
        self.assertEqual([], fake_xhs.cleaned)
        attach_mock.assert_not_called()

    def test_service_api_error_degrades_without_unavailable_label(self) -> None:
        fake_xhs = FakeXhsClient(detail_error=FakeXhsError("自建 XHS-Downloader 服务详情请求失败：HTTP 403 没有访问权限"))
        note, _ = self._run_capture(fake_xhs, page_info=None, page_error="小红书公开页读取失败：403")

        self.assertIn("自建 XHS-Downloader 服务解析失败", note)
        self.assertIn("403", note)
        self.assertIn("解析失败原因", note)

    def test_prepare_failure_still_cleans_up_media_cache(self) -> None:
        """L11 回归：prepare 中途异常时，finally 仍清理媒体缓存目录。"""
        fake_xhs = FakeXhsClient(detail_result=self._video_detail())
        run_yt_dlp = mock.patch.object(self.capture, "run_yt_dlp", return_value=(None, None))
        page = mock.patch.object(self.capture, "xiaohongshu_page_info", return_value=(None, "小红书公开页读取失败：403"))
        xhs_patched = mock.patch.object(self.capture, "xhs_client", fake_xhs)
        failing = mock.patch.object(self.capture, "prepare_xhs_media", side_effect=RuntimeError("OCR 子进程启动失败"))
        cleaned = mock.patch.object(self.capture, "cleanup_xhs_media_cache")
        with run_yt_dlp, page, xhs_patched, failing as prepare_mock, cleaned as cleanup_mock:
            with self.assertRaises(RuntimeError):
                self.capture.capture_url(self.url, self.inbox)
        prepare_mock.assert_called_once()
        cleanup_mock.assert_called_once_with("65f1a2b3000000005678efab")

    def test_no_extract_content_skips_video_download_and_uses_metadata(self) -> None:
        """M6 回归：never 模式不下载视频/ffprobe；媒体链接与时长直接取服务元数据，也不清理缓存。"""
        detail = {**self._video_detail(), "视频时长": 336}
        fake_xhs = FakeXhsClient(detail_result=detail)
        note, attach_mock = self._run_capture(fake_xhs, extract_content_mode="never")

        attach_mock.assert_not_called()
        self.assertEqual([], fake_xhs.downloaded)  # 不再全量下载视频后又在 finally 删除。
        self.assertEqual([], fake_xhs.probed)
        self.assertEqual([], fake_xhs.cleaned)  # 无下载则无清理。
        self.assertIn("视频媒体链接", note)
        self.assertIn("https://sns-video.xhscdn.test/stream/110.mp4", note)
        self.assertIn("时长：05:36", note)  # 时长来自服务元数据 视频时长=336。
        self.assertIn("转写=未开始", note)
        self.assertIn("内容质量: 需继续解析", note)

    def test_no_image_ocr_skips_xhs_album_download_and_ocr(self) -> None:
        """M5 回归：--no-image-ocr 时不下载图片、不触发快速路径与兜底全量 OCR，其余媒体信息不受影响。"""
        fake_xhs = FakeXhsClient(detail_result=self._album_detail(3))
        download_ocr = mock.Mock()
        attach_full = mock.patch.object(self.capture, "attach_xhs_image_ocr_full", new=mock.Mock())
        with attach_full:
            note, attach_mock = self._run_capture(
                fake_xhs,
                image_ocr=False,
                track_download_ocr=download_ocr,
            )

        attach_mock.assert_not_called()
        attach_full.new.assert_not_called()  # 兜底全量 OCR 未触发。
        download_ocr.assert_not_called()  # 快速路径图片下载也未触发。
        self.assertEqual([], fake_xhs.downloaded)
        self.assertEqual([], fake_xhs.cleaned)
        self.assertIn("已按 --no-image-ocr 参数跳过图片 OCR", note)
        self.assertIn("内容质量: 需继续解析", note)


class CaptureXhsMappingTest(unittest.TestCase):
    """类型映射、兜底触发条件与共用启发式的纯函数用例。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.capture = load_module(CAPTURE_SCRIPT, "test_xhs_capture_mapping_module")

    def test_xiaohongshu_work_type_mapping_rules(self) -> None:
        cases = [
            ({"xhs_kind": "图文"}, "图文"),
            ({"xhs_kind": "图集"}, "图文"),
            ({"xhs_kind": "视频", "title": "普通分享"}, "口播视频"),
            ({"xhs_kind": "视频", "title": "界面操作演示"}, "UI演示视频"),
            ({"xhs_kind": "视频", "description": "How to use this tutorial"}, "UI演示视频"),
            ({"xhs_kind": "视频", "description": "how to 配置"}, "UI演示视频"),
            ({"xhs_kind": "未知"}, "未知"),
            ({}, "未知"),
            (None, "未知"),
            ({"platform_meta": {"笔记类型": "normal"}, "image_urls": ["https://ci.xiaohongshu.test/1.jpg"]}, "图文"),
            ({"platform_meta": {"笔记类型": "video"}}, "口播视频"),
            ({"platform_meta": {"笔记类型": "video"}, "title": "新手教程三步走"}, "UI演示视频"),
            ({"image_urls": ["https://ci.xiaohongshu.test/1.jpg"]}, "图文"),
            ({"description": "只有文案"}, "未知"),
        ]
        for info, expected in cases:
            with self.subTest(info=info):
                self.assertEqual(expected, self.capture.xiaohongshu_work_type(info))

    def test_ui_demo_keyword_heuristic_is_shared_with_douyin(self) -> None:
        for text in ("新界面操作演示教程", "How to use this tutorial", "how to 配置", "普通分享口播", ""):
            with self.subTest(text=text):
                self.assertEqual(
                    self.capture.ui_demo_keyword_heuristic(text),
                    self.capture.douyin_ui_demo_heuristic(text),
                )
        self.assertIs(self.capture.DOUYIN_UI_DEMO_KEYWORDS, self.capture.UI_DEMO_KEYWORDS)

    def test_guess_platform_accepts_xhslink_cn_short_links(self) -> None:
        """回归：小红书 .cn 短链域名必须识别为小红书，否则整条链路不触发（2026-11-17 实测 0/7 根因）。"""

        cases = [
            "https://xhslink.cn/o/AfnRC99tUEZ",
            "https://xhslink.com/o/AbCdEfG",
            "https://www.xiaohongshu.com/explore/65f1a2b3",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual("小红书", self.capture.guess_platform(url))

    def test_xiaohongshu_needs_fallback_rules(self) -> None:
        complete = {
            "platform_meta": {"笔记类型": "normal", "图片数": 3},
            "image_urls": [f"https://ci.xiaohongshu.test/{index}.jpg" for index in range(1, 4)],
        }
        cases = [
            (None, True),  # 公开页整体失败
            ({}, True),
            (complete, False),  # 公开页图文列表完整，不强制兜底
            ({"platform_meta": {"笔记类型": "normal", "图片数": 3}, "image_urls": ["https://ci.xiaohongshu.test/1.jpg"]}, True),  # 列表不全
            ({"platform_meta": {"笔记类型": "video", "图片数": 1}, "image_urls": ["https://ci.xiaohongshu.test/cover.jpg"]}, True),  # 视频需要媒体直链
            ({"platform_meta": {"图片数": ""}, "image_urls": ["https://ci.xiaohongshu.test/og.jpg"]}, True),  # 未暴露 imageList，只剩 og:image
        ]
        for info, expected in cases:
            with self.subTest(info=info):
                self.assertEqual(expected, self.capture.xiaohongshu_needs_fallback(info))

    def test_xiaohongshu_info_from_xhs_real_payload_shape(self) -> None:
        payload = {
            "作品类型": "视频",
            "作品ID": "65f1a2b3000000001234abcd",
            "作品标题": "界面操作演示",
            "作品描述": "分享文案",
            "发布时间": "2026-11-15 10:28:10",
            "作者昵称": "作者",
            "作者ID": "5ff0e6410000000001008400",
            "标签": ["标签一", "标签二"],
            "下载地址": ["https://sns-video.xhscdn.test/stream/110.mp4"],
            "封面地址": "https://ci.xiaohongshu.test/cover.jpg",
            "点赞数量": 1234,
            "收藏数量": 567,
            "评论数量": 89,
            "分享数量": 12,
        }
        info = self.capture.xiaohongshu_info_from_xhs(payload, NOTE_URL)
        assert info is not None
        self.assertEqual("视频", info["xhs_kind"])
        self.assertEqual("65f1a2b3000000001234abcd", info["id"])
        self.assertEqual("20261115", info["upload_date"])
        self.assertEqual("https://sns-video.xhscdn.test/stream/110.mp4", info["media_url"])
        self.assertEqual("https://sns-video.xhscdn.test/stream/110.mp4", info["video_media_url"])
        self.assertEqual([], info["image_urls"])
        self.assertEqual("https://ci.xiaohongshu.test/cover.jpg", info["thumbnail"])
        self.assertEqual("作者", info["uploader"])
        self.assertEqual("1234", info["like_count"])
        self.assertEqual(["标签一", "标签二"], info["tags"])
        self.assertEqual("video", info["platform_meta"]["笔记类型"])
        self.assertEqual("https://www.xiaohongshu.com/explore/65f1a2b3000000001234abcd", info["webpage_url"])
        self.assertEqual(self.capture.xiaohongshu_work_type(info), "UI演示视频")

    def test_xiaohongshu_info_from_xhs_album_payload(self) -> None:
        payload = {
            "作品类型": "图集",
            "作品ID": "65f1a2b3000000001234abcd",
            "作品标题": "图集笔记",
            "作品描述": "图文文案",
            "下载地址": [f"https://ci.xiaohongshu.test/img-{index}.webp" for index in range(1, 4)],
            "封面地址": "https://ci.xiaohongshu.test/img-1.webp",
        }
        info = self.capture.xiaohongshu_info_from_xhs(payload, "https://xhslink.com/abcd")
        assert info is not None
        self.assertEqual(
            [f"https://ci.xiaohongshu.test/img-{index}.webp" for index in range(1, 4)],
            info["image_urls"],
        )
        self.assertEqual("", info["media_url"])
        self.assertEqual("normal", info["platform_meta"]["笔记类型"])
        self.assertEqual("3", str(info["platform_meta"]["图片数"]))
        self.assertEqual(self.capture.xiaohongshu_work_type(info), "图文")

    def test_xiaohongshu_info_from_xhs_normalizes_webpage_url_with_note_id(self) -> None:
        """L10 回归：兜底成功且有笔记编号时，webpage_url 统一规范为稳定作品页链接；短链保留在 original_url。"""
        payload = {
            "作品类型": "视频",
            "作品ID": "65f1a2b3000000005678efab",
            "作品标题": "短链视频",
            "作品描述": "分享文案",
            "下载地址": ["https://sns-video.xhscdn.test/stream/110.mp4"],
        }
        info = self.capture.xiaohongshu_info_from_xhs(payload, "https://xhslink.cn/o/AbCdEfG")
        assert info is not None
        self.assertEqual("https://www.xiaohongshu.com/explore/65f1a2b3000000005678efab", info["webpage_url"])
        self.assertEqual("https://xhslink.cn/o/AbCdEfG", info["original_url"])

        # 图集短链输入同样规范；无笔记编号时保留输入链接不猜测。
        album = {
            "作品类型": "图集",
            "作品ID": "65f1a2b3000000001234abcd",
            "作品标题": "图集笔记",
            "作品描述": "图文文案",
            "下载地址": ["https://ci.xiaohongshu.test/img-1.webp"],
        }
        info = self.capture.xiaohongshu_info_from_xhs(album, "https://xhslink.com/abcd")
        assert info is not None
        self.assertEqual("https://www.xiaohongshu.com/explore/65f1a2b3000000001234abcd", info["webpage_url"])
        self.assertEqual("https://xhslink.com/abcd", info["original_url"])

        no_id = {
            "作品类型": "图集",
            "作品标题": "无编号图集",
            "作品描述": "图文文案",
            "下载地址": ["https://ci.xiaohongshu.test/img-1.webp"],
        }
        info = self.capture.xiaohongshu_info_from_xhs(no_id, "https://xhslink.com/no-id")
        assert info is not None
        self.assertEqual("https://xhslink.com/no-id", info["webpage_url"])

    def test_xiaohongshu_info_from_xhs_empty_payload_returns_none(self) -> None:
        self.assertIsNone(self.capture.xiaohongshu_info_from_xhs({}, NOTE_URL))
        self.assertIsNone(self.capture.xiaohongshu_info_from_xhs({"作品类型": "未知"}, NOTE_URL))

    def test_assess_content_quality_gates_xhs_by_work_type(self) -> None:
        complete_ocr = {"word_count": 200, "processed_count": 3, "image_count": 3, "errors": []}
        quality, _ = self.capture.assess_content_quality(
            platform="小红书",
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
            platform="小红书",
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

        quality, _ = self.capture.assess_content_quality(
            platform="小红书",
            material_type="社媒链接",
            parse_status="已解析",
            description=LONG_DESCRIPTION,
            platform_meta={"图片数": 3},
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
            platform="小红书",
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
            work_type="未知",
        )
        self.assertEqual("需继续解析", quality)


if __name__ == "__main__":
    unittest.main()
