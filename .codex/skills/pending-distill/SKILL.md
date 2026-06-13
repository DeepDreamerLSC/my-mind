---
name: pending-distill
description: Consume my-mind pending distillation queues into candidate knowledge and confirmation items. Use when the user asks to 消费待沉淀, 自动沉淀待处理资料, clear pending distillation, generate candidate knowledge from 05_流转区/30_待沉淀, build 今日待确认, or run the backend distillation automation for OpenClaw/Codex. Writes 05_流转区/50_待确认, 85_运行记录/待确认事项.jsonl, and 待沉淀消费 reports; never directly promotes long-term knowledge.
---

# Pending Distill

Consume `my-mind` 待沉淀 items into candidate artifacts and a separate confirmation queue.

## Quick Start

Preview pending work without writing:

```bash
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py
```

Consume and write outputs:

```bash
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py --write
```

Limit one trial item:

```bash
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py --write --limit 1
```

Process a specific inbox source:

```bash
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py \
  --write \
  --source "00_收件箱/某条笔记.md"
```

Force a target type:

```bash
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py --write --target prompt
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py --write --target library
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py --write --target insight
```

## Behavior

The script:

- Reads `00_收件箱` notes currently in `处理状态: 已分拣`.
- Selects items whose flow phase is `已阅读待生成候选`, `已有候选待确认`, or `已读待补判断`.
- Reuses `knowledge-intake` candidate generation instead of duplicating distillation logic.
- Generates candidate files only when the source is readable, non-sensitive, and not already linked to a candidate.
- Writes all generated or blocked items into a separate confirmation queue.
- Refreshes `05_流转区` after write mode unless `--no-flow` is passed.

## Outputs

Write mode creates or updates:

```text
05_流转区/50_待确认/待确认队列.md
85_运行记录/待确认事项.jsonl
85_运行记录/待沉淀消费-YYYY-MM-DD-HHMM.md
```

Candidate files may be created in:

```text
20_资料库/
75_提示词库/
65_洞察/候选洞察/
```

depending on the triage result and `--target`.

## Confirmation Boundary

This skill does not directly promote long-term knowledge.

Generated candidates remain `候选 / 待确认 / 待核验`. User-facing confirmation should happen through OpenClaw as a separate “今日待确认” message, not mixed into “今日精选待读”.

Recommended OpenClaw reply forms:

```text
1 确认转正
1 继续核验
1 调整分类：提示词
1 跳过
1 我的判断是：...
```

Codex backend consumes those replies through `frontdesk-feedback`, runs the promotion gate, then updates source notes, candidate notes, confirmation queue status, and Feishu sync state.

## Automation

Use this as the execution unit for scheduled distillation:

```bash
python3 .codex/skills/pending-distill/scripts/consume_pending_distill.py --write --limit 5
```

Automation should call the skill script only. Keep business judgment here, not inside the scheduler.

Suggested cadence:

- Every 6 hours for a trial period.
- Use `--limit 5` while the queue is noisy.
- Remove the limit only after confirmation handling is stable.

## Work Boundaries

- Do not delete inbox sources.
- Do not move long-term knowledge without the promotion gate.
- Do not mark candidates as formal knowledge.
- Do not publish confirmation items to Feishu directly; `frontdesk-feedback --sync-feishu` handles Feishu sync only after confirmed promotion.
- Do not treat generated candidates as user-approved. They are backend proposals.
