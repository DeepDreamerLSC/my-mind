---
name: inbox-triage
description: Generate Chinese triage reports for my-mind inbox notes. Use when the user asks to 分拣收件箱, 沉淀收件箱, 巡检收件箱, review pending inbox items, or run the first-stage inbox automation. Reads 00_收件箱 and suggests destinations without modifying source notes.
---

# 收件箱分拣

把 `00_收件箱/` 中 `处理状态: 待分拣` 的笔记整理成中文分拣建议。第一阶段只分析，不自动移动、改写或晋升资料。

## 快速使用

只在终端输出报告：

```bash
python3 .codex/skills/inbox-triage/scripts/triage_inbox.py
```

写入 `85_指标/`：

```bash
python3 .codex/skills/inbox-triage/scripts/triage_inbox.py --write --report-dir 85_指标
```

## 输出内容

每条待分拣笔记会包含：

- 优先级：高 / 中 / 低
- 建议去向：`20_资料库/`、`30_原子笔记/`、`75_提示词库/`、`10_项目/个人数据资产系统/`、`60_行业情报/`、`65_洞察/候选洞察/`、保持待分拣、可丢弃
- 核心价值
- 下一步
- 风险
- 来源文件

## 工作边界

- 不修改收件箱笔记。
- 不确认长期知识。
- 不删除或归档资料。
- 不使用登录态、cookie 或额外抓取。
- 报告是候选建议，人工确认后再进入资料库、原子笔记、提示词库或项目文件。

## 设计依据

完整阶段设计见 `design/收件箱自动分拣与沉淀设计稿.md`。
