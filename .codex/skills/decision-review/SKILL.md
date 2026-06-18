---
name: decision-review
description: Review my-mind managed project decisions, assumptions, bias traps, opportunity cost, deviation risk, and smallest reversible next actions. Use when the user asks 决策审视, 外部视角, 反方意见, 思维定势, 项目偏航, decision review, or wants Codex/OpenClaw automation to challenge current project direction before more coding or knowledge work.
---

# 决策审视

## Overview

Use this skill to create an external-view decision review for a managed project. It reads project files, recent project-progress reports, git evidence, and run records, then asks whether the project is still serving the target or just deepening a local habit.

The skill is an operating review, not long-term knowledge. It does not edit project conclusions, modify code, confirm candidates, commit, push, or send Feishu messages.

## Quick Start

Preview the default project:

```bash
python3 .codex/skills/decision-review/scripts/review_decisions.py
```

Write a timestamped report and refresh the fixed latest panel:

```bash
python3 .codex/skills/decision-review/scripts/review_decisions.py --write --project my-mind
```

Review `edu-agent`:

```bash
python3 .codex/skills/decision-review/scripts/review_decisions.py --write --project edu-agent
```

Review all managed projects:

```bash
python3 .codex/skills/decision-review/scripts/review_decisions.py --write --project all
```

## Inputs

- `10_项目/<项目>/项目总览.md`
- `10_项目/<项目>/任务清单.md`
- `10_项目/<项目>/风险清单.md`
- `10_项目/<项目>/问题清单.md`
- `10_项目/<项目>/决策记录.md`
- Latest `85_运行记录/项目进展巡检-<project>-*.md`
- Latest `85_运行记录/建议分析-*.md`
- Git commits and working tree state for the managed project repo

## Outputs

`--write` creates:

- `85_运行记录/决策审视-<project>-YYYY-MM-DD-HHMM.md`
- `85_运行记录/后台总览/决策审视-<project>.md`
- `85_运行记录/后台总览/决策审视.md`

The report includes:

- 总览
- 当前隐含假设
- 反方视角
- 偏航风险
- 机会成本
- 下个最小可逆动作
- OpenClaw 可提醒问题
- 证据来源

## Review Rules

- Treat recent commits as evidence, not proof of correct direction.
- Treat automation output as operating state, not project value by itself.
- Prefer smaller reversible moves when assumptions are weak.
- Flag over-automation when the project spends more effort on tooling than on the user's target outcome.
- Flag local-optimum risk when many files change but the project goal, decision record, or risk list does not move.
- Ask OpenClaw to interrupt the user only for high-risk direction questions or decisions the user must own.
- Keep the final recommendation practical: one smallest next action and one thing to stop doing for now.

## Boundaries

- Do not edit `项目进展.md`, `决策记录.md`, `任务清单.md`, or code.
- Do not auto-approve decisions.
- Do not auto-commit or push.
- Do not send Feishu messages directly.
- Use the best available high-reasoning model when this skill is run by Codex automation; the script gathers evidence, while the automation prompt should still ask Codex to read the generated report and apply critical judgment.
