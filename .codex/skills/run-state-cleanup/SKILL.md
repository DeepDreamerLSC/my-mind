---
name: run-state-cleanup
description: Audit and govern my-mind run-state artifacts and dirty worktree batches. Use when the user asks 运行产物治理, 工作区整理, 分批提交建议, 自动化运行产物清理, run state cleanup, or wants Codex/OpenClaw/backend automation to classify generated reports, inbox state, flow views, Feishu pages, dashboard snapshots, and code changes before commit/push.
---

# Run State Cleanup

审计当前工作区的运行产物和未提交改动，输出可执行的分批治理建议。这个 skill 只分类、提示风险和生成报告，不自动删除文件、不回滚、不提交、不推送。

## Quick Start

预览当前治理计划：

```bash
python3 .codex/skills/run-state-cleanup/scripts/plan_run_state_cleanup.py
```

写入固定治理视图和时间戳报告：

```bash
python3 .codex/skills/run-state-cleanup/scripts/plan_run_state_cleanup.py --write
```

输出 JSON 供后台总控或其他脚本消费：

```bash
python3 .codex/skills/run-state-cleanup/scripts/plan_run_state_cleanup.py --json
```

## Outputs

写入模式会生成：

```text
85_运行记录/运行产物治理-YYYY-MM-DD-HHMM.md
85_运行记录/后台总览/运行产物治理.md
```

报告包含：

- 工作区是否干净。
- 按类别统计的未提交改动。
- 每类样例、建议动作和建议提交批次。
- 敏感文件或本地配置风险。
- 时间戳运行记录、固定视图、收件箱状态、流转区视图、飞书精选页和技能代码的分离建议。

## Automation

总控日更可以在生成前台推送、飞书发布、后台状态和建议分析之后运行：

```bash
python3 .codex/skills/run-state-cleanup/scripts/plan_run_state_cleanup.py --write
```

它的作用是把当天自动化造成的文件变化压缩成一张治理面板，让 Codex 后续分批提交时能先看分类，而不是翻完整 `git status`。

## Boundaries

- 不自动 `git add`、`git commit`、`git push`。
- 不删除旧报告、不归档文件、不重写历史。
- 不修改 `00_收件箱`、`05_流转区` 或长期知识正文。
- 不把本地 `.local.json`、密钥、环境文件纳入提交建议；只提示风险。
- 代码批次和运行状态批次必须分开提交。
