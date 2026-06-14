#!/usr/bin/env python3
"""Prepare concise OpenClaw-facing messages from advice analysis."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import analyze_advice


ROOT = analyze_advice.ROOT
OPENCLAW_ADVICE = analyze_advice.DASHBOARD_DIR / "OpenClaw行动建议.md"


def is_openclaw_item(suggestion: analyze_advice.Suggestion) -> bool:
    if suggestion.status in {"已执行", "已忽略"}:
        return False
    return (
        "OpenClaw" in suggestion.owner
        or suggestion.owner == "用户"
        or suggestion.domain in {"候选确认", "前台协作"}
    )


def suggested_replies(suggestion: analyze_advice.Suggestion) -> str:
    if suggestion.domain == "候选确认":
        return "确认转正 / 继续核验 / 调整分类 / 跳过"
    if suggestion.domain == "前台协作":
        return "已读：想法 / 沉淀成提示词 / 跳过"
    return "知道了 / 稍后处理"


def render_message(suggestions: list[analyze_advice.Suggestion]) -> str:
    generated_at = analyze_advice.now().strftime("%Y-%m-%d %H:%M:%S %z")
    lines = [
        "# OpenClaw行动建议",
        "",
        f"- 更新时间：{generated_at}",
        "- 用途：只转发下面的短消息，不转发完整建议分析报告。",
        "",
        "## 可转发消息",
        "",
    ]
    if not suggestions:
        lines.append("当前没有需要主动触达用户的事项。")
    for index, suggestion in enumerate(suggestions, 1):
        lines.extend(
            [
                f"### 消息 {index}",
                "",
                f"{index}. [{suggestion.priority}] {suggestion.action}",
                f"- 建议说法：{suggestion.next_step}",
                f"- 可回复：{suggested_replies(suggestion)}",
                f"- 记录键：`{suggestion.record_key}`",
                "",
            ]
        )
    lines.extend(
        [
            "## 边界",
            "",
            "- 不要转发后台配置、自动化日志或完整项目巡检。",
            "- 不要替用户确认转正，不要直接改长期知识正文。",
            "- 如果需要后台处理，只说“已交给 Codex 后台处理”。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare short OpenClaw-facing advice messages.")
    parser.add_argument("--project", default="all", help="Project filter passed to advice analysis.")
    parser.add_argument("--limit", type=int, default=3, help="Maximum messages to show.")
    parser.add_argument("--write", action="store_true", help="Write 85_运行记录/后台总览/OpenClaw行动建议.md.")
    parser.add_argument("--mark-reminded", action="store_true", help="Mark rendered advice items as 已提醒 in advice state.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    context, suggestions = analyze_advice.build_advice(args.project, max(args.limit * 3, args.limit))
    analyze_advice.apply_state(suggestions, write=False)
    selected = [item for item in suggestions if is_openclaw_item(item)][: max(args.limit, 0)]
    if args.mark_reminded and selected:
        keys = {item.record_key or analyze_advice.suggestion_key(item) for item in selected}
        analyze_advice.apply_state(suggestions, write=True, mark_reminded=keys)
        selected = [item for item in suggestions if (item.record_key or analyze_advice.suggestion_key(item)) in keys]
    message = render_message(selected)
    print(message, end="")
    if args.write:
        OPENCLAW_ADVICE.parent.mkdir(parents=True, exist_ok=True)
        OPENCLAW_ADVICE.write_text(message, encoding="utf-8")
        print(f"\n已写入 OpenClaw 行动建议：{analyze_advice.repo_rel(OPENCLAW_ADVICE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
