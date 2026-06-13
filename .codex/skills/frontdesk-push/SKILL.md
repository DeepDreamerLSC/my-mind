---
name: frontdesk-push
description: Generate a Chinese frontdesk reading-card note for OpenClaw and Feishu mobile reading from my-mind flow-zone queues, inbox notes, and project status. Use when the user asks to 生成前台推送, 给 OpenClaw 推送内容, create frontdesk push, or prepare pending-reading/distillation items for OpenClaw. Writes 85_运行记录/前台推送-*.md and never modifies long-term knowledge.
---

# 前台推送生成

把 Codex 后台看到的 `05_流转区/` 队列、收件箱正文和项目进度，整理成 OpenClaw 可以转发、飞书可以承载的手机阅读卡片。

这份文件是飞书精选 bundle 的素材，不是 OpenClaw 的最终聊天消息。OpenClaw 给用户发消息时，应调用 `prepare_openclaw_feishu_message.py`，由 `feishu-publish` 自动发布或复用“单篇文章 + 精选索引页”，再输出飞书索引链接、少量重点标题和回复指令。

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

默认会读取 `85_运行记录/前台推送状态.json`，跳过 24 小时内已经推送但尚未收到反馈的条目，避免 OpenClaw 反复催同一批内容。调整冷却时间：

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
- 不修改 `00_收件箱/`。
- 不写入长期知识目录。
- 不确认事实、观点、项目决策或沉淀结果。
- OpenClaw 必须通过 `.codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py` 生成聊天消息；该脚本会自动发布或复用飞书精选 bundle，不要退回发送原文链接。
- 推送优先级来自流转区；流转区缺失时才回退到收件箱评分排序。
- 默认不推送 `内容质量: 需继续解析` 的条目；需要临时包含时显式加 `--include-low-quality`。
- 默认不推送已经有正式/已吸收长期知识回链的收件箱来源；需要调试时显式加 `--include-promoted`。
- 推送状态只记录触达和反馈状态，不改变长期知识。

## 设计依据

完整协作设计见 `design/OpenClaw前台协作设计稿.md`。
