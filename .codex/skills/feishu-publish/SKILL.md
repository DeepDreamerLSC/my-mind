---
name: feishu-publish
description: Publish or prepare my-mind frontdesk push notes as Feishu/Lark精选 bundle pages. Use when the user asks to 发布飞书阅读页, 发布飞书精选, 生成飞书知识库链接, 飞书发布, Feishu publish, Lark publish, or connect OpenClaw pushes to Feishu. Reads 85_运行记录/前台推送-*.md, writes 85_运行记录/飞书精选页/ plus optional legacy 85_运行记录/飞书阅读页/, and never modifies long-term knowledge.
---

# 飞书精选发布

把最新 `85_运行记录/前台推送-*.md` 转成适合手机阅读的飞书知识库页面，并维护发布记录供 OpenClaw 转发链接。当前推荐使用“精选 bundle”模式：每条待读生成一篇飞书文章，再生成一个索引页；OpenClaw 只推索引页链接。

## 快速使用

手动发布飞书精选时，OpenClaw 可调用这个出口；它会先查找与最新前台推送匹配的已发布飞书精选 bundle，如果没有就先发布，再输出可直接转发给用户的短消息：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py
```

日常 OpenClaw 提醒必须走唯一入口 `今日动作.json`，不能无参数调用上面的 bundle 命令，也不能读取旧推送或 `OpenClaw待提醒.md`：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py \
  --today-action "85_运行记录/后台总览/今日动作.json"
```

用户明确启用未读清仓时，OpenClaw 可以额外发送由 Codex 预先生成的固定批次，但必须显式传入路径，不能回退到“最新推送”：

```bash
python3 .codex/skills/feishu-publish/scripts/send_openclaw_backlog_pack.py \
  --target "user:ou_xxx" \
  --include-incomplete
```

该发送器固定发布最多 10 条的飞书“额外待读” bundle，再调用 OpenClaw 飞书出口发送索引页和单篇文章链接。默认保留标准内容完整性门禁；用户明确要求将待核验内容也放入飞书待读页时，调用方显式传入 `--include-incomplete`，页面必须保留待核验提醒，不能表述为已沉淀的正式知识。只有返回外部 `messageId` 后才以固定 `--push-file` 调用 `--ack-sent`；无发送回执的页面发布不能被视为用户已读。

每次有真实回执后，发送器会把当日包冻结到 `85_运行记录/后台总览/未读清仓历史/`，并更新 `今日未读清仓包最近发送.json`。这样次日固定包覆盖后，晚到的阅读反馈仍能定位到实际发送的条目。

今日动作输出为空时保持静默。实际发送成功并拿到消息 ID 后，记录同一份今日动作的回执，避免未变化的动作被重复提醒：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py \
  --today-action "85_运行记录/后台总览/今日动作.json" \
  --ack-today-action-sent \
  --channel 飞书 \
  --message-id "OpenClaw 返回的消息 ID"
```

页面可访问后，交付状态只到 `published`。OpenClaw 实际发送成功后，必须用外部消息 ID 或幂等键单独记录回执；这个命令本身不发送消息：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py \
  --ack-sent \
  --push-file "85_运行记录/前台推送-YYYY-MM-DD-HHMM.md" \
  --channel 飞书 \
  --message-id "OpenClaw 返回的消息 ID"
```

没有消息 ID 时可改用 `--idempotency-key`。缺少渠道或两类外部证据时，脚本拒绝进入 `sent`；重复同一回执为幂等操作。

消息太长时可拆分：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py --chunk-size 1200
```

预览精选 bundle：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --dry-run
```

## 存量视觉补全

已发布的飞书页面不需要重建。下面的命令会同时读取每日精选页和长期知识同步页的发布记录，按来源笔记重新计算封面、原始图片和视频时间线关键帧；默认只预演，不下载媒体、不改本地笔记、不调用飞书写接口：

```bash
python3 .codex/skills/feishu-publish/scripts/backfill_feishu_visuals.py --dry-run
```

预演默认只处理当前来源通过阅读质量门禁的页面，跳过素材缺失、待核验或需继续解析的历史内容。需要只检查某一类页面或分批检查时：

```bash
python3 .codex/skills/feishu-publish/scripts/backfill_feishu_visuals.py --dry-run --lane frontdesk --max-items 10
python3 .codex/skills/feishu-publish/scripts/backfill_feishu_visuals.py --dry-run --lane library
```

明确确认后才执行实际补图；脚本将素材写入本地忽略的缓存，再通过飞书原生图片接口插入。它按来源文件、页面 ID 和已插入素材 ID 去重，旧页面找不到新锚点时才追加图片。不要把 `--include-incomplete` 用作常规清理手段：它只适合用户明确要求保留待核验页面时使用。

```bash
python3 .codex/skills/feishu-publish/scripts/backfill_feishu_visuals.py --publish --lane frontdesk --max-items 10
```

默认会检查每条来源笔记的正文完整性；RSS/Atom 摘要、截断摘录、过短转写和缺少可读证据的条目会被阻断，并写入待补全队列。只有调试或临时兜底时才显式允许不完整条目：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --dry-run --include-incomplete
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
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --doc-format markdown --content @{markdown_file_rel}' \
MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id' \
MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='手机待读目录node_token' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
```

bundle 模式会：

- 为前台推送里的每条待读创建或更新一篇飞书文章。
- 音视频先生成分层阅读版：首屏是 100 至 200 字的“30 秒结论”，随后是 3 至 7 条带时间范围和证据的核心观点、3 至 8 个语义章节、视觉证据和“对我的价值”；来源、质量状态和运行信息统一放在末尾。
- 3 分钟以内的完整转写在主页面底部按自然段展示；超过 3 分钟的转写使用稳定身份创建或更新独立“完整转写”页，主阅读页只保留入口。原始本地转写始终保留，不作为发布重写目标。
- 为单篇文章附上可追溯的视觉证据：来源封面、原始图文图片、本地 Markdown 图片，或与核心观点对应的视频关键画面。说明使用完整观点和时间范围，不再截取字幕前 72 字；不生成配图，也不下载或保存完整视频。
- `--dry-run` 只显示视觉候选数量；仅 `--publish` 会下载图片或提取视频关键帧并用飞书原生图片插入。已有文章缺少新锚点时会追加图片，已记录的图片不会重复插入；视觉处理失败时仍保留文字页和原始来源链接。
- 生成一个“今日精选”索引页，索引页每一节链接到对应飞书文章。
- 索引页放在 `📱 my-mind 手机待读`；单篇文章按本地目录映射自动归入 `20_资料库精选/` 的对应主题目录。
- 通过 `source_file`、标题、页面 token 和内容 hash 复用已有文章，避免重复创建。
- 内容 hash 会纳入来源笔记正本的 hash；如果来源笔记补了全文、OCR 或转写，重跑发布会更新已有飞书文章。
- 如果来源笔记仍只有 RSS/Atom 摘要、章节标题、截断文案、过短或不完整转写，默认阻断正式阅读页并进入待补全；`--include-incomplete` 只能生成带明显警告的核验页，不能伪装成精选正本。
- 如果已有单篇文章内容没变但父目录不对，重跑发布会移动已有 Wiki 节点，不新建重复文章。
- 同一天的索引页按标题复用并更新，不因新的 `前台推送-*.md` 文件重复创建入口页。

存量音视频升级使用独立工具，默认只有预演，不调用飞书写接口：

```bash
python3 .codex/skills/feishu-publish/scripts/backfill_feishu_reading_pages.py --dry-run
```

`--write-local` 只重建本地阅读稿和独立转写稿；只有用户明确确认后才使用 `--publish` 更新符合质量门禁的既有页面。脚本按来源文件、阅读版 hash 和完整转写 hash 复用页面，重跑不会重复创建。

旧版单页飞书阅读页只作为兼容路径保留：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --dry-run
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --write-local
```

旧版单页发布也必须按标题复用当天页面：同一个 `my-mind 今日待读 YYYY-MM-DD` 已经发布过时，后续运行应更新该页面，不新增同名文档。默认更新命令兼容本机 `lark-cli 1.0.32`：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --publish
```

如果要让索引页出现在手机待读目录里，用本地环境变量注入目标空间和索引目录，不要写进仓库：

```bash
export MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id'
export MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='手机待读目录node_token'
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --doc-format markdown --content @{markdown_file_rel}' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
```

单篇文章默认读取本地私有映射 `85_运行记录/飞书知识库目录映射.local.json`，按标题、来源、摘要和沉淀方向推断目标目录。可用环境变量或参数覆盖：

只有该映射中显式配置的目录才会作为独立飞书 Wiki 父目录。新增或扩展分类关键词只会改变归类；没有对应映射时，文章回落到 `20_资料库` 根目录，不会自动创建飞书目录。

```bash
MY_MIND_FEISHU_ITEM_PARENT_MAP='85_运行记录/飞书知识库目录映射.local.json' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --dry-run
```

也可以显式传入命令：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py \
  --publish \
  --publish-command 'OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --doc-format markdown --content @{markdown_file_rel}'
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
05_流转区/40_待核验/飞书发布待补全队列.md
85_运行记录/飞书精选页/门禁报告/*.md
85_运行记录/飞书发布记录.jsonl
85_运行记录/飞书视觉补全记录.jsonl
85_运行记录/飞书阅读页/飞书阅读页-YYYY-MM-DD-HHMM.md（旧版兼容）
```

`飞书发布记录.jsonl` 中记录飞书链接、来源推送、内容哈希、条目列表、来源正本接入状态、发布状态，以及可选的 `wiki_space_id` / `wiki_node_token` / `wiki_parent_directory`。OpenClaw 不直接解析前台推送里的原文链接，也不自己判断飞书目录，而是调用 `prepare_openclaw_feishu_message.py` 自动补齐“发布精选 bundle -> 生成 OpenClaw 消息”链路，并最终读取与最新前台推送匹配的已发布 `frontdesk_bundle_index` 记录。

## 工作边界

- 只读取前台推送和生成飞书精选页；旧版单页阅读页仅作兼容。
- 不修改 `00_收件箱/`。
- 不写入资料库、原子笔记、行业情报、洞察或提示词库。
- 不消费用户反馈。
- 不把飞书当源库；本地 Markdown 仍是唯一可信源。
- 同一份推送内容如果已有相同 `content_hash` 的已发布记录，默认复用，不重复发布；需要重发时加 `--force`。
- 默认视觉证据上限为 4 张，可用 `--max-visuals` 调整；需要仅发布文字时加 `--no-visual-evidence`。图片与关键帧缓存写入 `85_运行记录/飞书精选页/媒体/`，不提交到仓库。
- 存量视觉补全只使用可追溯的封面、原始图片、本地页面图或公开视频关键帧；没有来源素材时保留文字页面，不使用生成图片补位。

## OpenClaw 对接

执行归属：

- OpenClaw 对常规日常提醒只读取 `今日动作.json`，调用 `prepare_openclaw_feishu_message.py --today-action ...`，并在真实发送后记录回执；不得回退到前台推送或旧提醒文件。
- 已授权的固定额外待读包只读取 `后台总览/今日未读清仓包.md`，由 `send_openclaw_backlog_pack.py` 调用标准 `prepare_openclaw_feishu_message.py --push-file ...` 链路，先发布 10 条单篇文章和一个飞书索引页，再发送知识库链接；真实发送后仍必须以同一固定包执行 `--ack-sent`。
- 用户手动要求飞书精选或手机阅读页时，OpenClaw 触发常规前台推送、bundle 发布和默认 `prepare_openclaw_feishu_message.py` 链路。
- `feishu-publish` 负责发布、复用、更新、记录和消息出口；OpenClaw 不直接拼接原文链接，也不直接解析 `前台推送-*.md` 生成最终消息。
- Codex 负责维护这个 skill、处理失败记录、调整门禁和修复发布链路。

生成 OpenClaw 可直接转发的消息：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py
```

这条命令会输出精选索引页的 Wiki 链接，例如 `https://my.feishu.cn/wiki/...`。如果最新 `前台推送-*.md` 还没有匹配的 `status = 已发布` 的 `frontdesk_bundle_index` 记录，它会先运行 `publish_frontdesk_bundle.py --publish`；发布失败时才返回错误。OpenClaw 不应在失败时退回发送原文链接。

消息太长时可让脚本拆分：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py --chunk-size 1200
```

底层调试命令仍可单独使用：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
python3 .codex/skills/feishu-publish/scripts/build_openclaw_feishu_message.py
```

OpenClaw 的前台消息只发送：

- 今日待读总标题。
- 飞书知识库阅读链接。
- 2 到 3 条重点标题、明确标注的简述和对应飞书知识页链接；简述优先使用分层阅读页的“30 秒结论”，旧记录才回退到原一句话摘要。
- 需要用户确认的候选知识摘要。
- 待确认候选以“决策卡”展示，包含候选文件、建议动作和可回复格式。
- 回复格式：`序号 已读：...`、`序号 沉淀成提示词`、`序号 跳过`、`序号 继续解析`。

完整阅读内容放在飞书页中。

固定额外待读包仍使用标准飞书 bundle。默认不降低完整性门禁；当用户明确要求可直接查看待核验来源时，发送器可显式使用 `--include-incomplete`，单篇页必须展示完整性提醒和“待核验来源”状态。示例：

```bash
python3 .codex/skills/feishu-publish/scripts/send_openclaw_backlog_pack.py \
  --target "user:ou_xxx" \
  --include-incomplete
```

固定包会先在飞书生成可阅读的完整 bundle，OpenClaw 消息包含索引页链接和少量单篇链接。`published` 表示飞书 bundle 已发布，只有外部消息 ID 或稳定幂等键才能转为 `sent`。

脚本生成消息不等于 OpenClaw 已经实际触达用户。OpenClaw 发送成功后先记录显式回执，再在收到回复时调用 `frontdesk-feedback` 入队脚本；后台只把有回执的 `sent` 条目计为已发送待反馈。
