---
name: project-progress
description: Generate my-mind project progress candidates from git commits, working tree changes, recent run records, and project files. Use when the user asks 项目进展巡检, 项目进度, 变更证据, 项目周报候选, sync commits into knowledge base, or wants a recurring project management report without auto-committing.
---

# 项目进展巡检

## Overview

Use this skill to turn engineering evidence into project progress candidates and Codex analysis. It treats git commits, working tree changes, and `85_运行记录/` reports as evidence, then separates real project progress from noise, risk, and follow-up actions.

## Workflow

1. Generate a candidate report:

   ```bash
   python3 .codex/skills/project-progress/scripts/project_progress.py --write
   ```

2. Review `85_运行记录/项目进展巡检-<project>-*.md`.

3. If the user confirms the candidate, append it to project files:

   ```bash
   python3 .codex/skills/project-progress/scripts/project_progress.py --write --apply
   ```

## Boundaries

- Do not automatically create git commits.
- Do not treat every commit as real project progress.
- Do not overwrite `项目进展.md`, `变更证据.md`, or `项目周报.md`; only append.
- Keep running records as evidence in `85_运行记录/`.
- When run by automation, use report-only mode and leave confirmation to Codex/user.

## Script

`scripts/project_progress.py` collects:

- recent git commits;
- current working tree changes;
- project-related run records;
- project front matter and status files.

The report must include `Codex 项目分析`:

- 项目判断：判断置信度、本轮真正推进、当前阻塞、下个最小动作、噪声
- 阶段判断
- 有效进展
- 证据噪声
- 风险与阻塞
- 下一步建议
- 需要确认
- 回写建议

Useful commands:

```bash
python3 .codex/skills/project-progress/scripts/project_progress.py --dry-run
python3 .codex/skills/project-progress/scripts/project_progress.py --write
python3 .codex/skills/project-progress/scripts/project_progress.py --write --project edu-agent
python3 .codex/skills/project-progress/scripts/project_progress.py --write --since-hours 48
python3 .codex/skills/project-progress/scripts/project_progress.py --write --apply
```

Automation should run `--write` only.

## Managed projects

- `my-mind` / `个人数据资产系统`：默认项目，扫描当前知识库仓库。
- `edu-agent`：扫描 `/Users/linsuchang/Desktop/work/edu-agent`，把候选进展写入 `10_项目/edu-agent/`。

When a project has an external code repository, keep generated reports in `85_运行记录/` and write only confirmed project conclusions into `10_项目/<项目>/`.
