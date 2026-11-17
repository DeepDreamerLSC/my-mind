---
类别: 收件箱
资料类型: 前沿情报
来源平台: Simon Willison
标题: "Using DSPy to evaluate and improve Datasette Agent's SQL system prompts"
作者或频道: "Simon Willison"
发布时间: 2026-07-03
捕获时间: "2026-07-05 07:14:24 +0800"
来源链接: "https://simonwillison.net/2026/Jul/2/dspy-datasette-agent-prompts"
原始链接: "https://simonwillison.net/2026/Jul/2/dspy-datasette-agent-prompts"
解析工具: frontier-watch
解析器: RSS/Atom
解析状态: 已解析
内容质量: 可推送
质量门禁: 已通过前沿情报来源和分数门禁，可进入分拣和前台阅读候选；沉淀前仍需阅读原文核验。
内容摘录来源: RSS/Atom 摘要中文译写
内容摘录后端:
内容摘录字数: 578
阅读状态: 未读
处理状态: 已分拣
关联项目:
  - 个人数据资产系统
关联领域: []
主题:
  - AI与Agent工具
  - 工作流与知识系统
标签:
  - 前沿情报
  - AI与Agent工具
  - 工作流与知识系统
敏感状态: 未知
---

# Using DSPy to evaluate and improve Datasette Agent's SQL system prompts

## 基础信息

- 来源：Simon Willison
- 作者：未知
- 发布时间：2026-07-03
- 原文链接：https://simonwillison.net/2026/Jul/2/dspy-datasette-agent-prompts
- 情报门类：AI与Agent工具, 工作流与知识系统
- 分数：24

## 中文摘要

研究：使用 DSPy 评估和改进 Datasette Agent 的 SQL 系统提示 今天早上的 AIE 主题演讲之一涉及 dspy，这提醒我我一直想看看它是否可以帮助我改进 Datasette Agent 使用的系统提示 - 因此我使用 Claude Fable 5 在 Claude Code for web 中启动了一项异步研究任务： Pip 安装最新的 Datasette alpha 和 datasette-agent 以及 dspy - 然后了解如何使用 dspy 来评估和改进 Datasette Agent 使用的主系统提示，以实现它可以执行只读 SQL 查询以回答用户有关数据的问题。 Fable 选择使用 GPT 4.1 mini 和 nano 进行测试，并确定了几个有希望的改进方向。我特别喜欢这个： 模式列表仅给出表名称； “如果您已经拥有信息，则不要调用 describe_table”建议导致基线跟踪中的列名猜测（page_count、o.order_id、first_name）和错误重试循环。要么在提示的架构列表中包含列名称，要么软化该建议。 标签: ai , datasette , 生成人工智能 , llms , evals , dspy , datasette-agent , Claude-mythos-fable

## 原文摘录

Research: Using DSPy to evaluate and improve Datasette Agent's SQL system prompts 

 One of this morning's AIE keynotes covered dspy , which reminded me I've been meaning to see if it could help me improve the system prompt used by Datasette Agent - so I fired off an asynchronous research task in Claude Code for web using Claude Fable 5:

 
 Pip install the latest Datasette alpha and datasette-agent and dspy - then figure out how to use dspy to evaluate and improve the main system prompts used by Datasette Agent for the feature where it can execute read only SQL queries to answer user questions about data. 

 
 Fable chose to test using GPT 4.1 mini and nano, and identified several promising looking directions for improvements. I particularly like this one:

 
 The schema listing gives only table names; the "don't call describe_table if you already have the information" advice caused column-name guessing (page_count, o.order_id, first_name) and error-retry loops in baseline traces. Either include column names in the prompt's schema listing or soften that advice.

 
 
 
 Tags: ai , datasette , generative-ai , llms , evals , dspy , datasette-agent , claude-mythos-fable

## 为什么保存

- 命中 AI与Agent工具、工作流与知识系统，可能影响 Codex/OpenClaw/知识库自动化的后续设计。
- 建议去向：`20_资料库/工作流与自动化/`、`75_提示词库/Codex工作流/` 或 `10_项目/个人数据资产系统/`

## 初步想法

- 待阅读后补充。

## 阅读思考

- 待阅读后补充。
- 可记录：这条情报是否影响当前项目、商业判断、管理方法或后续沉淀方向。

## 后续处理建议

- 先打开原文判断内容质量。
- 如果值得保留，再反馈 `已读`、`沉淀`、`跳过` 或 `继续追踪`。

## 分拣记录

- 2026-07-05：已自动分拣。建议去向：75_提示词库/Codex工作流/、10_项目/个人数据资产系统/、20_资料库/工作流与自动化/、20_资料库/。核心价值：与 Codex 工作流、提示词或自动化直接相关，适合先进入 `75_提示词库/Codex工作流/`，再反哺个人数据资产系统。下一步：先阅读原文，再在对话中反馈是否继续沉淀。

## 原始链接

https://simonwillison.net/2026/Jul/2/dspy-datasette-agent-prompts
