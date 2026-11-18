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

预览常规日常用户入口：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --view today
```

只固定覆盖“今日动作”两个文件，不生成时间戳报告：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --view today --write
```

刷新唯一的“今日动作”固定视图：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

`--write` 只写入 `今日动作.json` 和 `今日动作.md`，相同输入重复运行不会改变内容。需要显式生成诊断快照和仪表盘导出时使用：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write-diagnostics
```

`--write-diagnostics` 会写入：

- `85_运行记录/后台总控巡检-*.md`：完整证据报告，供 Codex 排查。
- `85_运行记录/后台总览/当前后台状态.md`：固定覆盖的用户状态面板。
- `85_运行记录/后台总览/OpenClaw待提醒.md`：兼容镜像，内容与“今日动作”一致，不是第二套动作来源。
- `85_运行记录/后台总览/飞书仪表盘数据.json`：供飞书多维表格同步的结构化数据。
- `85_运行记录/后台总览/飞书仪表盘数据/*.csv`：按表拆分的 CSV 数据。
- `85_运行记录/后台总览/今日动作.json` 与 `今日动作.md`：同一动作类型、来源去重、最多 3 项的常规日常入口。

只导出飞书仪表盘数据。结构化导出属于诊断写入，必须显式授权：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write-diagnostics --export-dashboard-data
```

输出 JSON 供其他前台工具消费：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --format json
```

## 检查范围

- Codex App 自动化配置和频率。
- 计划暂停的自动化是否已并入总控日更；只有意外未激活才进入风险。
- `05_流转区/` 的待读、待沉淀、待核验数量；待沉淀会继续拆成可消费、已有候选待确认、待补判断三类，避免把已生成候选的内容重复沉淀。
- `00_收件箱/` 的处理状态、解析质量和低质量条目；已归档且明确为重复证据的低质量条目不计入活跃异常。
- `00_收件箱/` 的分段转写队列：`转写状态` 的未开始/进行中/失败分布，进行中最近一条的覆盖进度（`转写覆盖`）与 `转写备注`；有未开始或进行中时在 Codex 后台应处理中给出续跑命令建议。
- `parse-quality-repair` 的最近运行记录和待核验修复状态。
- `run-state-cleanup` 的最近运行产物治理记录。
- `decision-review` 的最新项目决策审视等级、偏航风险和最小动作。
- `code-quality-review` 的最新代码质量等级、验证缺口和提交门禁。
- `85_运行记录/前台反馈队列.jsonl` 的待处理反馈。
- `85_运行记录/待确认事项.jsonl` 的候选转正确认事项。
- `85_运行记录/前台推送状态.json` 的五态、交付未知、已发送待反馈和重复触达风险；只有 `sent` 计入待反馈。
- 最近的分拣、门禁、前台推送、飞书发布、飞书同步和反馈消费记录。
- 当前 git 工作区是否存在未提交改动。
- 当前 git 工作区改动的治理分组，例如技能代码、收件箱状态、流转区视图、后台固定视图、时间戳运行记录和飞书精选页。
- 飞书仪表盘的 `后台驾驶舱` 聚合表，用于一屏查看系统健康、用户待决策、前台触达、解析质量、流转库存、工作区治理、决策审视和代码质量。

## 长视频分段转写续跑（后台总控日更）

后台总控日更流程中，长视频分段转写按以下步骤续跑：

1. 巡检发现待续跑：健康检查扫描 `00_收件箱/` 中 `转写状态 ∈ {未开始, 进行中}` 的视频笔记；dashboard 视图的“Codex 后台应处理”会出现续跑建议。
2. 执行续跑（预算 900 秒/次）：

   ```bash
   python3 .codex/skills/inbox-capture/scripts/segmented_transcribe.py --budget-seconds 900
   ```

3. 运行证据由执行器自动落盘为 `85_运行记录/分段转写-*.md`，总控不代写、不改动。
4. 下次日重复查进度：进行中条目的 `转写覆盖` 会逐次增长，覆盖 ≥98% 后 `转写状态` 转为已完成，才允许内容质量升为可推送。

要点：

- 900 秒/次的续跑预算是用户拍板决策；长视频分几天跑完属预期，未完成不视为异常。
- 健康检查只发现队列并建议命令，本身不执行转写；执行由日更流程或人工完成。
- `转写状态: 失败` 的条目计入异常与风险区（如“分段转写失败 N 条，需人工核验”），需人工核验后再决定重试或放弃。

## 输出原则

- 先给总览，再列证据。
- 把动作分成“需要用户处理”“Codex 后台应处理”“OpenClaw 前台应提醒”。
- 待沉淀总数只代表流转区存量；只有“待沉淀可消费”才表示 Codex 应继续自动生成候选。
- 固定总览要覆盖写入，避免用户翻多份历史自动化报告。
- 今日动作只输出 `阅读`、`确认`、`补充判断/核验`、`暂无动作` 之一；相同输入重复刷新不写出内容变化。
- 转正门禁失败或解析不足的候选属于 Codex 后台待核验，不得继续作为用户“确认转正”事项重复提醒。
- 不删除文件，不改收件箱，不生成候选沉淀。
- 健康检查不执行分段转写，只统计队列并建议续跑命令；转写执行由日更流程或人工完成，分段转写进度由执行器回写收件箱笔记。
- 若发现待处理反馈，建议优先运行 `frontdesk-feedback` 的消费脚本。
- 若发现低质量解析，不建议进入前台推送。
- OpenClaw 的常规提醒只读取“今日动作”；用户明确启用清仓时，额外读取固定的 `今日未读清仓包.md`，不得改读最新推送或 `OpenClaw待提醒.md`。
- 若已推送未反馈较多但反馈队列为空，明确标记“脚本生成不等于已触达”，提醒 OpenClaw 观察真实发送。
- 若决策审视或代码质量审视出现高/红色风险，把它放入 Codex 后台应处理事项，必要时交给 OpenClaw 提醒用户确认。
- 飞书仪表盘同步只读取结构化数据文件，不重复扫描仓库事实源。
- 工作区治理只分类和建议分批，不自动提交、不自动回滚。

## 工作边界

- 这是总控视图，不是事实来源。
- 具体内容仍以 `00_收件箱/`、`05_流转区/` 和各 skill 运行记录为准。
- OpenClaw 可读取本 skill 输出的短建议，但不应把完整巡检报告直接发给用户。
