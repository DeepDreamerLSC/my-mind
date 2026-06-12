---
name: backend-control
description: Inspect and govern my-mind backend operations. Use when the user asks 后台总控巡检, 系统健康检查, 自动化状态, 推送节流, 反馈消费状态, or wants Codex to decide whether inbox triage, frontdesk push, Feishu sync, or feedback processing should run next without creating long-term knowledge.
---

# 后台总控巡检

检查 `my-mind` 后台链路是否健康，给出下一步动作建议。这个 skill 只做治理和报告，不晋升长期知识。

## 快速使用

预览系统健康报告：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py
```

写入运行记录：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

输出 JSON 供其他前台工具消费：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --format json
```

## 检查范围

- Codex App 自动化配置和频率。
- `05_流转区/` 的待读、待沉淀、待核验数量。
- `00_收件箱/` 的处理状态、解析质量和低质量条目。
- `85_运行记录/前台反馈队列.jsonl` 的待处理反馈。
- `85_运行记录/前台推送状态.json` 的冷却、已推送未反馈和重复触达风险。
- 最近的分拣、门禁、前台推送、飞书发布、飞书同步和反馈消费记录。
- 当前 git 工作区是否存在未提交改动。

## 输出原则

- 先给结论，再列证据。
- 把动作分成“后台可直接做”和“需要 OpenClaw 提醒用户”。
- 不删除文件，不改收件箱，不生成候选沉淀。
- 若发现待处理反馈，建议优先运行 `frontdesk-feedback` 的消费脚本。
- 若发现低质量解析，不建议进入前台推送。

## 工作边界

- 这是总控视图，不是事实来源。
- 具体内容仍以 `00_收件箱/`、`05_流转区/` 和各 skill 运行记录为准。
- OpenClaw 可读取本 skill 输出的短建议，但不应把完整巡检报告直接发给用户。
