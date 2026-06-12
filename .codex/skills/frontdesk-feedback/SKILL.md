---
name: frontdesk-feedback
description: Append OpenClaw frontdesk replies into my-mind's Chinese JSONL feedback queue. Use when the user asks to 记录前台反馈, OpenClaw 反馈入队, append frontdesk feedback, or parse replies like “1 已读：想法” / “2 沉淀成提示词”. Writes only 85_运行记录/前台反馈队列.jsonl and does not modify knowledge notes.
---

# 前台反馈入队

把 OpenClaw 收到的用户短回复解析成结构化 JSONL，追加到 `85_运行记录/前台反馈队列.jsonl`。

## 快速使用

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

## 输出

默认追加写入：

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

## 工作边界

- 只 append JSONL。
- 不改 `00_收件箱/`。
- 不写长期知识目录。
- 不触发沉淀。
- Codex 后台后续消费队列，再决定回写 `阅读思考` 或调用 `inbox-distill`。

## 设计依据

完整协作设计见 `design/OpenClaw前台协作设计稿.md`。
