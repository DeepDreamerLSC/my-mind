---
name: feishu-dashboard
description: Sync my-mind backend dashboard rows into Feishu/Lark Base for visualization. Use when the user asks 飞书仪表盘, 同步飞书仪表盘, 刷新后台驾驶舱, 更新后台驾驶舱, 主动同步仪表盘, 多维表格看板, 后台驾驶舱, 可视化自动化结果, sync backend dashboard to Feishu Base, or wants Codex/OpenClaw automation outcomes shown as charts without duplicate records.
---

# 飞书仪表盘

把 `backend-control` 生成的后台总览数据同步到飞书多维表格。这个 skill 只负责可视化数据层，不创建长期知识，不改变收件箱、流转区或资料库正文。

## 快速使用

先刷新本地仪表盘数据：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --export-dashboard-data
```

预览同步计划，不写飞书：

```bash
python3 .codex/skills/feishu-dashboard/scripts/sync_dashboard_rows.py
```

指定飞书多维表格并真实同步：

```bash
MY_MIND_FEISHU_DASHBOARD_BASE_TOKEN='base_token' \
python3 .codex/skills/feishu-dashboard/scripts/sync_dashboard_rows.py --refresh-data --write
```

日常主动触发时，优先运行：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
python3 .codex/skills/feishu-dashboard/scripts/sync_dashboard_rows.py --refresh-data --refresh-advice --write --dedupe --delete-long-keys --delete-stale
```

同步记录写入：

- `85_运行记录/飞书仪表盘同步记录.jsonl`

## 本地数据源

默认读取：

- `85_运行记录/后台总览/飞书仪表盘数据.json`
- `85_运行记录/后台总览/飞书仪表盘数据/*.csv`

这些文件由 `backend-control --export-dashboard-data` 或 `backend-control --write` 生成。

## 推荐飞书表

脚本默认把本地表同步到同名飞书表：

- `后台驾驶舱`
- `后台指标`
- `指标历史`
- `行动队列`
- `自动化状态`
- `运行记录`
- `解析质量`
- `待确认候选`
- `前台推送`
- `流转队列`
- `当前行动建议`

如果飞书表名不同，使用本地配置覆盖，配置文件不要提交：

```json
{
  "base_token": "base_token",
  "identity": "user",
  "table_map": {
    "cockpit": "后台驾驶舱或 tbl...",
    "metrics": "后台指标",
    "metric_history": "指标历史"
  }
}
```

默认配置路径：

- `85_运行记录/飞书仪表盘配置.local.json`

如果 `lark-cli 1.0.32` 无法通过表名找到新建的 `后台驾驶舱`，先用 `lark-cli base +table-list` 找到 table id，再把 `table_map.cockpit` 写到本地 `.local.json`。不要把这个配置提交。

## 去重原则

- 每行都有 `记录键`。
- `记录键` 控制在 50 字符以内，长路径会转为短哈希；可读原文保留在 `原始键` 或来源字段里。
- 同步前先在目标表搜索 `记录键`。
- 找到远端记录就更新，找不到才创建。
- `指标历史` 的 `记录键` 包含快照时间，因此用于趋势图；其他表主要表示当前状态。

如需清理调试或异常造成的重复行：

```bash
MY_MIND_FEISHU_DASHBOARD_BASE_TOKEN='base_token' \
python3 .codex/skills/feishu-dashboard/scripts/sync_dashboard_rows.py --dedupe-only --delete-long-keys --delete-stale --write
```

`--delete-stale` 会保留 `指标历史`，只清理当前状态表中本地数据已不存在的行。

## 初始化飞书表

如果多维表格是空的，可以先预览建表和建字段命令：

```bash
MY_MIND_FEISHU_DASHBOARD_BASE_TOKEN='base_token' \
python3 .codex/skills/feishu-dashboard/scripts/sync_dashboard_rows.py --init-tables --init-only
```

确认后再加 `--write`。`--init-tables` 只适合新 Base；已存在表时不要反复运行，避免重复字段。

## 仪表盘建议

第一版建议在飞书多维表格里手工搭仪表盘，数据由脚本维护：

- 顶部总览：用 `后台驾驶舱` 做一屏状态表，展示系统健康、用户待决策、前台触达、解析质量、流转库存和工作区治理。
- 指标卡：后台状态指数、待确认候选、待读、待沉淀、待核验、低质量解析、已推送未反馈。
- 折线图：`指标历史` 中近 7/30 天的关键指标。
- 漏斗图：`流转队列` 或 `后台指标` 中收件箱到转正同步的数量。
- 表格：`后台驾驶舱`、`当前行动建议`、`行动队列`、`待确认候选`、`解析质量`。

后续如果仪表盘布局稳定，再考虑让脚本自动创建 dashboard block。
