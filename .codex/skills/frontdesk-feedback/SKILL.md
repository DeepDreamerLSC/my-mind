---
name: frontdesk-feedback
description: Append and consume OpenClaw frontdesk replies for my-mind. Use when the user asks to 记录前台反馈, OpenClaw 反馈入队, 消费反馈队列, 回写阅读思考, 消费待确认, 确认转正, or parse replies like “1 已读：想法” / “2 沉淀成提示词” / “1 确认转正”. Appending writes 85_运行记录/前台反馈队列.jsonl; consuming can write 阅读思考, feedback reports, confirmed candidate distillation records, promotion gate results, and Feishu sync markers.
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

待确认回复也写入同一个队列。如果用户回复 `确认转正`、`继续核验`、`调整分类`，脚本会默认读取 `05_流转区/50_待确认/待确认队列.md` 来定位候选：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py "1 确认转正"
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py "1 继续核验"
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py "1 调整分类：资料库"
```

如果 OpenClaw 展示的是某个指定队列，显式传入：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py \
  --push-file "05_流转区/50_待确认/待确认队列.md" \
  "1 确认转正"
```

批量反馈可以保留自然语言，不要求 OpenClaw 拆成多条。消费脚本会按当前展示文件展开：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py \
  --push-file "05_流转区/50_待确认/待确认队列.md" \
  "五条待确认全部确认转正"

python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py \
  --push-file "85_运行记录/前台推送-YYYY-MM-DD-HHMM.md" \
  "前三条已读，但内容简略，没什么价值"
```

如果一句话同时包含数量和“全部”，以显式数量为准。例如“3 条链接全部超时”只展开 3 条，不会扩成当前推送文件里的所有条目。

## 消费队列

预览待处理反馈，不写文件：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py
```

真实消费并写回来源：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py --write
```

消费待确认并在转正通过后立即同步飞书：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py --write --sync-feishu
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

如果用户回复 `沉淀成提示词`，消费脚本会调用 `inbox-distill` 生成候选提示词并回链；如果用户明确说“入库”或泛化地要求沉淀为资料/洞察，优先交给 `knowledge-intake` 生成候选资料、候选提示词或候选洞察。所有候选都不直接标记为已晋升。

如果用户回复 `确认转正`，消费脚本会读取待确认队列，定位候选文件和来源文件，执行转正门禁。通过后：

- 候选文件标记为 `处理状态: 已处理`、`吸收状态: 已吸收`、`可信状态: 已核验`、`转正门禁: 通过`。
- 来源文件标记为 `处理状态: 已处理`、`入库状态: 已晋升`，并追加沉淀记录。
- 候选文件写入 `飞书同步: 精选同步 / 待同步`。
- 如传入 `--sync-feishu`，调用 `feishu-sync` 发布或更新飞书知识库页面。

如果门禁不通过，候选保持待确认/待核验，并把最小问题写回 `转正门禁记录`。

## 工作边界

- OpenClaw 只调用入队脚本，不直接改来源笔记。
- Codex 后台调用消费脚本。
- `已读` 和 `补充想法` 只回写阅读思考。
- `已读` 会同步标记来源 `阅读状态: 已读`；若用户明确说“没什么价值 / 无需沉淀”，来源会归档并从前台待反馈压力中退出。
- `跳过` 只归档来源笔记，不删除原文。
- `继续解析` 只记录补解析请求；实际 OCR、字幕或转写由后台后续执行。
- `沉淀成提示词` 生成候选提示词，但不标记为已晋升。
- `确认转正` 必须经过门禁；不能因为用户一句确认就绕过来源、解析质量、术语和正文完整性检查。
- `--sync-feishu` 可能调用外部飞书接口；没有该参数时只更新本地同步标记。

## 设计依据

完整协作设计见 `design/OpenClaw前台协作设计稿.md`。
