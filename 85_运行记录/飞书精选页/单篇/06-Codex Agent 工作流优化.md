# Codex Agent 工作流优化

- 页面类型：my-mind 前台精选单篇
- 生成来源：Codex / feishu-publish bundle
- 来源推送：`85_运行记录/前台推送-2026-06-13-1905.md`
- 前台推送时间：2026-06-13 19:05:03 +0800
- 条目序号：6
- 来源：小红书 / Kriswillwin
- 状态：已分拣 / 已读 / 已解析
- 来源文件：`00_收件箱/2026-06-10 小红书 - Codex Agent 工作流优化.md`
- 原文链接：https://www.xiaohongshu.com/explore/6a21650b000000000702651f
- 分享链接：http://xhslink.com/o/7ZGjSx5sdPA
- 为什么值得读：这是用户主动入箱且已读过的资料，适合优先判断是否继续沉淀，而不是再次提醒阅读。
- 建议动作：已读过；直接回复“沉淀成提示词/资料库”或补一句读后判断，不值得保留就回复“跳过”。
- 建议沉淀方向：`75_提示词库/Codex工作流/` 或 `10_项目/个人数据资产系统/`，优先判断是否能沉淀成可复用工作流。

## 一句话摘要

基于自己的工作流优化了一下 Codex 的 subagents 使用方式 小 Feature：feature_coder ↔ feature_reviewer 对抗闭环，最后 human review 大 Feature：方案确认 → squad 实现 → 验收，人主要介入阶段 1 和阶段 3 核心目标：减少上线后问题，让 Codex 不只是“会写代码”，而...

## 内容摘录

**文案摘录**
基于自己的工作流优化了一下 Codex 的 subagents 使用方式

小 Feature：feature_coder ↔ feature_reviewer 对抗闭环，最后 human review
大 Feature：方案确认 → squad 实现 → 验收，人主要介入阶段 1 和阶段 3

核心目标：减少上线后问题，让 Codex 不只是“会写代码”，而是按工程流程先查证、再判断、再实现、最后验收。

工作流一：小 Feature 对抗闭环
适合范围明确、改动小、风险中低的任务。
Human 给需求和边界
↓
feature_coder 实现最小改动
↓
feature_reviewer 对抗审查
↓
feature_coder 修复 blocker
↓
feature_reviewer 复审
↓
Human 最后 review

工作流二：大 Feature 阶段门控
适合跨模块、跨仓库、上线风险较高、需求需要先收敛的任务。
阶段 1：方案确认，人主导
↓
阶段 2：Squad 实现，agent 主导
↓
阶段 3：验收，人主导

* 主 Agent 负责理解需求、控制范围、最终决策。
* 小 Feature 通过 coder-reviewer 对抗提高细节质量。
* 大 Feature 通过阶段门控降低方向和验收风险。
* Subagents 负责独立取证、审查、复现、查文档。
* 默认只读；只有 coder、UI 复现、验收取证允许 workspace-write。
* 不提交、不推送、不做破坏性操作。
* 所有结论必须基于真实文件、日志、请求链或官方来源。
* 控制并发和递归，避免 token 和时间失控。

**图片文字 OCR**
Codex Agent工作流优化器
一份用于优化Codex工作流的本地Sk它会安装一套可复用的CoexSubagents默认配置，并把复杂开发拆成两种
更可控的模式：
eaiurefeaturecoderfeature_reviewer对抗环，最后humanreview
大Feature：方案确认→squad实现→验收，人主要介入阶段1和阶段3
核心目标：减少上线后问题，Codex不只是会写代码，而是按工程流程先查证，再判断、再实现、最后验收。
解决什么问题
日常使用Codex时，复杂任务很容易出现这些问题
没看清真实代码链路就开始改
小需求缺少revlewer对抗，隐藏bug容易进注线
·大需求一开始就写代码；方案、边异、验收标准都没确认
没看日志、payload，allbackpath就判断根因。
PR或上线前缺少独立风险审查
第三方ARI/SDK行为靠记忆断。
Ubug没有复现步，console，network，载图证据
验收阶段只有代码能跑”，没有逐项证据和Go/No-Go

（摘录已截断，完整内容见来源文件。）

## 阅读时重点

- 这条能否沉淀成一个可复用提示词、skill 或自动化流程？
- 它对当前 `my-mind` 的入箱、分拣、反馈或飞书阅读闭环有什么直接启发？
- 是否有一个可以马上加入任务清单的最小动作？

## 质量提醒

- 已具备文案摘录和图片 OCR，可进入分拣和前台阅读候选。；本条包含图片 OCR，适合先读大意，沉淀前需要校对图片文字。

## 回复给 OpenClaw

- `6 已读：你的想法`
- `6 沉淀成提示词`
- `6 跳过`
- `6 继续解析`

## 处理边界

- 本页是本地 `my-mind` 的手机阅读镜像，不是长期知识源。
- 原始状态、回链和沉淀记录仍以本地 Markdown 为准。
- 未经用户确认，不自动晋升资料库、原子笔记、洞察或提示词库。
