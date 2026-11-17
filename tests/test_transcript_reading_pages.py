from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLISH_SCRIPTS = ROOT / ".codex/skills/feishu-publish/scripts"
READING = PUBLISH_SCRIPTS / "reading_version.py"
VISUALS = PUBLISH_SCRIPTS / "visual_evidence.py"
BUNDLE = PUBLISH_SCRIPTS / "publish_frontdesk_bundle.py"
BACKFILL = PUBLISH_SCRIPTS / "backfill_feishu_reading_pages.py"
CAPTURE = ROOT / ".codex/skills/inbox-capture/scripts/capture_link.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SHORT_BODY = """## 视频内容摘录

### 摘要

这段内容说明管理不是控制个人行为，而是围绕目标释放团队能量。管理者需要建立清晰方向、持续沟通和及时反馈，并把个人能力沉淀为团队可复用的流程、工具和文化。

### 关键点

- 管理的第一性原理是达成目标，而不是展示形式权力。
- 达成目标的关键是释放团队能量，而不是管控个人行为。
- 管理者应该通过方向、沟通和反馈帮助团队持续成长。
- 团队最终要沉淀流程、工具和文化，避免依赖少数骨干。

### 时间线摘录

- 00:00：管理的第一性原理是达成目标，而不是展示形式权力。达成目标需要释放团队能量。
- 00:35：管理者应该创造归属感和成长空间，通过持续沟通和及时反馈帮助团队调整方向。
- 01:10：团队最终要沉淀流程、工具和文化，让普通成员也能稳定创造结果。

### 转写摘录

管理的第一性原理是达成目标，不是形式权力。
达成目标的关键是释放团队能量，不是管控个人行为。
管理者应该创造归属感和成长空间，并保持持续沟通与及时反馈。
团队最终要沉淀流程、工具和文化，让普通成员也能稳定创造结果。
"""


LONG_BODY = """## 基础信息

- 时长：25:00

## 视频内容摘录

### 摘要

这段访谈讨论人工智能如何改变工程设计。核心不是用模型直接替代数值仿真，而是把高成本仿真放到更晚阶段，并在早期扩大设计探索空间，从而压缩开发周期并提高组织学习速度。

### 关键点

- 人工智能不会直接取代数值仿真，而会改变仿真在工程流程中的位置。
- 快速评估让工程团队能够在早期探索更多设计方案。
- 竞争优势来自更快的组织学习循环，而不只是单次模型精度。

### 时间线摘录

- 00:00：讲者先解释传统工程设计依赖少量高成本仿真，因此早期探索空间受到限制。
- 05:00：物理感知模型能够从历史仿真和测试数据中学习，在几分钟内评估新的三维设计。
- 10:00：捷豹路虎的案例说明每日空气动力学评估次数可以显著增加，工程师因此能比较更多方向。
- 15:00：人工智能没有消除数值仿真，而是让高精度仿真转移到更晚、更值得验证的候选方案上。
- 20:00：长期竞争优势来自更快的设计反馈和组织学习循环，而不只是购买一个模型工具。

### 转写摘录

传统工程设计通常依赖少量高成本仿真，所以团队在早期只能探索有限的设计方向。
物理感知模型从历史仿真与测试数据中学习，可以在几分钟内评估新的三维设计。
这让工程师能够在同样时间内比较更多候选方向，并更早发现不可行的方案。
捷豹路虎的案例显示，每日空气动力学评估次数能够显著增加，设计探索因此从串行变成更接近并行。
人工智能并没有消除高精度数值仿真，而是把它移动到更晚、更值得验证的候选方案上。
工程团队仍然需要物理知识、实验数据和质量控制，否则快速迭代只会更快地产生错误。
真正的竞争优势来自更短的设计反馈周期，以及组织能否把每轮试验转化成下一轮决策。
这种能力会影响汽车、制造和其他复杂物理产品的开发速度。
"""


class TranscriptReadingPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(PUBLISH_SCRIPTS))
        cls.reading = load_module(READING, "reading_version")
        cls.visuals = load_module(VISUALS, "test_reading_visuals")
        cls.bundle = load_module(BUNDLE, "test_reading_bundle")
        cls.backfill = load_module(BACKFILL, "test_reading_backfill")
        cls.capture = load_module(CAPTURE, "test_reading_capture")

    def test_short_transcript_builds_inline_layered_reading_view(self) -> None:
        view = self.reading.build_reading_version(
            metadata={"时长": "01:46", "语言": "zh"},
            body=SHORT_BODY,
            item_summary="管理不是控制个人，而是围绕目标释放团队能量，并通过方向、沟通、反馈和团队系统让普通成员持续创造结果。",
        )

        self.assertIsNotNone(view)
        assert view is not None
        self.assertEqual("inline", view.transcript_mode)
        self.assertGreaterEqual(len(view.conclusion), 100)
        self.assertLessEqual(len(view.conclusion), 200)
        self.assertGreaterEqual(len(view.claims), 3)
        self.assertLessEqual(len(view.claims), 7)
        self.assertGreaterEqual(len(view.chapters), 3)
        self.assertTrue(all(not claim.statement.endswith(("…", "...")) for claim in view.claims))
        self.assertTrue(all(claim.evidence for claim in view.claims))
        self.assertEqual(len(view.claims), len({claim.statement for claim in view.claims}))
        self.assertTrue(all(len(chapter.summary) >= 150 for chapter in view.chapters))
        self.assertTrue(all(len(chapter.title) <= 42 for chapter in view.chapters))
        self.assertTrue(all(not chapter.title.endswith(("…", "...")) for chapter in view.chapters))

    def test_long_transcript_uses_separate_transcript_page(self) -> None:
        view = self.reading.build_reading_version(metadata={}, body=LONG_BODY, item_summary="工程人工智能通过扩大早期设计探索和缩短反馈周期改变产品开发。")

        self.assertIsNotNone(view)
        assert view is not None
        self.assertEqual("separate", view.transcript_mode)
        transcript_page = self.reading.transcript_markdown("工程设计访谈", view)
        self.assertIn("# 完整转写｜工程设计访谈", transcript_page)
        self.assertIn("传统工程设计通常依赖少量高成本仿真", transcript_page)

        item = self.bundle.base.PushItem(index=1, title="工程设计访谈", summary="工程人工智能改变设计反馈周期。")
        rendered = self.bundle.render_item_page(
            item,
            ROOT / "85_运行记录/前台推送-fixture.md",
            "2026-07-23 08:00:00 +0800",
            {
                "reading_version": view,
                "publish_ready": True,
                "path": "00_收件箱/fixture.md",
                "status": "已接入来源正本",
                "char_count": len(LONG_BODY),
            },
            [],
            "https://my.feishu.cn/wiki/transcript",
        )
        self.assertIn("## 30 秒结论", rendered)
        self.assertIn("[查看按语义章节整理的完整转写](https://my.feishu.cn/wiki/transcript)", rendered)
        self.assertNotIn("本页保留完整转写证据", rendered)
        self.assertLess(rendered.index("## 来源与质量"), rendered.index("## 处理边界"))

    def test_visual_captions_use_complete_claims_and_time_ranges(self) -> None:
        view = self.reading.build_reading_version(metadata={"时长": "01:46"}, body=SHORT_BODY)
        assert view is not None
        evidence = self.visuals.build_visual_evidence(
            metadata={"来源链接": "https://video.example.test/watch/1"},
            body=SHORT_BODY,
            source_path=None,
            original_url="https://video.example.test/watch/1",
            reading_version=view,
        )

        self.assertGreaterEqual(len(evidence), 2)
        frames = [item for item in evidence if item.kind == "video_frame"]
        self.assertGreaterEqual(len(frames), 2)
        self.assertTrue(all(item.end_timecode for item in frames))
        self.assertEqual(len(frames), len({(item.timecode, item.end_timecode) for item in frames}))
        self.assertTrue(all(not item.caption.endswith(("…", "...")) for item in evidence))
        self.assertTrue(any(view.claims[0].statement in item.caption for item in evidence))

    def test_chapter_list_without_transcript_is_blocked(self) -> None:
        body = """## 官方转录来源

- 链接：https://example.test/transcript

## 章节目录

- 00:00：开场和背景
- 10:00：关键观点...
"""
        assessment = self.bundle.assess_source_material(
            {"内容摘录来源": "RSS/Atom 摘要"},
            body,
            len(body),
        )

        self.assertFalse(assessment["ready"])
        self.assertIn("当前只有转录来源或章节目录，尚未保存正文级完整转写。", assessment["blockers"])

    def test_include_incomplete_page_cannot_look_like_formal_reading_page(self) -> None:
        item = self.bundle.base.PushItem(index=7, title="截断音频", summary="只有一小段简介...")
        rendered = self.bundle.render_item_page(
            item,
            ROOT / "85_运行记录/前台推送-fixture.md",
            "2026-07-23 08:00:00 +0800",
            {
                "publish_ready": False,
                "publish_blockers": ["当前只有 RSS/Atom 摘要。"],
                "path": "00_收件箱/incomplete.md",
                "status": "已接入来源正本",
                "char_count": 120,
            },
        )

        self.assertTrue(rendered.startswith("# 待核验｜截断音频"))
        self.assertIn("不能作为正式精选", rendered)
        self.assertNotIn("## 核心观点", rendered)

    def test_english_transcript_without_chinese_claims_is_blocked(self) -> None:
        view = self.reading.build_reading_version(
            metadata={"时长": "04:00", "语言": "en"},
            body="""### Transcript

The speaker explains why fast feedback loops improve engineering decisions. Teams should preserve source evidence, compare alternatives, and delay expensive validation until the strongest candidates remain. The final section describes how organizations convert repeated experiments into reusable operating knowledge.
""",
        )
        self.assertIsNotNone(view)
        gated = self.bundle.apply_reading_quality_gate(
            {"ready": True, "status": "可发布正本", "blockers": []},
            view,
        )

        self.assertFalse(gated["ready"])
        self.assertIn("英文内容尚缺中文核心观点，需要先完成中文结构化阅读版。", gated["blockers"])

    def test_capture_summary_uses_full_transcript_and_semantic_timeline(self) -> None:
        text = "\n".join(
            [
                "开场介绍今天的主题和背景。",
                "第一部分讨论传统流程为什么限制方案探索。",
                "第二部分解释模型如何从历史仿真数据中学习。",
                "第三部分给出汽车工程团队扩大评估次数的案例。",
                "最后总结竞争优势来自更快的组织学习循环。",
            ]
        )
        summary = self.capture.summarize_transcript_text(text)
        self.assertIn("组织学习循环", summary["summary"])
        self.assertLessEqual(len(summary["key_points"]), 7)
        self.assertTrue(all(not point.endswith(("…", "...")) for point in summary["key_points"]))

        segments = [
            {"time": "00:00", "text": "开场介绍问题背景和传统流程。"},
            {"time": "01:00", "text": "首先讨论传统流程限制设计探索。"},
            {"time": "02:00", "text": "接下来解释模型如何学习历史数据。"},
            {"time": "03:00", "text": "然后介绍汽车工程团队的真实案例。"},
            {"time": "04:00", "text": "最后总结组织学习循环的重要性。"},
        ]
        timeline = self.capture.build_transcript_timeline(segments)
        self.assertGreaterEqual(len(timeline), 3)
        self.assertLessEqual(len(timeline), 8)
        self.assertTrue(all("end_time" in entry for entry in timeline))

    def test_core_claims_reject_deictic_fragments(self) -> None:
        candidates = self.reading.claim_candidates(
            ["它就是允许用户在任务过程中提问", "这就代表后面会自动处理", "核心价值是缩短反馈周期"]
        )

        self.assertEqual(["核心价值是缩短反馈周期。"], candidates)

    def test_backfill_detects_idempotent_reading_version(self) -> None:
        view = self.reading.build_reading_version(metadata={"时长": "01:46"}, body=SHORT_BODY)
        assert view is not None
        item = self.bundle.base.PushItem(index=1, title="短视频", source_file="00_收件箱/short.md")
        record = {
            "source_file": item.source_file,
            "source_push": "85_运行记录/前台推送-fixture.md",
            "reading_version_hash": view.digest(),
            "transcript_content_hash": "",
        }
        candidate = self.backfill.RebuildCandidate(
            record=record,
            item=item,
            material={"publish_ready": True},
            reading_view=view,
        )

        self.assertTrue(candidate.eligible)
        self.assertFalse(candidate.needs_rebuild)

    def test_backfill_counts_incomplete_media_as_blocked(self) -> None:
        item = self.bundle.base.PushItem(
            index=2,
            title="只有 RSS 摘要的视频",
            source="YouTube",
            source_file="00_收件箱/incomplete-video.md",
            original_url="https://youtube.com/watch?v=example",
        )
        candidate = self.backfill.RebuildCandidate(
            record={"source_file": item.source_file, "source_push": "85_运行记录/前台推送-fixture.md"},
            item=item,
            material={"publish_ready": False},
            reading_view=None,
        )

        self.assertTrue(candidate.media_like)
        self.assertFalse(candidate.eligible)

    def test_unchanged_transcript_page_is_reused_without_write_command(self) -> None:
        result = self.bundle.publish_one(
            existing={
                "content_hash": "same-hash",
                "feishu_url": "https://my.feishu.cn/wiki/transcript",
                "page_token": "page-token",
                "wiki_node_token": "node-token",
                "wiki_parent_node_token": "parent-token",
            },
            title="完整转写｜验收样例",
            draft_path=ROOT / "tests/fixtures/transcript.md",
            push_path=ROOT / "85_运行记录/前台推送-fixture.md",
            record_file=ROOT / "85_运行记录/飞书发布记录.jsonl",
            digest="same-hash",
            create_command="must-not-run-create",
            update_command="must-not-run-update",
            wiki_space_id="space-id",
            wiki_parent_node_token="parent-token",
            wiki_move_command="",
            force=False,
        )

        self.assertEqual("reused", result[0])
        self.assertEqual("https://my.feishu.cn/wiki/transcript", result[1])


if __name__ == "__main__":
    unittest.main()
