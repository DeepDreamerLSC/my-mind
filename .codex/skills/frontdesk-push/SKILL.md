---
name: frontdesk-push
description: Generate a Chinese frontdesk reading-card note for OpenClaw and Feishu mobile reading from my-mind flow-zone queues, inbox notes, and project status. Use when the user asks to 生成前台推送, 给 OpenClaw 推送内容, create frontdesk push, or prepare pending-reading/distillation items for OpenClaw. Writes 85_运行记录/前台推送-*.md and never modifies long-term knowledge.
---

# 前台推送生成

把 Codex 后台看到的 `05_流转区/` 队列、收件箱正文和项目进度，整理成 OpenClaw 可以转发、飞书可以承载的手机阅读卡片。

OpenClaw 直接发消息时只需要发标题、链接和回复指令；完整内容适合发布到飞书文档或知识库节点后让用户在手机上阅读。

## 快速使用

生成并写入 `85_运行记录/`：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py
```

只预览不写入：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --dry-run
```

默认推送所有待读候选，适合后续发布成飞书阅读页。限制推送条数：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --limit 5
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
```

内容包含：

- 今天最值得读的全部候选；只有显式 `--limit N` 时才限制条数。
- 每条的来源、价值、建议动作、建议沉淀方向和来源文件。
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
- OpenClaw 优先读取 `飞书发布记录.jsonl` 里的最新飞书链接；没有飞书链接时再读取最新 `前台推送-*.md`，把精简摘要推给用户。
- 推送优先级来自流转区；流转区缺失时才回退到收件箱评分排序。

## 设计依据

完整协作设计见 `design/OpenClaw前台协作设计稿.md`。
