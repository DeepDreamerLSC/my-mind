---
name: code-quality-review
description: Review Codex-generated or recently changed project code quality, AI-slop risk, over-engineering, test gaps, security/config hazards, and commit readiness. Use when the user asks 代码质量审视, Codex 产出质量, AI 代码质量, review generated code, code quality gate, or wants automation to audit recent diffs before continuing.
---

# 代码质量审视

## Overview

Use this skill to inspect the quality risk of recent code and automation changes. It reads git status, recent commits, diff stats, changed files, and project conventions, then produces a review-style operating report.

This skill is a gate and evidence surface. It does not fix code, run destructive commands, commit, push, or mark work complete.

## Quick Start

Preview the default project:

```bash
python3 .codex/skills/code-quality-review/scripts/review_code_quality.py
```

Write a report:

```bash
python3 .codex/skills/code-quality-review/scripts/review_code_quality.py --write --project my-mind
```

Review `edu-agent`:

```bash
python3 .codex/skills/code-quality-review/scripts/review_code_quality.py --write --project edu-agent
```

Review all managed projects:

```bash
python3 .codex/skills/code-quality-review/scripts/review_code_quality.py --write --project all
```

## Inputs

- Git status, recent commits, changed paths, diff stats, and added diff lines
- Project-specific path categories
- Existing project files under `10_项目/<项目>/`
- Recent project-progress and decision-review reports when available

## Outputs

`--write` creates:

- `85_运行记录/代码质量审视-<project>-YYYY-MM-DD-HHMM.md`
- `85_运行记录/后台总览/代码质量审视-<project>.md`
- `85_运行记录/后台总览/代码质量审视.md`

The report includes:

- 总览
- 质量结论
- 主要风险
- AI 代码味道
- 必要验证
- 提交前门禁
- 建议分批
- 证据来源

## Review Rules

- Lead with bugs, risks, behavior regressions, missing tests, security/config hazards, and unclear ownership.
- Flag code changes without corresponding tests or validation notes.
- Flag broad diffs that mix skill code, docs, inbox state, run records, Feishu pages, and project files.
- Flag hard-coded local paths, broad exception swallowing, debug prints, TODO placeholders, and secret-like changes.
- Prefer deletion or reuse over new abstraction when a change looks over-engineered.
- Treat generated run records as evidence batches, not code quality proof.
- For automation and skill changes, require at least syntax validation plus one representative script run.

## Boundaries

- Do not fix code automatically unless the user separately asks for implementation.
- Do not run destructive cleanup.
- Do not auto-commit or push.
- Do not treat a green report as proof that tests passed; report only the evidence available.
- Use the best available high-reasoning model when this skill is run by Codex automation; the script gathers quality signals, while Codex should still read the report as a code reviewer.
