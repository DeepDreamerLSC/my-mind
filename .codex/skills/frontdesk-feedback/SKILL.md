---
name: frontdesk-feedback
description: Append and consume OpenClaw frontdesk replies for my-mind. Use when the user asks to 记录前台反馈, OpenClaw 反馈入队, 消费反馈队列, 回写阅读思考, or parse replies like “1 已读：想法” / “2 沉淀成提示词”. Appending writes 85_运行记录/前台反馈队列.jsonl; consuming can write 阅读思考, feedback processing reports, and confirmed candidate distillation records.
---

# 前台反馈队列

把 OpenClaw 收到的用户短回复解析成结构化 JSONL，并在后台消费时回写来源笔记。

## 入队

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py "1 已读：我觉得重点是高频流程应该沉淀成 skill。"
```

指定渠道：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py --channel 微信 "1 沉淀成提示词"
```

只预览不写入：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py --dry-run "2 跳过"
```

## 消费队列

预览待处理反馈，不写文件：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py
```

真实消费并写回来源：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py --write
```

只回写阅读思考，不触发候选沉淀：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py --write --no-distill
```

## 输出

入队默认追加写入：

```text
85_运行记录/前台反馈队列.jsonl
```

每行包含：

- 时间
- 来源
- 渠道
- 推送文件
- 目标序号
- 目标标题
- 来源文件
- 动作
- 目标类型
- 内容
- 原始回复
- 处理状态

消费默认写入：

```text
来源笔记的 ## 阅读思考
来源笔记的 ## 前台反馈处理记录
85_运行记录/反馈消费-YYYY-MM-DD-HHMM.md
```

如果用户回复 `沉淀成提示词`，消费脚本会调用 `inbox-distill` 生成候选提示词并回链；其他沉淀类型先只记录待处理，不直接写长期知识。

## 工作边界

- OpenClaw 只调用入队脚本，不直接改来源笔记。
- Codex 后台调用消费脚本。
- `已读` 和 `补充想法` 只回写阅读思考。
- `跳过` 只归档来源笔记，不删除原文。
- `继续解析` 只记录补解析请求；实际 OCR、字幕或转写由后台后续执行。
- `沉淀成提示词` 生成候选提示词，但不标记为已晋升。

## 设计依据

完整协作设计见 `design/OpenClaw前台协作设计稿.md`。
