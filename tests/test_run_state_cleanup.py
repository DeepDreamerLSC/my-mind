from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".codex/skills/run-state-cleanup/scripts/plan_run_state_cleanup.py"


def load_module():
    spec = importlib.util.spec_from_file_location("test_run_state_cleanup_module", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunStateCleanupTest(unittest.TestCase):
    def test_external_identifiers_are_reported_without_values(self) -> None:
        cleanup = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            secret_value = "redacted_external_identifier_001"
            (run_dir / "publish-record.jsonl").write_text(
                '{"wiki_node_token":"' + secret_value + '","space_id":"redacted_space"}\n',
                encoding="utf-8",
            )

            candidates = cleanup.external_identifier_candidates(run_dir)
            plan = cleanup.build_plan([], sample_limit=2, external_ids=candidates)
            report = cleanup.render_report(plan, mode="dry-run")

            self.assertEqual(["space_id", "wiki_node_token"], candidates[0]["fields"])
            self.assertIn("外部对象标识迁移计划", report)
            self.assertNotIn(secret_value, report)


if __name__ == "__main__":
    unittest.main()
