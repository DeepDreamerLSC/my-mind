from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
FRONTIER = ROOT / ".codex/skills/frontier-watch/scripts/frontier_watch.py"
TRIAGE = ROOT / ".codex/skills/inbox-triage/scripts/triage_inbox.py"
BUNDLE = ROOT / ".codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py"
PUSH = ROOT / ".codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class EducationCollectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frontier = load_module(FRONTIER, "test_education_frontier")
        cls.triage = load_module(TRIAGE, "test_education_triage")
        cls.push = load_module(PUSH, "test_education_frontdesk_push")
        sys.path.insert(0, str(BUNDLE.parent))
        cls.bundle = load_module(BUNDLE, "test_education_bundle")

    def test_frontier_watch_labels_and_routes_education(self) -> None:
        source = self.frontier.FeedSource(
            name="Education fixture",
            url="https://example.invalid/education.xml",
            category_hints=["教育与学习"],
            quality=4,
        )
        item = self.frontier.FeedItem(
            source=source,
            title="AI tutoring for formative assessment in the classroom",
            url="https://example.invalid/article",
            summary="Teachers used an adaptive learning tool with students.",
            published=dt.datetime.now(ZoneInfo("Asia/Shanghai")),
        )

        self.frontier.score_item(item, lookback_days=14)

        self.assertIn("教育与学习", item.categories)
        self.assertIn("20_资料库/教育与学习/", self.frontier.suggested_destination(item.categories))
        self.assertIn("10_项目/edu-agent/", self.frontier.suggested_destination(item.categories))
        self.assertIn("  - edu-agent", self.frontier.render_inbox_note(item))

    def test_frontier_watch_removes_html_embedded_in_feed_attributes(self) -> None:
        summary = (
            '<figure><img data-caption="&lt;p&gt;image caption&lt;/p&gt;" '
            'data-large-file="https://example.invalid/image" /></figure><p>Readable education summary.</p>'
        )

        cleaned = self.frontier.clean_text(summary)

        self.assertEqual("Readable education summary.", cleaned)

    def test_triage_routes_general_education_to_its_library(self) -> None:
        note = self.triage.InboxNote(
            path=Path("00_收件箱/education.md"),
            frontmatter={"标题": "课堂教学中的形成性评价", "解析状态": "已解析", "标签": ["教育与学习"]},
            body="讨论教师如何通过教学设计和课程反馈改进学生学习。",
        )

        result = self.triage.triage_note(note)

        self.assertIn(self.triage.KNOWLEDGE_EDUCATION_DESTINATION, result.destinations)
        self.assertNotIn(self.triage.EDU_AGENT_PROJECT_DESTINATION, result.destinations)

    def test_triage_links_education_ai_material_to_edu_agent(self) -> None:
        note = self.triage.InboxNote(
            path=Path("00_收件箱/edu-agent.md"),
            frontmatter={"标题": "教育 AI 如何改进错题整理和组卷", "解析状态": "已解析"},
            body="文章讨论 OCR、题目裁切、教育测评和智能辅导的产品设计。",
        )

        result = self.triage.triage_note(note)

        self.assertIn(self.triage.KNOWLEDGE_EDUCATION_DESTINATION, result.destinations)
        self.assertIn(self.triage.EDU_AGENT_PROJECT_DESTINATION, result.destinations)

    def test_feishu_bundle_uses_education_parent_when_configured(self) -> None:
        item = SimpleNamespace(
            title="AI tutoring for classroom assessment",
            source="Education fixture",
            source_file="00_收件箱/education.md",
            summary="Education AI fixture",
            excerpt="",
            original_url="https://example.invalid/article",
            share_url="",
        )

        parent, directory, reason = self.bundle.infer_item_parent(
            item,
            {"20_资料库": "root", "20_资料库/教育与学习": "education"},
        )

        self.assertEqual("education", parent)
        self.assertEqual("20_资料库/教育与学习", directory)
        self.assertIn("教育", reason)

    def test_feishu_bundle_does_not_create_parent_for_unmapped_education_keywords(self) -> None:
        item = SimpleNamespace(
            title="AI tutoring for classroom assessment",
            source="Education fixture",
            source_file="00_收件箱/education.md",
            summary="Education AI fixture",
            excerpt="",
            original_url="https://example.invalid/article",
            share_url="",
        )
        parent_map = {"20_资料库": "root"}

        parent, directory, reason = self.bundle.infer_item_parent(item, parent_map)

        self.assertEqual("root", parent)
        self.assertEqual("20_资料库", directory)
        self.assertIn("不创建飞书目录", reason)
        self.assertEqual({"20_资料库": "root"}, parent_map)

    def test_frontdesk_recognizes_english_education_material(self) -> None:
        meta = {"标题": "Anthropic Introduces Claude for Teachers", "阅读状态": "未读"}
        body = "The AI rollout is aimed at educators and school districts."

        self.assertIn("教育与学习", self.push.note_value(meta, body))
        self.assertIn("20_资料库/教育与学习/", self.push.distill_direction(meta, body))


if __name__ == "__main__":
    unittest.main()
