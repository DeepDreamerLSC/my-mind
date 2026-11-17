---
name: frontdesk-push
description: Generate a Chinese frontdesk reading-card note for OpenClaw and Feishu mobile reading from my-mind flow-zone queues, inbox notes, and project status. Use when the user asks to 生成前台推送, 给 OpenClaw 推送内容, create frontdesk push, or prepare pending-reading/distillation items for OpenClaw. Writes 85_运行记录/前台推送-*.md and never modifies long-term knowledge.
---

# 前台推送生成

把 Codex 后台看到的 `05_流转区/` 队列、收件箱正文和项目进度，整理成 OpenClaw 可以转发、飞书可以承载的手机阅读卡片。

这份文件是飞书精选 bundle 的素材，不是 OpenClaw 的最终聊天消息。OpenClaw 给用户发消息时，应调用 `prepare_openclaw_feishu_message.py`，由 `feishu-publish` 自动发布或复用“单篇文章 + 精选索引页”，再输出飞书索引链接、少量重点标题、每条简述和回复指令。

## 快速使用

生成并写入 `85_运行记录/`：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py
```

只预览不写入：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --dry-run
```

默认推送所有待读候选，适合后续发布成飞书精选 bundle。限制推送条数：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --limit 5
```

受控清仓时，固定写入一个只含当日批次的阅读包；例如每天最多 10 条：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py \
  --limit 10 \
  --output-file "85_运行记录/后台总览/今日未读清仓包.md" \
  --requeue-published-after-hours 24
```

`--requeue-published-after-hours` 仅会恢复“前台投递包已准备但没有真实消息发送回执”的旧条目；已 `sent`、已有反馈或已处理的条目不会重新进入包内。

默认会读取 `85_运行记录/前台推送状态.json`。生成文件只把条目标记为 `prepared`；已 `published`、已真实 `sent` 或已有反馈的条目不会被重新当作普通候选。调整 `prepared` 冷却时间：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --cooldown-hours 48
```

忽略冷却并强制生成：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --force
```

默认使用 `05_流转区/10_待读/收件箱待读队列.md` 决定推送优先级，再回到 `00_收件箱/` 读取正文摘录。临时忽略流转区排序：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --ignore-flow
```

调整每条阅读摘录长度：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --excerpt-chars 1600
```

## 输出

默认写入：

```text
85_运行记录/前台推送-YYYY-MM-DD-HHMM.md
85_运行记录/前台推送状态.json
```

传入 `--output-file` 时改为覆盖该固定路径，适合 OpenClaw 用显式路径发送当日受控批次，避免误取旧的 `前台推送-*.md`。

内容包含：

- 今天最值得读的全部候选；只有显式 `--limit N` 时才限制条数。
- 每条的来源、价值、建议动作、建议沉淀方向和来源文件；需要明确区分“待读”“待沉淀”和“已生成候选待确认”。
- 每条的原文链接、原始分享入口和外部转录链接，便于用户跳转到平台继续阅读。
- 可直接阅读的正文摘录、OCR 摘录或视频摘要。
- 阅读时重点问题和质量提醒。
- 项目当前已完成、卡点和下一步建议。
- 用户可直接回复给 OpenClaw 的指令。

## 工作边界

- 只生成前台推送文件。
- 生成推送稿不等于真实发送；本脚本只写 `prepared_at` / `delivery_stage: prepared`，不得写 `sent_at`。
- 不修改 `00_收件箱/`。
- 不写入长期知识目录。
- 不确认事实、观点、项目决策或沉淀结果。
- 常规精选由 OpenClaw 通过 `.codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py` 生成聊天消息，并自动发布或复用飞书精选 bundle，不要退回发送原文链接。
- 已授权的固定额外待读包必须通过 `send_openclaw_backlog_pack.py` 调用标准飞书 bundle 发布链路；它先生成飞书索引页和单篇页，再发送知识库链接。用户明确要求查看待核验来源时，调用方显式传入 `--include-incomplete`，页面必须保留待核验提醒。
- 推送优先级来自流转区；流转区缺失时才回退到收件箱评分排序。
- 默认不推送 `内容质量: 需继续解析` 或 `内容质量: 需核验` 的条目；需要临时包含时显式加 `--include-low-quality`。
- 默认不推送已经有正式/已吸收长期知识回链的收件箱来源；需要调试时显式加 `--include-promoted`。
- 旧版无真实发送证据的记录保持 `delivery_stage` 为空并标记 `legacy_delivery: unknown`，不进入待反馈统计。
- 推送状态只记录交付和反馈状态，不改变长期知识。

## 设计依据

完整协作设计见 `design/OpenClaw前台协作设计稿.md`。
