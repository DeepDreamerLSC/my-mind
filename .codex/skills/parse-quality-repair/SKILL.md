---
name: parse-quality-repair
description: Repair and audit low-quality my-mind inbox parses before triage, frontdesk push, candidate promotion, or Feishu sync. Use when the user asks to 处理低质量解析队列, 修复待核验解析, parse quality repair, repair OCR/transcription quality, 补 OCR/字幕/转写门禁, or integrate parse-quality repair into the six-hour inbox gate. Writes 05_流转区/40_待核验/解析质量修复队列.md and 85_运行记录/解析质量修复-*.md; never creates or promotes long-term knowledge.
---

# Parse Quality Repair

Repair low-risk parsing quality issues and keep unresolved parsing risks visible.

## Quick Start

Preview matched low-quality notes without writing:

```bash
python3 .codex/skills/parse-quality-repair/scripts/repair_parse_quality.py
```

Write repairs, refresh the verification queue, and generate a report:

```bash
python3 .codex/skills/parse-quality-repair/scripts/repair_parse_quality.py --write --limit 5
```

Repair one specific inbox source:

```bash
python3 .codex/skills/parse-quality-repair/scripts/repair_parse_quality.py \
  --write \
  --source "00_收件箱/某条笔记.md"
```

## Behavior

The script scans `00_收件箱` for notes with low-quality parse signals:

- `内容质量: 需核验`
- `内容质量: 需继续解析`
- `解析状态: 解析失败` or `部分解析`
- tiny/small transcription warning text
- obvious term recognition mistakes
- RSS/Atom summary-only captures, truncated excerpts, or source blocks that are too short to support mobile reading

It automatically performs only low-risk repairs:

- Convert common Traditional Chinese characters to Simplified Chinese.
- Normalize obvious tool/company names such as `Codex`, `Claude Code`, `Anthropic`, `OpenAI`, `OpenClaw`, and `MCP`.
- Fill or tighten `内容质量` and `质量门禁` when the note has enough readable evidence.
- Append `## 解析修复记录` to the source note.
- Mark summary-only or truncated sources as `内容质量: 需核验` and keep them out of frontdesk push, Feishu publish, and candidate promotion.

It does not redownload media, run long transcriptions, or promote knowledge in v1. Items needing heavy work remain in the repair queue.

## Outputs

Write mode creates or updates:

```text
05_流转区/40_待核验/解析质量修复队列.md
85_运行记录/解析质量修复-YYYY-MM-DD-HHMM.md
```

It also refreshes the flow-zone views unless `--no-flow` is passed.

## Automation

Use this inside the six-hour inbox quality gate before triage and frontdesk push:

```bash
python3 .codex/skills/parse-quality-repair/scripts/repair_parse_quality.py --write --limit 5
```

Recommended boundaries:

- Keep `--limit 5` during trial.
- Run before `inbox-triage`.
- Do not include terminal statuses unless explicitly auditing old notes.
- Treat remaining repair queue items as blockers for candidate promotion.

## Work Boundaries

- Do not delete inbox notes.
- Do not create `20_资料库`, `75_提示词库`, `65_洞察`, or project notes.
- Do not mark a note as long-term knowledge.
- Do not publish anything to Feishu.
- Do not override user judgement; unresolved items should be surfaced to OpenClaw as short confirmation prompts.
