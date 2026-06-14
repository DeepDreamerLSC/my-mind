---
name: advice-analysis
description: Generate prioritized my-mind action advice from backend dashboard data, project progress reports, flow queues, pending confirmations, and run records. Use when the user asks 建议分析, 行动建议, 接下来做什么, 优先级分析, recommendation analysis, or wants Codex/OpenClaw to convert collected evidence into next safe actions.
---

# 建议分析

## Overview

Use this skill to turn already collected my-mind evidence into a prioritized action list. It reads existing reports and structured dashboard data, then answers: what should happen next, who should do it, why it matters, and which evidence supports the advice.

This skill does not promote knowledge, edit inbox notes, commit code, or publish to Feishu. It only generates analysis reports and a fixed latest advice panel.

## Quick Start

Generate a preview:

```bash
python3 .codex/skills/advice-analysis/scripts/analyze_advice.py
```

Write a timestamped report and refresh the fixed latest panel:

```bash
python3 .codex/skills/advice-analysis/scripts/analyze_advice.py --write
```

`--write` also injects a `当前行动建议` table into local Feishu dashboard data.

Prepare a short OpenClaw-facing message:

```bash
python3 .codex/skills/advice-analysis/scripts/prepare_openclaw_advice_message.py --write
```

After OpenClaw actually sends the message, it may mark rendered items as reminded:

```bash
python3 .codex/skills/advice-analysis/scripts/prepare_openclaw_advice_message.py --write --mark-reminded
```

Limit to one project:

```bash
python3 .codex/skills/advice-analysis/scripts/analyze_advice.py --project edu-agent --write
```

Output JSON for another tool:

```bash
python3 .codex/skills/advice-analysis/scripts/analyze_advice.py --format json
```

## Inputs

- `85_运行记录/后台总览/飞书仪表盘数据.json`
- `85_运行记录/后台总览/当前后台状态.md`
- `85_运行记录/后台总览/OpenClaw待提醒.md`
- `85_运行记录/项目进展巡检-*.md`
- `10_项目/<项目>/任务清单.md`
- `10_项目/<项目>/风险清单.md`
- `10_项目/<项目>/问题清单.md`

## Outputs

`--write` creates:

- `85_运行记录/建议分析-YYYY-MM-DD-HHMM.md`
- `85_运行记录/后台总览/当前行动建议.md`
- `85_运行记录/后台总览/OpenClaw行动建议.md`
- `85_运行记录/建议分析状态.json`

The report must include:

- 总览
- 优先建议
- 按角色拆分
- OpenClaw 可转发摘要
- 证据来源

The dashboard export is injected into:

- `85_运行记录/后台总览/飞书仪表盘数据.json`
- `85_运行记录/后台总览/飞书仪表盘数据/当前行动建议.csv`

## Advice Rules

- User-facing advice should stay small. If several items require the user, ask OpenClaw to batch them instead of pushing everything at once.
- Codex advice should favor backend maintenance, parse-quality repair, project worktree cleanup, candidate distillation, and evidence hygiene.
- OpenClaw advice should focus on presenting Feishu links, pending confirmations, and short feedback prompts.
- Treat `待沉淀` total as inventory only. Generate automatic distillation advice only from `待沉淀待消费`; route `已有候选待确认` to OpenClaw/user confirmation and `待补判断` to parse or judgement repair.
- Project advice must use project reports as evidence, not raw commit lists alone.
- If a project has many dirty files, suggest整理和分批提交 before writing project conclusions.
- If parse quality, OCR, transcription, or environment files are involved, treat quality and safety as higher priority than speed.

## Boundaries

- Do not run `--apply` on project progress reports.
- Do not auto-confirm pending knowledge.
- Do not auto-commit or push.
- Do not send Feishu messages directly.
- Do not treat generated advice as long-term knowledge; it is an operating view.
