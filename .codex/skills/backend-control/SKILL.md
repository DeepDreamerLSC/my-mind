---
name: backend-control
description: Inspect and govern my-mind backend operations. Use when the user asks 后台总控巡检, 后台总览, 系统健康检查, 自动化状态, 自动化结果, 推送节流, 反馈消费状态, or wants Codex/OpenClaw to see a concise dashboard of automation outcomes and next safe actions without creating long-term knowledge.
---

# 后台总控巡检

检查 `my-mind` 后台链路是否健康，给出下一步动作建议。这个 skill 只做治理、总览和报告，不晋升长期知识。

## 快速使用

预览详细系统健康报告：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py
```

预览给用户看的固定后台总览：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --view dashboard
```

预览给 OpenClaw 读取的最小提醒稿：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --view openclaw
```

写入运行记录和固定总览：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

`--write` 会同时写入：

- `85_运行记录/后台总控巡检-*.md`：完整证据报告，供 Codex 排查。
- `85_运行记录/后台总览/当前后台状态.md`：固定覆盖的用户状态面板。
- `85_运行记录/后台总览/OpenClaw待提醒.md`：固定覆盖的前台提醒稿。
- `85_运行记录/后台总览/飞书仪表盘数据.json`：供飞书多维表格同步的结构化数据。
- `85_运行记录/后台总览/飞书仪表盘数据/*.csv`：按表拆分的 CSV 数据。

只导出飞书仪表盘数据：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --export-dashboard-data
```

输出 JSON 供其他前台工具消费：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --format json
```

## 检查范围

- Codex App 自动化配置和频率。
- `05_流转区/` 的待读、待沉淀、待核验数量。
- `00_收件箱/` 的处理状态、解析质量和低质量条目；已归档且明确为重复证据的低质量条目不计入活跃异常。
- `parse-quality-repair` 的最近运行记录和待核验修复状态。
- `85_运行记录/前台反馈队列.jsonl` 的待处理反馈。
- `85_运行记录/待确认事项.jsonl` 的候选转正确认事项。
- `85_运行记录/前台推送状态.json` 的冷却、已推送未反馈和重复触达风险。
- 最近的分拣、门禁、前台推送、飞书发布、飞书同步和反馈消费记录。
- 当前 git 工作区是否存在未提交改动。

## 输出原则

- 先给总览，再列证据。
- 把动作分成“需要用户处理”“Codex 后台应处理”“OpenClaw 前台应提醒”。
- 固定总览要覆盖写入，避免用户翻多份历史自动化报告。
- 不删除文件，不改收件箱，不生成候选沉淀。
- 若发现待处理反馈，建议优先运行 `frontdesk-feedback` 的消费脚本。
- 若发现低质量解析，不建议进入前台推送。
- OpenClaw 只读取 `OpenClaw待提醒.md` 进行前台沟通，不转发完整巡检报告。
- 飞书仪表盘同步只读取结构化数据文件，不重复扫描仓库事实源。

## 工作边界

- 这是总控视图，不是事实来源。
- 具体内容仍以 `00_收件箱/`、`05_流转区/` 和各 skill 运行记录为准。
- OpenClaw 可读取本 skill 输出的短建议，但不应把完整巡检报告直接发给用户。
