---
name: inbox-triage
description: Generate Chinese triage reports for my-mind inbox notes. Use when the user asks to 分拣收件箱, 沉淀收件箱, 巡检收件箱, review pending inbox items, or run the first-stage inbox automation. Reads 00_收件箱, can auto-mark obvious items as 已分拣, and produces a pending-distill queue with handling strategies for user follow-up.
---

# 收件箱分拣

把 `00_收件箱/` 中 `处理状态: 待分拣` 的笔记整理成中文分拣建议。现在支持把明显可分拣的条目标为 `已分拣`，并进入“待沉淀队列”；真正沉淀仍等待用户读完后主动触发。

## 快速使用

只在终端输出报告：

```bash
python3 .codex/skills/inbox-triage/scripts/triage_inbox.py
```

自动分拣并写入 `85_运行记录/`：

```bash
python3 .codex/skills/inbox-triage/scripts/triage_inbox.py --mark-sorted --write --report-dir 85_运行记录
```

## 输出内容

每条待分拣笔记会包含：

- 优先级：高 / 中 / 低
- 建议去向：`20_资料库/`、`30_原子笔记/`、`75_提示词库/`、`10_项目/个人数据资产系统/`、`60_行业情报/`、`65_洞察/候选洞察/`、保持待分拣、可丢弃
- 核心价值
- 下一步
- 风险
- 来源文件

报告还会附带：

- 当前 `已分拣` 条目的待沉淀队列
- 每条条目的当前阶段、已有候选和建议处理策略
- 每条阅读后建议反馈什么，方便用户把反馈回写到 `阅读思考`，再主动触发下一步沉淀

## 工作边界

- 可选地把 `待分拣` 条目标成 `已分拣` 并追加 `分拣记录`。
- 不确认长期知识。
- 不删除或归档资料。
- 不使用登录态、cookie 或额外抓取。
- 报告是候选建议；用户阅读并反馈后，才进入资料库、原子笔记、提示词库或项目文件。

## 设计依据

完整阶段设计见 `design/收件箱自动分拣与沉淀设计稿.md`。
