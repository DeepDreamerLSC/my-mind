---
name: knowledge-intake
description: Orchestrate my-mind “入库” requests from raw material to candidate knowledge while preserving original evidence first. Use when the user says 入库, 入到知识库, 保存到知识库, 直接沉淀, 自动沉淀, knowledge intake, or provides links/text/files they want Codex/OpenClaw to process beyond simple inbox capture. Always saves source material into 00_收件箱 first, then triages, creates candidate artifacts in 20_资料库/75_提示词库/65_洞察 when safe, and produces a short confirmation list for uncertain decisions.
---

# 入库处理

把用户明确认为“值得进入知识资产”的材料，从原始保存推进到候选知识。这个 skill 是上层编排器，不重写 `inbox-capture` 的链接解析、OCR、转写能力。

## 核心规则

凡是入库，先入箱。

流程：

```text
入库请求
-> 保存原始材料到 00_收件箱
-> 复用 inbox-capture / inbox-triage 的解析和分类能力
-> 判断待读、待沉淀、可生成候选、需继续解析
-> 生成候选知识或确认问题
-> 刷新 05_流转区
-> 写入 85_运行记录/入库处理-*.md
```

## 快速使用

预览，不写文件：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py \
  "https://example.com/article"
```

真实入库：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py \
  --write \
  "https://example.com/article"
```

处理已有收件箱来源：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py \
  --write \
  --source "00_收件箱/某条笔记.md"
```

处理纯文本：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py \
  --write \
  --title "我的临时想法" \
  "这里是要入库的内容..."
```

## 自动化边界

- 自动保存：链接、文本、解析结果和来源回链必须保存在 `00_收件箱/`。
- 自动分拣：只回写本次入库涉及的来源，避免批量改动无关待分拣条目。
- 自动候选：信息量足够且默认已读的条目，可以生成候选资料、候选提示词或候选洞察。
- 自动刷新：候选生成后刷新 `05_流转区/`，让“待读 / 待沉淀 / 待核验”继续可见。
- 不自动确认长期知识：候选文件默认标记 `处理状态: 候选`、`吸收状态: 待确认`，需要后续确认后才算“化为己有”。

## 目标选择

默认 `--target auto`：

- Codex、Prompt、Skill、工作流类内容优先生成 `75_提示词库/` 候选。
- 管理、组织、AI 产业、工具、设计、写作等资料优先生成 `20_资料库/` 候选。
- 高层判断、原则、跨来源模式才生成 `65_洞察/候选洞察/`。
- 解析不足、低质量、未读且未强制处理的内容只进入待读或待核验，不生成候选。

可显式指定：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --target library --source "00_收件箱/..."
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --target prompt --source "00_收件箱/..."
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --target insight --source "00_收件箱/..."
```

## 人机交互

尽量弱化用户感知，只在这些情况形成确认问题：

- 解析质量不足，需要继续转写、OCR 或人工核验。
- 候选已生成，但是否晋升长期知识需要用户判断。
- 涉及项目决策、管理原则、个人观点或洞察判断。
- 可能重复、过时、来源不可靠或内容敏感。

用户可以用短回复推进：

```text
确认
跳过
改成提示词
放到资料库
继续解析
我的判断是：...
```

## 工作边界

- 不删除收件箱原件。
- 不把飞书当源库。
- 不直接改写已确认的长期知识正文。
- 不自动同步到飞书；候选资料默认写入 `飞书同步: 策略: 不同步 / 状态: 暂停`，确认后再改为精选同步并走 `feishu-sync`。
- 不处理登录态、cookie 或绕过平台限制。
- 需要高质量中文表达时，Codex 应在脚本生成候选后再人工级润色，尤其是外文资料要做到信、达、雅。

## OpenClaw 桥接

OpenClaw 原生 `openclaw skills list` 可能不会自动发现项目内 `.codex/skills`。本机 OpenClaw workspace 使用薄入口 `my-mind-knowledge-intake` 委托回本脚本：

```bash
cd /Users/linsuchang/Desktop/work/my-mind
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --raw "<用户原始消息>"
```

解析、分拣、候选写入、报告写入和安全门禁都保留在本 skill，不要在 OpenClaw 侧复制一份业务逻辑。
