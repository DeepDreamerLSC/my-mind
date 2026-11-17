from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VISUALS = ROOT / ".codex/skills/feishu-publish/scripts/visual_evidence.py"
BUNDLE = ROOT / ".codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py"
CAPTURE = ROOT / ".codex/skills/inbox-capture/scripts/capture_link.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FeishuVisualEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(VISUALS.parent))
        cls.visuals = load_module(VISUALS, "visual_evidence")
        cls.bundle = load_module(BUNDLE, "test_visual_bundle")
        cls.capture = load_module(CAPTURE, "test_visual_capture")

    def test_video_timeline_uses_distributed_timestamp_frames(self) -> None:
        evidence = self.visuals.build_visual_evidence(
            metadata={
                "封面图": "https://cdn.example.test/cover.jpg",
                "来源链接": "https://video.example.test/watch/1",
            },
            body="""## 视频内容摘录

### 时间线摘录

- 00:00：开场和问题定义。
- 05:00：第一种做法。
- 10:00：第二种做法。
- 15:00：第三种做法。
- 20:00：总结和下一步。

### 转写摘录

完整转写。""",
            source_path=None,
            original_url="https://video.example.test/watch/1",
            max_visuals=4,
        )

        self.assertEqual(["cover", "video_frame", "video_frame", "video_frame"], [item.kind for item in evidence])
        self.assertEqual(["00:00", "10:00", "20:00"], [item.timecode for item in evidence[1:]])
        self.assertEqual(["媒体证据 01", "媒体证据 02", "媒体证据 03", "媒体证据 04"], [item.anchor for item in evidence])

    def test_image_post_reuses_cover_and_adds_only_distinct_source_images(self) -> None:
        evidence = self.visuals.build_visual_evidence(
            metadata={"封面图": "https://cdn.example.test/one.jpg"},
            body="""## 原始图片素材

- 来源图片 1：https://cdn.example.test/one.jpg
- 来源图片 2：https://cdn.example.test/two.jpg
""",
            source_path=None,
            original_url="https://source.example.test/note/1",
            max_visuals=4,
        )

        self.assertEqual(["cover", "source_image"], [item.kind for item in evidence])
        self.assertEqual("https://cdn.example.test/two.jpg", evidence[1].source_url)

    def test_local_markdown_image_is_available_as_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "note.md"
            image_path = root / "page.png"
            note_path.write_text("# Note\n", encoding="utf-8")
            image_path.write_bytes(b"x" * 128)

            evidence = self.visuals.build_visual_evidence(
                metadata={},
                body="![PDF page](page.png)",
                source_path=note_path,
                original_url="",
            )

        self.assertEqual(1, len(evidence))
        self.assertEqual("local_image", evidence[0].kind)
        self.assertEqual(str(image_path.resolve()), evidence[0].local_path)

    def test_rendered_page_has_visible_media_anchors(self) -> None:
        item = self.bundle.base.PushItem(index=1, title="视频示例", original_url="https://video.example.test/watch/1")
        evidence = self.visuals.build_visual_evidence(
            metadata={"封面图": "https://cdn.example.test/cover.jpg"},
            body="",
            source_path=None,
            original_url=item.original_url,
        )
        rendered = self.bundle.render_item_page(
            item,
            ROOT / "85_运行记录/前台推送-fixture.md",
            "2026-07-21 09:00:00 +0800",
            {"body": "可发布正文。", "path": "00_收件箱/fixture.md", "status": "已接入来源正本", "char_count": 6, "publish_ready": True},
            evidence,
        )

        self.assertIn("## 视觉证据", rendered)
        self.assertIn("### 媒体证据 01", rendered)
        self.assertIn("来源封面", rendered)

    def test_existing_page_falls_back_to_append_when_anchor_is_absent(self) -> None:
        evidence = self.visuals.VisualEvidence(
            evidence_id="cover-1",
            kind="cover",
            caption="来源封面",
            anchor="媒体证据 01",
            source_url="https://cdn.example.test/cover.jpg",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "cover.jpg"
            image_path.write_bytes(b"x" * 128)
            materialized = [self.visuals.MaterializedVisual(evidence=evidence, asset_path=image_path)]
            calls: list[str] = []
            original = self.bundle.run_media_insert_command

            def fake_insert(**kwargs):
                selection = str(kwargs.get("selection") or "")
                calls.append(selection)
                if selection:
                    return False, "anchor not found"
                return True, "appended"

            self.bundle.run_media_insert_command = fake_insert
            try:
                inserted, errors, output = self.bundle.embed_visual_evidence(
                    page_token="doc-token",
                    materialized=materialized,
                    existing={"page_token": "doc-token", "visual_embedded_ids": []},
                    width=960,
                )
            finally:
                self.bundle.run_media_insert_command = original

        self.assertEqual(["媒体证据 01", ""], calls)
        self.assertEqual(["cover-1"], inserted)
        self.assertEqual([], errors)
        self.assertEqual("anchor not found\nappended", output)

    def test_existing_page_does_not_retry_non_anchor_media_failure(self) -> None:
        evidence = self.visuals.VisualEvidence(
            evidence_id="cover-2",
            kind="cover",
            caption="来源封面",
            anchor="媒体证据 01",
            source_url="https://cdn.example.test/cover.jpg",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "cover.jpg"
            image_path.write_bytes(b"x" * 128)
            materialized = [self.visuals.MaterializedVisual(evidence=evidence, asset_path=image_path)]
            calls: list[str] = []
            original = self.bundle.run_media_insert_command

            def fake_insert(**kwargs):
                calls.append(str(kwargs.get("selection") or ""))
                return False, "permission denied"

            self.bundle.run_media_insert_command = fake_insert
            try:
                inserted, errors, _ = self.bundle.embed_visual_evidence(
                    page_token="doc-token",
                    materialized=materialized,
                    existing={"page_token": "doc-token", "visual_embedded_ids": []},
                    width=960,
                )
            finally:
                self.bundle.run_media_insert_command = original

        self.assertEqual(["媒体证据 01"], calls)
        self.assertEqual([], inserted)
        self.assertEqual(["来源封面：permission denied"], errors)

    def test_capture_persists_original_image_sources_for_future_publish(self) -> None:
        _, markdown = self.capture.build_note(
            "https://www.xiaohongshu.com/explore/fixture",
            {
                "title": "图文素材",
                "thumbnail": "https://cdn.example.test/one.jpg",
                "image_urls": ["https://cdn.example.test/one.jpg", "https://cdn.example.test/two.jpg"],
                "webpage_url": "https://www.xiaohongshu.com/explore/fixture",
                "original_url": "https://www.xiaohongshu.com/explore/fixture",
                "platform_meta": {"图片数": 2},
            },
            None,
        )

        self.assertIn('图片素材数: 2', markdown)
        self.assertIn("## 原始图片素材", markdown)
        self.assertIn("来源图片 2：https://cdn.example.test/two.jpg", markdown)


if __name__ == "__main__":
    unittest.main()
