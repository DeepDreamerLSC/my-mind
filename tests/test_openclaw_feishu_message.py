from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".codex/skills/feishu-publish/scripts/build_openclaw_feishu_message.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class OpenClawFeishuMessageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.message = load_module(SCRIPT, "test_openclaw_feishu_message_module")

    def test_highlight_prefers_reading_conclusion_and_labels_link(self) -> None:
        record = {
            "record_type": "frontdesk_bundle_index",
            "title": "今日精选",
            "item_count": 1,
            "wiki_node_token": "bundle-token",
            "items": [
                {
                    "index": 1,
                    "title": "管理者如何建立权威",
                    "summary": "管理者如何建立权威",
                    "reading_brief": "核心不是提高音量，而是用清晰边界、一致反馈和可预期后果建立稳定权威。",
                    "wiki_url": "https://my.feishu.cn/wiki/item-token",
                }
            ],
        }

        rendered = self.message.render_message(record, max_items=3, summary_chars=90)

        self.assertIn("简述：核心不是提高音量", rendered)
        self.assertIn("飞书：https://my.feishu.cn/wiki/item-token", rendered)
        self.assertNotIn("\n   管理者如何建立权威\n", rendered)

    def test_old_record_falls_back_to_non_duplicate_summary(self) -> None:
        lines = self.message.item_lines(
            {
                "items": [
                    {
                        "index": 2,
                        "title": "旧记录标题",
                        "summary": "这是一条旧记录的说明。",
                        "feishu_url": "https://my.feishu.cn/docx/legacy",
                    }
                ]
            },
            max_items=3,
            summary_chars=90,
        )

        self.assertEqual(
            ["2. 旧记录标题\n   简述：这是一条旧记录的说明。\n   飞书：https://my.feishu.cn/docx/legacy"],
            lines,
        )


if __name__ == "__main__":
    unittest.main()
