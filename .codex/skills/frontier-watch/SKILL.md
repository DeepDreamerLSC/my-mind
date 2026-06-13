---
name: frontier-watch
description: Run curated frontier intelligence checks for my-mind across AI/agent tools, workflow and knowledge systems, business, and management. Use when the user asks to 巡检前沿信息, 获取前沿情报, frontier watch, 浏览有用资讯入箱, or monitor useful external signals. Produces 85_运行记录/前沿情报巡检-*.md and only writes 00_收件箱 when explicitly requested.
---

# 前沿情报巡检

从高质量来源筛选对 `my-mind` 有用的外部信号，覆盖四类：

- `AI与Agent工具`
- `工作流与知识系统`
- `商业`
- `管理`

默认只生成巡检报告，不入箱；只有用户明确要求或命令显式加 `--write-inbox` 时，才把入选项写入 `00_收件箱/`。

## 快速使用

生成巡检报告：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py
```

只预览不写入：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py --dry-run
```

生成报告并把入选项入箱：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py --write-inbox
```

限制门类：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py --category 商业 --category 管理
```

默认只入选 `--lookback-days` 时间窗口内且有发布时间的内容。确实要回看旧内容时再显式放宽：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py --include-older
```

入选项的外文摘要默认会译写成简体中文，并在报告里保留原文摘录便于追溯。译写会走网络翻译兜底，再用项目术语表修正 Codex、OpenAI、Agent、MCP、Notion、Obsidian、Token 等常见错译。临时关闭翻译：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py --no-translate-summaries
```

## 输出

报告写入：

```text
85_运行记录/前沿情报巡检-YYYY-MM-DD-HHMM.md
```

入箱写入：

```text
00_收件箱/YYYY-MM-DD 前沿情报 - 标题.md
```

写入收件箱时默认标记：

```yaml
阅读状态: 未读
处理状态: 待分拣
```

## 门禁

入选项必须：

- 来自配置里的高质量来源。
- 命中至少一个门类。
- 分数达到 `--min-score`。
- 默认不是已存在链接。

不要把普通热搜、标题党、活动报名、纯营销内容入箱。

## 来源配置

默认来源见：

```text
.codex/skills/frontier-watch/references/sources.json
```

调整来源时优先使用稳定 RSS/Atom；没有稳定 feed 的网站先不要加入自动巡检。

## 工作边界

- 不确认事实。
- 不直接生成长期知识。
- 不绕过登录态或付费墙。
- 不全量抓取网页正文。
- 自动翻译只服务预读，重要事实和术语仍需阅读原文校对；沉淀阶段可再做一次人工式中文改写。
- 入箱后仍交给 `inbox-triage` 分拣，用户阅读反馈后再决定是否沉淀。
