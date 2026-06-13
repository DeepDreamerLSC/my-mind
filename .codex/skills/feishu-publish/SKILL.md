---
name: feishu-publish
description: Publish or prepare my-mind frontdesk push notes as Feishu/Lark精选 bundle pages. Use when the user asks to 发布飞书阅读页, 发布飞书精选, 生成飞书知识库链接, 飞书发布, Feishu publish, Lark publish, or connect OpenClaw pushes to Feishu. Reads 85_运行记录/前台推送-*.md, writes 85_运行记录/飞书精选页/ plus optional legacy 85_运行记录/飞书阅读页/, and never modifies long-term knowledge.
---

# 飞书精选发布

把最新 `85_运行记录/前台推送-*.md` 转成适合手机阅读的飞书知识库页面，并维护发布记录供 OpenClaw 转发链接。当前推荐使用“精选 bundle”模式：每条待读生成一篇飞书文章，再生成一个索引页；OpenClaw 只推索引页链接。

## 快速使用

预览精选 bundle：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --dry-run
```

生成本地精选 bundle 草稿并追加发布记录：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --write-local
```

使用本机飞书 CLI 或自定义发布命令。发布到用户手机可见的飞书知识库时，`lark-cli` 必须使用 OpenClaw workspace 的用户身份；如果仍是 `bot` 身份，文档可能创建成功但用户手机知识库不可见。

```bash
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config strict-mode user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config default-as user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli auth status --verify
```

确认 `identity` 为 `user` 后发布精选 bundle：

```bash
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}' \
MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id' \
MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='手机待读目录node_token' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
```

bundle 模式会：

- 为前台推送里的每条待读创建或更新一篇飞书文章。
- 把原文链接、分享链接、摘录、OCR、转写和建议动作放进单篇文章。
- 生成一个“今日精选”索引页，索引页每一节链接到对应飞书文章。
- 通过 `source_file`、标题、页面 token 和内容 hash 复用已有文章，避免重复创建。

旧版单页飞书阅读页只作为兼容路径保留：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --dry-run
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --write-local
```

如果要让页面出现在具体飞书知识库目录里，发布后再自动移动到目标知识库：

```bash
export MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id'
export MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='目标目录node_token'
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
```

也可以显式传入命令：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py \
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
85_运行记录/飞书精选页/单篇/*.md
85_运行记录/飞书精选页/索引/*.md
85_运行记录/飞书发布记录.jsonl
85_运行记录/飞书阅读页/飞书阅读页-YYYY-MM-DD-HHMM.md（旧版兼容）
```

`飞书发布记录.jsonl` 中记录飞书链接、来源推送、内容哈希、条目列表、发布状态，以及可选的 `wiki_space_id` / `wiki_node_token`。OpenClaw 不直接解析前台推送里的原文链接，而是调用下面的消息出口脚本读取与最新前台推送匹配的已发布 `frontdesk_bundle_index` 记录。

## 工作边界

- 只读取前台推送和生成飞书精选页；旧版单页阅读页仅作兼容。
- 不修改 `00_收件箱/`。
- 不写入资料库、原子笔记、行业情报、洞察或提示词库。
- 不消费用户反馈。
- 不把飞书当源库；本地 Markdown 仍是唯一可信源。
- 同一份推送内容如果已有相同 `content_hash` 的已发布记录，默认复用，不重复发布；需要重发时加 `--force`。

## OpenClaw 对接

执行归属：

- OpenClaw 负责在前台定时任务或用户手动要求时触发链路：生成前台推送、发布精选 bundle、转发消息出口脚本输出。
- `feishu-publish` 负责发布、复用、更新、记录和消息出口；OpenClaw 不直接拼接原文链接，也不直接解析 `前台推送-*.md` 生成最终消息。
- Codex 负责维护这个 skill、处理失败记录、调整门禁和修复发布链路。

生成 OpenClaw 可直接转发的消息：

```bash
python3 .codex/skills/feishu-publish/scripts/build_openclaw_feishu_message.py
```

这条命令会输出精选索引页的 Wiki 链接，例如 `https://my.feishu.cn/wiki/...`；如果最新 `前台推送-*.md` 还没有匹配的 `status = 已发布` 的 `frontdesk_bundle_index` 记录，命令会失败并提示先运行 `publish_frontdesk_bundle.py --publish`。OpenClaw 不应在失败时退回发送原文链接。

消息太长时可让脚本拆分：

```bash
python3 .codex/skills/feishu-publish/scripts/build_openclaw_feishu_message.py --chunk-size 1200
```

OpenClaw 的前台消息只发送：

- 今日待读总标题。
- 飞书知识库阅读链接。
- 2 到 3 条重点标题和一句话摘要。
- 回复格式：`序号 已读：...`、`序号 沉淀成提示词`、`序号 跳过`、`序号 继续解析`。

完整阅读内容放在飞书页中。
