---
name: feishu-sync
description: Sync selected my-mind Markdown notes into Feishu/Lark knowledge base pages without creating duplicates. Use when the user asks to 同步本地知识库到飞书, 飞书精选同步, sync selected notes to Feishu, maintain Feishu directory mirrors, or publish curated 20_资料库/10_项目/75_提示词库 pages. Scans explicit 飞书同步 front matter, writes 85_运行记录/飞书知识库同步记录.jsonl, and avoids duplicate documents by reusing local metadata, prior sync records, and Feishu search before creation.
---

# 飞书精选同步

把本地已经标记为精选的 Markdown 同步到飞书知识库。这个 skill 只处理长期精选内容；每日待读仍使用 `feishu-publish`。

## 快速使用

预览候选和动作，不写本地、不调用飞书：

```bash
python3 .codex/skills/feishu-sync/scripts/sync_selected_notes.py --dry-run
```

真实同步：

```bash
OPENCLAW_HOME="$HOME/.openclaw" lark-cli auth status --verify
python3 .codex/skills/feishu-sync/scripts/sync_selected_notes.py --publish
```

默认创建和更新命令使用 OpenClaw workspace 的用户身份：

```bash
OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}
OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +update --api-version v2 --doc {page_token} --mode overwrite --new-title {title} --markdown @{markdown_file_rel}
```

如需移动到指定飞书知识库目录，用环境变量注入空间和目录，不要写进仓库：

```bash
export MY_MIND_FEISHU_SYNC_WIKI_SPACE_ID='目标知识库space_id'
export MY_MIND_FEISHU_SYNC_PARENT_NODE_TOKEN='默认父目录node_token'
python3 .codex/skills/feishu-sync/scripts/sync_selected_notes.py --publish
```

如需按本地目录映射不同飞书目录，创建本地私有 JSON 文件，例如 `85_运行记录/飞书知识库目录映射.local.json`：

```json
{
  "20_资料库/人工智能产业": "AI目录node_token",
  "20_资料库/工作流与自动化": "工作流目录node_token",
  "10_项目/个人数据资产系统": "项目目录node_token"
}
```

然后运行：

```bash
MY_MIND_FEISHU_SYNC_PARENT_MAP='85_运行记录/飞书知识库目录映射.local.json' \
python3 .codex/skills/feishu-sync/scripts/sync_selected_notes.py --publish
```

自定义命令会经过 Python `str.format` 渲染；如果命令里需要字面量 `{` 或 `}`，请写成 `{{` 或 `}}`。

## 本地标记

只有带显式 `飞书同步` front matter 的文档会被扫描为候选：

```yaml
飞书同步:
  策略: 精选同步
  状态: 待同步
  飞书页面: ""
  页面Token: ""
  Wiki节点: ""
  最近同步: ""
  内容哈希: ""
```

支持策略：

- `精选同步`
- `目录页同步`

`策略: 不同步` 或 `状态: 暂停` 会跳过。

## 防重复规则

脚本默认不重复创建文档：

- 如果 front matter 里已有 `飞书页面` 或 `页面Token`，只更新该页面。
- 如果 `85_运行记录/飞书知识库同步记录.jsonl` 里已有同一 `source_file` 的页面记录，只复用该页面。
- 如果本地没有记录，创建前会用飞书搜索同标题文档；命中唯一精确标题时认领并更新该页面。
- 如果搜索失败或命中多个同名文档，默认停止创建并记录错误；需要强行创建必须显式加 `--allow-create-without-search`。
- 如果内容哈希未变化，默认跳过，不创建也不更新。

## 输出

默认写入：

```text
85_运行记录/飞书知识库同步页/*.md
85_运行记录/飞书知识库同步记录.jsonl
```

成功同步后，脚本会回写来源 Markdown 的 `飞书同步` 字段，记录飞书链接、页面 token、Wiki 节点、最近同步时间和内容哈希。

## 工作边界

- 只同步显式标记的精选文档。
- 不扫描 `00_收件箱/` 和 `05_流转区/` 作为长期精选来源。
- 不保存飞书凭证、space id 或目录 node token 到仓库文件。
- 不把飞书侧编辑回写到本地正文。
- OpenClaw 只负责转发链接和收反馈，不负责判断精选标准。
