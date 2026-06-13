# 我的心智

`my-mind` 是一个以本地 Markdown 为源库的个人数据资产系统。它把外部资料、人工智能对话、行业信号、项目记录、原子笔记、主动回忆、洞察和复盘组织成一套可长期运行的数据飞轮。

当前定位：

- 本地仓库是唯一可信源。
- 飞书知识库是手机阅读和精选内容镜像。
- OpenClaw 是前台秘书，负责高频交互、入箱、推送和反馈收集。
- Codex 是后台工程师，负责解析、门禁、分拣、同步、巡检和系统演进。

核心设计稿：

- [个人数据资产飞轮设计稿](design/个人数据资产飞轮设计稿.md)
- [收件箱自动分拣与沉淀设计稿](design/收件箱自动分拣与沉淀设计稿.md)
- [Codex后台与OpenClaw前台分工设计稿](design/Codex后台与OpenClaw前台分工设计稿.md)
- [OpenClaw前台协作设计稿](design/OpenClaw前台协作设计稿.md)
- [前沿情报巡检设计稿](design/前沿情报巡检设计稿.md)

## 快速开始

最常见的使用方式不是手写 Markdown，而是把链接、片段或想法丢给 OpenClaw 或 Codex：

```text
入箱：https://example.com/article
入箱：抖音/小红书/YouTube/X 分享链接
记录前台反馈：1 已读：这条对我有用，后面想沉淀成 Codex 工作流
生成前台推送
同步本地知识库到飞书
后台总控巡检
```

如果要直接运行脚本，先确保在仓库根目录：

```bash
cd /Users/linsuchang/Desktop/work/my-mind
```

然后按下面流程执行。

## 日常流程

1. 收集

把 YouTube、抖音、小红书、X、网页链接或原始片段丢进收件箱。系统会尽量抓取标题、作者、发布时间、封面、正文、文案、字幕、互动数据和视频转写。

2. 门禁和分拣

Codex 会检查入箱内容是否解析充分，标记 `内容质量`，再刷新 `05_流转区/` 的待读、待沉淀、待核验和暂缓队列。

3. 前台推送

Codex 生成 `85_运行记录/前台推送-*.md`。飞书发布脚本把它转成手机阅读页，OpenClaw 只需要把飞书链接和少量摘要发给你。

4. 阅读和反馈

你在手机上读飞书页面，然后回复 OpenClaw：

```text
1 已读：这个可以沉淀成我的 Codex 工作流
2 沉淀成提示词
3 跳过
4 继续解析
```

5. 沉淀和同步

Codex 消费反馈队列，把你的阅读思考回写到来源，并把确认有价值的内容准备成资料库、提示词库或其他长期目录的候选；精选内容再同步到飞书知识库，保持手机可读。

## 常用命令

### 入箱

普通链接入箱：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "https://example.com/article"
```

视频链接强制解析正文或音频转写：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py \
  --extract-content \
  --transcribe-backend faster-whisper \
  --transcribe-model small \
  "https://youtu.be/..."
```

小红书图片笔记默认会尝试 OCR。需要跳过 OCR 时：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py \
  --no-image-ocr \
  "http://xhslink.com/..."
```

只收集基础元数据，不做正文抓取或视频转写：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py \
  --no-extract-content \
  "https://v.douyin.com/..."
```

### 分拣收件箱

预览分拣报告：

```bash
python3 .codex/skills/inbox-triage/scripts/triage_inbox.py
```

写入分拣报告、回写 `已分拣`，并刷新 `05_流转区/`：

```bash
python3 .codex/skills/inbox-triage/scripts/triage_inbox.py \
  --write \
  --mark-sorted \
  --report-dir 85_运行记录
```

### 后台总控巡检

检查自动化、收件箱质量、前台推送状态和反馈队列：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py
```

写入巡检报告：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

### 生成前台推送

生成给 OpenClaw 和飞书精选 bundle 使用的推送稿：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py
```

常用参数：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py \
  --limit 0 \
  --cooldown-hours 24
```

说明：

- `--limit 0` 表示推送全量候选。
- `--cooldown-hours 24` 表示 24 小时内已推送但未反馈的条目不重复推。
- `--force` 会忽略冷却时间。
- `--include-low-quality` 会把 `内容质量: 需继续解析` 的条目也推出来，一般只用于调试。

### 发布飞书精选页

旧版单页预览：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --dry-run
```

只生成旧版本地飞书阅读页草稿：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --write-local
```

真实发布前，确认 `lark-cli` 使用的是 OpenClaw workspace 的用户身份：

```bash
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config strict-mode user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config default-as user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli auth status --verify
```

确认 `identity` 是 `user` 后发布精选 bundle：

```bash
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}' \
MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id' \
MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='手机待读目录node_token' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
```

如果要移动到指定飞书知识库目录，用本地环境变量注入目标空间，不要写进仓库：

```bash
export MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id'
export MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='目标目录node_token'
```

生成 OpenClaw 可直接转发的飞书消息：

```bash
python3 .codex/skills/feishu-publish/scripts/build_openclaw_feishu_message.py
```

这条命令会输出飞书知识库精选索引链接和少量重点标题。它必须找到与最新 `前台推送-*.md` 匹配的已发布 bundle 索引记录；如果找不到，会失败并提示先发布飞书精选 bundle。OpenClaw 不应退回发送原文链接。

### 记录和消费前台反馈

把 OpenClaw 收到的回复追加到反馈队列：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py \
  "1 已读：这条对我有用，后面可以沉淀成工作流"
```

预览消费反馈：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py
```

真实消费反馈，回写阅读思考和处理记录：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py --write
```

只回写反馈，不自动生成提示词候选：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py \
  --write \
  --no-distill
```

### 同步本地知识库到飞书

预览候选和动作：

```bash
python3 .codex/skills/feishu-sync/scripts/sync_selected_notes.py --dry-run
```

真实同步：

```bash
OPENCLAW_HOME="$HOME/.openclaw" lark-cli auth status --verify
python3 .codex/skills/feishu-sync/scripts/sync_selected_notes.py --publish
```

`20_资料库/` 下的 Markdown 默认会作为精选同步候选。其他目录需要显式 front matter：

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

如需按本地目录映射飞书目录，创建本地私有文件 `85_运行记录/飞书知识库目录映射.local.json`：

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

同步脚本会通过本地 front matter、同步记录和飞书搜索避免重复创建文档。

### 巡检前沿情报

生成前沿情报巡检报告：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py
```

只看某个门类：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py \
  --category AI与Agent工具 \
  --limit 3
```

把巡检入选项写入收件箱需要显式确认：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py --write-inbox
```

从已有巡检报告里选择条目入箱：

```bash
python3 .codex/skills/frontier-watch/scripts/frontier_watch.py \
  --from-report "85_运行记录/前沿情报巡检-YYYY-MM-DD-HHMM.md" \
  --select 1-3 \
  --write-inbox
```

## 目录怎么用

| 目录 | 用途 |
| --- | --- |
| `00_收件箱/` | 未处理内容入口。保存链接、原始片段、解析结果、视频转写和质量门禁。 |
| `05_流转区/` | 短期行动视图，包括待读、待沉淀、待核验和暂缓。这里是生成区，可以被刷新覆盖。 |
| `10_项目/` | 项目上下文、目标、决策、任务、问题和风险。 |
| `20_资料库/` | 经过初步整理的资料，当前默认纳入飞书精选同步。 |
| `30_原子笔记/` | 长期可复用的单点知识。 |
| `35_主动回忆/` | 回忆卡片和复习队列。 |
| `60_行业情报/` | 外部变化信号和行业观察。 |
| `65_洞察/` | 跨资料、项目和经验形成的候选、已验证、已应用或已退役洞察。 |
| `75_提示词库/` | 可复用的人工智能协作提示词和工作流。 |
| `80_复盘/` | 周复盘、月复盘和项目复盘。 |
| `85_运行记录/` | 自动化报告、前台推送、飞书发布记录、反馈队列、同步记录和巡检报告。 |
| `_模板/` | 笔记模板。 |
| `_附件/` | 图片、音频、视频、导出文件等附件。 |
| `design/` | 系统设计稿和阶段规划。 |

## 质量门禁

收件箱笔记会尽量包含这些状态：

```yaml
处理状态: 待分拣
阅读状态: 已读
内容质量: 可推送
质量门禁:
  状态: 通过
```

用户主动发来的入箱链接默认视为 `阅读状态: 已读`，后续分拣会把它当作待补判断/待沉淀材料，而不是继续催你阅读。自动发现、巡检抓取或你明确还没读过的链接，可以在入箱时写成 `阅读状态: 未读`。

常见 `内容质量`：

- `可推送`：信息量足够，可以进入前台推送或分拣。
- `需核验`：有摘要或片段，但来源、转写或关键信息需要人工核验。
- `需继续解析`：只拿到链接或低价值元数据，不应该直接推送。

默认前台推送会跳过 `需继续解析`。如果抖音、小红书或 YouTube 没解析出正文，要优先重新入箱或强制转写，而不是把空文档沉淀进长期知识。

## 自动化

当前 Codex App 里启用了两个后台自动化：

- `收件箱待分拣巡检`：每 6 小时执行一次，负责分拣收件箱和刷新流转区。
- `收件箱入箱门禁审核`：每 4 小时执行一次，负责检查入箱质量、推送状态和反馈队列异常。

自动化配置不在仓库内，运行记录写入 `85_运行记录/`。如果怀疑自动化停了，先跑：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

## OpenClaw 和 Codex 的分工

OpenClaw 适合做前台秘书：

- 接收你从手机复制来的链接或片段。
- 调用入箱技能，把内容写入 `00_收件箱/`。
- 调用 `build_openclaw_feishu_message.py` 读取最新飞书知识库链接，必要时拆分成多条短消息推给你。
- 收集你的短反馈，并追加到 `85_运行记录/前台反馈队列.jsonl`。
- 不直接判断长期知识结构，不直接改写资料库正文。

Codex 适合做后台工程师：

- 维护解析脚本、OCR、视频转写、门禁和异常修复。
- 巡检收件箱、分拣队列、反馈队列和自动化健康。
- 根据你的反馈把内容准备成资料库、提示词库、项目或洞察候选，并在确认后沉淀。
- 管理飞书同步、防重复、目录映射和运行记录。
- 继续扩展设计稿、技能和测试。

## 飞书使用原则

- 本地 Markdown 是源库，飞书是手机阅读镜像。
- 每日前台待读用 `feishu-publish`，精选长期知识用 `feishu-sync`。
- 飞书侧编辑不要当作最终正文；需要保留的修改应回到本地 Markdown。
- 凭证、`space_id`、`node_token` 和目录映射只放在本地环境变量或 `.local.json` 文件，不提交进仓库。
- 如果手机飞书看不到页面，优先检查 `lark-cli` 是否是 `identity = user`。

## 故障排查

抖音、小红书或 YouTube 只有标题，没有正文：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py \
  --extract-content \
  --transcribe-backend faster-whisper \
  --transcribe-model small \
  "原链接"
```

小红书 OCR 太慢或卡住：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py \
  --image-ocr-backend apple-vision \
  "原链接"
```

前台重复推送同一条：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py \
  --cooldown-hours 24
```

飞书同步担心重复建文档：

```bash
python3 .codex/skills/feishu-sync/scripts/sync_selected_notes.py --dry-run
```

反馈队列有堆积：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py --write
```

不知道下一步该跑什么：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

## 工作边界

- 收件箱是入口，不是长期知识。
- 流转区是行动视图，不是事实源。
- 运行记录是审计材料，不是长期知识。
- 未经确认的内容不自动晋升为原子笔记、洞察或提示词。
- 外部来源摘要可以进入收件箱，但进入长期知识前需要信达雅的中文整理和必要核验。
- 自动化可以整理、提醒和准备候选，但最终哪些内容代表你的知识资产，要以你的阅读反馈和后续使用价值为准。
