---
name: inbox-distill
description: Turn one my-mind inbox note into a candidate distilled artifact. First version supports generating a Chinese prompt-library candidate from a single 00_收件箱 note, then optionally marking the source as 已处理 with a backlink. Use when the user asks to 沉淀某条收件箱, 生成候选提示词, distill inbox item, or complete the inbox distillation loop.
---

# 收件箱候选沉淀

把单条 `00_收件箱/` 笔记转成候选沉淀物。第一版只支持生成 `75_提示词库/` 候选提示词，并会按内容自动落到合适的二级目录，如 `Codex工作流/`、`前端与视觉/`、`收集与录入/`、`萃取与整理/`、`项目推进/`。

## 快速使用

默认 dry run，只预览：

```bash
python3 .codex/skills/inbox-distill/scripts/distill_inbox_note.py --source "00_收件箱/某条笔记.md" --target prompt
```

确认写入：

```bash
python3 .codex/skills/inbox-distill/scripts/distill_inbox_note.py --source "00_收件箱/某条笔记.md" --target prompt --write
```

## 行为

- 读取来源笔记的 frontmatter、文案摘录、摘要、关键点和转写摘录。
- 读取来源笔记的 `阅读思考`，把用户读后反馈纳入候选沉淀输入。
- 生成一条候选提示词到 `75_提示词库/` 下的合适子目录。
- 未显式指定 `--output-dir` 时，脚本会根据来源内容自动选择提示词目录。
- `--write` 时把来源笔记改为 `处理状态: 已处理`，补 `关联项目`、`主题`，并追加 `沉淀记录` 回链。

## 边界

- 不标记 `已晋升`。
- 不删除、不移动来源。
- 不自动写入决策记录。
- 不把候选提示词声称为来源逐字稿。
- 默认假设用户已经读过来源并明确要求继续沉淀。
- 长视频或低质量转写应先人工校对，第一版优先处理短文案、短视频和结构清晰的社媒笔记。
