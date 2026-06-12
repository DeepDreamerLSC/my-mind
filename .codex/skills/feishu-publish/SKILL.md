---
name: feishu-publish
description: Publish or prepare my-mind frontdesk push notes as Feishu/Lark mobile reading pages. Use when the user asks to 发布飞书阅读页, 生成飞书知识库链接, 飞书发布, Feishu publish, Lark publish, or connect OpenClaw pushes to Feishu. Reads 85_运行记录/前台推送-*.md, writes 85_运行记录/飞书阅读页/ and 85_运行记录/飞书发布记录.jsonl, and never modifies long-term knowledge.
---

# 飞书阅读页发布

把最新 `85_运行记录/前台推送-*.md` 转成适合手机阅读的飞书知识库/文档页面，并维护发布记录供 OpenClaw 转发链接。

## 快速使用

预览即将发布的飞书阅读页：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --dry-run
```

生成本地飞书阅读页草稿并追加发布记录：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --write-local
```

使用本机飞书 CLI 或自定义发布命令。发布到用户手机可见的飞书知识库时，`lark-cli` 必须使用 OpenClaw workspace 的用户身份；如果仍是 `bot` 身份，文档可能创建成功但用户手机知识库不可见。

```bash
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config strict-mode user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config default-as user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli auth status --verify
```

确认 `identity` 为 `user` 后再发布：

```bash
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}' \
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --publish
```

如果要让页面出现在具体飞书知识库目录里，发布后再自动移动到目标知识库：

```bash
export MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id'
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}' \
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --publish
```

也可以显式传入命令：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py \
  --publish \
  --publish-command 'OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}'
```

## 输入和输出

默认读取最新：

```text
85_运行记录/前台推送-*.md
```

默认写入：

```text
85_运行记录/飞书阅读页/飞书阅读页-YYYY-MM-DD-HHMM.md
85_运行记录/飞书发布记录.jsonl
```

`飞书发布记录.jsonl` 中记录飞书链接、来源推送、内容哈希、条目列表、发布状态，以及可选的 `wiki_space_id` / `wiki_node_token`。OpenClaw 应优先读取最新一条 `status = 已发布` 且带 `feishu_url` 的记录；如果只有 `草稿已生成`，说明还没接上真实飞书发布命令。

## 工作边界

- 只读取前台推送和生成飞书阅读页。
- 不修改 `00_收件箱/`。
- 不写入资料库、原子笔记、行业情报、洞察或提示词库。
- 不消费用户反馈。
- 不把飞书当源库；本地 Markdown 仍是唯一可信源。
- 同一份推送内容如果已有相同 `content_hash` 的已发布记录，默认复用，不重复发布；需要重发时加 `--force`。

## OpenClaw 对接

OpenClaw 的前台消息应只发送：

- 今日待读总标题。
- 飞书阅读链接。
- 2 到 3 条重点标题和一句话摘要。
- 回复格式：`序号 已读：...`、`序号 沉淀成提示词`、`序号 跳过`、`序号 继续解析`。

完整阅读内容放在飞书页中。
