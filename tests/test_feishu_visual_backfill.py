from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VISUALS = ROOT / ".codex/skills/feishu-publish/scripts/visual_evidence.py"
BACKFILL = ROOT / ".codex/skills/feishu-publish/scripts/backfill_feishu_visuals.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FeishuVisualBackfillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(VISUALS.parent))
        cls.visuals = load_module(VISUALS, "visual_evidence")
        cls.backfill = load_module(BACKFILL, "test_visual_backfill")

    def test_latest_pages_keep_only_latest_success_per_source(self) -> None:
        frontdesk_pages = self.backfill.latest_frontdesk_pages(
            [
                {
                    "record_type": "frontdesk_item",
                    "status": "已发布",
                    "item_identity": "inbox/a.md",
                    "source_file": "inbox/a.md",
                    "title": "旧页",
                    "page_token": "doc-old",
                },
                {
                    "record_type": "frontdesk_item",
                    "status": "草稿已生成",
                    "item_identity": "inbox/a.md",
                    "source_file": "inbox/a.md",
                    "title": "草稿",
                    "page_token": "doc-draft",
                },
                {
                    "record_type": "frontdesk_item",
                    "status": "已发布",
                    "item_identity": "inbox/a.md",
                    "source_file": "inbox/a.md",
                    "title": "新页",
                    "page_token": "doc-new",
                },
            ]
        )
        library_pages = self.backfill.latest_library_pages(
            [
                {"status": "已同步", "source_file": "20_资料库/a.md", "title": "A", "page_token": "doc-a"},
                {"status": "失败", "source_file": "20_资料库/a.md", "title": "A", "page_token": "doc-failed"},
                {"status": "已同步", "source_file": "20_资料库/b.md", "title": "B", "feishu_url": "https://my.feishu.cn/docx/doc-b"},
            ]
        )

        self.assertEqual(["doc-new"], [page.page_token for page in frontdesk_pages])
        self.assertEqual(["20_资料库/a.md", "20_资料库/b.md"], [page.source_file for page in library_pages])

    def test_prior_visual_ids_unions_original_and_backfill_records(self) -> None:
        page = self.backfill.ExistingPage(
            lane="前台精选",
            source_file="00_收件箱/a.md",
            title="示例",
            original_url="",
            page_token="doc-a",
            feishu_url="",
            record={"visual_embedded_ids": ["cover", "frame-1"]},
        )
        records = [
            {
                "record_type": "feishu_visual_backfill",
                "page_identity": page.identity,
                "visual_embedded_ids": ["frame-2"],
            },
            {
                "record_type": "feishu_visual_backfill",
                "page_identity": "other",
                "visual_embedded_ids": ["ignore"],
            },
        ]

        self.assertEqual({"cover", "frame-1", "frame-2"}, self.backfill.prior_visual_ids(records, page))

    def test_dry_run_reports_pending_visuals_without_writing_backfill_record(self) -> None:
        record = {
            "record_type": "frontdesk_item",
            "status": "已发布",
            "item_identity": "00_收件箱/a.md",
            "source_file": "00_收件箱/a.md",
            "title": "待补图页面",
            "page_token": "doc-a",
        }
        evidence = self.visuals.VisualEvidence(
            evidence_id="cover-a",
            kind="cover",
            caption="来源封面",
            anchor="媒体证据 01",
            source_url="https://cdn.example.test/cover.jpg",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontdesk_records = root / "frontdesk.jsonl"
            library_records = root / "library.jsonl"
            backfill_records = root / "backfill.jsonl"
            frontdesk_records.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            library_records.write_text("", encoding="utf-8")
            argv = sys.argv
            output = io.StringIO()
            with (
                mock.patch.object(
                    self.backfill.bundle,
                    "load_source_material",
                    return_value={"publish_status": "可发布正本", "publish_ready": True},
                ),
                mock.patch.object(self.backfill.bundle, "build_item_visual_evidence", return_value=[evidence]),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "backfill_feishu_visuals.py",
                        "--dry-run",
                        "--frontdesk-record-file",
                        str(frontdesk_records),
                        "--library-record-file",
                        str(library_records),
                        "--backfill-record-file",
                        str(backfill_records),
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(0, self.backfill.main())
            self.assertFalse(backfill_records.exists())

        self.assertIn("待插入：1", output.getvalue())
        self.assertIn("待补图页面：待补 1 张", output.getvalue())
        self.assertIs(argv, sys.argv)


if __name__ == "__main__":
    unittest.main()
