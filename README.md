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
入库：https://example.com/article
入库：这段内容值得沉淀...
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

1. 收集或入库

把 YouTube、抖音、小红书、X、网页链接或原始片段丢进收件箱。系统会尽量抓取标题、作者、发布时间、封面、正文、文案、字幕、互动数据和视频转写。

如果你说“入箱”，系统只保存原始材料，后续等分拣和阅读反馈；如果你说“入库”，系统仍会先入箱保存证据链，但会继续自动分拣并尽量生成资料库、提示词库或候选洞察草稿。

2. 门禁、分拣和候选沉淀

Codex 会检查入箱内容是否解析充分，标记 `内容质量`，再刷新 `05_流转区/` 的待读、待沉淀、待核验和暂缓队列。

`入库` 流程会进一步生成候选知识，默认标记为 `处理状态: 候选`、`吸收状态: 待确认`。这还不是最终“化为己有”的知识。

你对 OpenClaw 说“转为长期知识”时，它只代表你的确认意图，不代表绕过后台检查。候选仍要经过长期知识转正门禁，通过后才把状态改成已处理/已吸收，并按需要开启飞书精选同步。

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
  --max-transcribe-seconds 3600 \
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

### 入库

`入库` 是上层编排：先调用入箱能力保存原始材料，再分拣、生成候选知识和确认问题清单。

预览：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py \
  "https://example.com/article"
```

真实入库：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py \
  --write \
  --raw "https://example.com/article"
```

处理已有收件箱来源：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py \
  --write \
  --source "00_收件箱/某条笔记.md"
```

常用目标：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --target library --source "00_收件箱/..."
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --target prompt --source "00_收件箱/..."
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --target insight --source "00_收件箱/..."
```

运行结果会写入 `85_运行记录/入库处理-*.md`，并把候选回链追加到来源笔记的 `沉淀记录`。解析不足、未读或敏感状态不明时，只生成确认问题，不硬写长期知识。候选资料默认暂停飞书精选同步，确认后再开启。

候选稿会带 `转正门禁` 字段和“长期知识转正”正文段落。用户或 OpenClaw 说“转为长期知识”后，Codex 后台应检查门禁；未通过时只保留候选并列出最小补充问题。

普通入箱的视频自动转写默认上限是 360 秒；明确“入库”时，`knowledge-intake` 会把上限提高到 3600 秒，也就是 1 小时。更长的视频仍可用 `--max-transcribe-seconds` 显式调整。

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

查看给自己看的后台总览：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --view dashboard
```

查看给 OpenClaw 的前台提醒稿：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --view openclaw
```

写入巡检报告和固定总览：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

输出位置：

- `85_运行记录/后台总控巡检-*.md`：完整证据报告。
- `85_运行记录/后台总览/当前后台状态.md`：固定覆盖的后台状态面板。
- `85_运行记录/后台总览/OpenClaw待提醒.md`：固定覆盖的秘书提醒稿。
- `85_运行记录/后台总览/飞书仪表盘数据.json`：飞书多维表格同步数据。
- `85_运行记录/后台总览/飞书仪表盘数据/*.csv`：按表拆分的 CSV。

只刷新飞书仪表盘数据：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --export-dashboard-data
```

### 飞书仪表盘同步

把后台总览数据同步到飞书多维表格，默认 dry-run，不写飞书：

```bash
python3 .codex/skills/feishu-dashboard/scripts/sync_dashboard_rows.py
```

真实同步时使用 OpenClaw 的飞书用户身份，并通过环境变量或本地配置提供 Base token：

```bash
MY_MIND_FEISHU_DASHBOARD_BASE_TOKEN='base_token' \
python3 .codex/skills/feishu-dashboard/scripts/sync_dashboard_rows.py --refresh-data --write
```

本地配置文件可放在 `85_运行记录/飞书仪表盘配置.local.json`，不要提交。同步脚本按 `记录键` 搜索远端记录，找到则更新，找不到才新增，避免重复行。

主动同步时可以直接对 Codex 或 OpenClaw 说“同步飞书仪表盘”“刷新后台驾驶舱”或“更新多维表格看板”。后台会先刷新 `backend-control` 总览，再同步飞书多维表格。

### 解析质量修复

低质量解析队列不要直接进入前台推送或候选转正。先让后台修复明显问题：

```bash
python3 .codex/skills/parse-quality-repair/scripts/repair_parse_quality.py
```

真实写入修复记录、质量标签和待补抓清单：

```bash
python3 .codex/skills/parse-quality-repair/scripts/repair_parse_quality.py --write --limit 5
```

输出位置：

- `05_流转区/40_待核验/解析质量修复队列.md`
- `85_运行记录/解析质量修复-*.md`

第一版只自动修复低风险问题，例如繁体转简体、明显术语误识别、质量门禁说明缺失。需要重抓、补 OCR、补字幕或长视频重转写的条目会继续留在待核验队列，不会硬推到长期知识。

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
- 已经有正式/已吸收长期知识回链的收件箱来源会被默认跳过；调试时可用 `--include-promoted` 临时包含。

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

确认 `identity` 是 `user` 后，OpenClaw 默认只调用一个出口。这个出口会先检查最新前台推送是否已有飞书精选 bundle；没有就发布，再生成可直接转发的飞书消息：

```bash
python3 .codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py
```

飞书精选 bundle 的目录规则：

- `📱 my-mind 手机待读` 只放“今日精选”索引页，方便 OpenClaw 给手机端发一个入口。
- 每条单篇文章会按标题、来源、摘要和沉淀方向，自动归到 `20_资料库精选/` 的对应主题目录。
- 同一天的入口页按标题复用并更新；新的 `前台推送-*.md` 不应造成飞书里新增多个“今日待读/今日精选”页面。
- 如果已有单篇文章内容相同但父目录不对，重跑发布会移动已有 Wiki 节点，不重复新建文档。

如果要移动索引页到手机待读目录，用本地环境变量注入目标空间，不要写进仓库：

```bash
export MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id'
export MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='手机待读目录node_token'
```

单篇文章目录映射默认读取本地私有文件 `85_运行记录/飞书知识库目录映射.local.json`。要调试归类结果，先跑：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --dry-run
```

底层调试命令：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
python3 .codex/skills/feishu-publish/scripts/build_openclaw_feishu_message.py
```

`prepare_openclaw_feishu_message.py` 会输出飞书知识库精选索引链接和少量重点标题。它会自动补齐飞书发布链路；如果发布失败，OpenClaw 应把失败原因转成短提示，不应退回发送原文链接或前台推送 Markdown。

### 记录和消费前台反馈

把 OpenClaw 收到的回复追加到反馈队列：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py \
  "1 已读：这条对我有用，后面可以沉淀成工作流"
```

如果 OpenClaw 展示的是 `05_流转区/50_待确认/待确认队列.md`，用户可以回复：

```text
1 确认转正
1 继续核验
1 调整分类：提示词
1 调整分类：资料库
1 跳过
```

OpenClaw 应把待确认回复也追加到同一个反馈队列。若需要显式指定待确认队列：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py \
  --push-file "05_流转区/50_待确认/待确认队列.md" \
  "1 确认转正"
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

消费待确认回复时，Codex 会执行转正门禁。通过后会更新候选文件和来源文件，并把候选标记为飞书精选待同步。需要立刻同步飞书时：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py \
  --write \
  --sync-feishu
```

没有 `--sync-feishu` 时，只开启本地 `飞书同步: 精选同步 / 待同步` 标记，后续可由 `feishu-sync` 统一发布。

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
| `15_索引/` | Obsidian 导航层，包括项目索引、主题索引、洞察索引和 Bases 动态视图。 |
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

## Obsidian 关联层

第一阶段先使用 Obsidian 原生能力，不引入 Dataview 作为默认依赖：

- `15_索引/项目索引.md`：从项目视角进入知识库。
- `15_索引/主题索引.md`：从长期主题和资料分类进入知识库。
- `15_索引/洞察索引.md`：从候选、已验证、已应用、已退役洞察进入知识库。
- `15_索引/视图/*.base`：用 Bases 查看待读、待沉淀、候选待确认和飞书同步状态。
- `15_索引/视图/项目知识面板.base`：按项目查看项目文件、关联资料、关联洞察、关联提示词和项目影响待确认内容。

`关联项目`、`关联领域`、`主题` 三个字段建议填写 Obsidian 双链：

```yaml
关联项目:
  - "[[10_项目/个人数据资产系统/项目总览|个人数据资产系统]]"
关联领域:
  - "[[20_资料库/工作流与自动化/目录说明|工作流与自动化]]"
主题:
  - "[[Codex 工作流]]"
```

生成某条笔记的关联建议：

```bash
python3 .codex/skills/obsidian-linking/scripts/suggest_links.py \
  --note "20_资料库/管理与组织/某条资料.md" \
  --write
```

脚本只生成建议报告，不直接改写原笔记；确认后再把推荐链接补进 front matter 或正文。

## 项目管理联动

项目目录是行动主线，知识库是判断燃料。进行中的项目需要维护这些 front matter 字段：

```yaml
项目状态: 进行中
当前阶段: 阶段三：跑通小闭环与项目联动
优先级: 高
下次复盘时间: 2026-06-20
```

入库材料会生成 `项目影响建议`：

- 可能转成任务候选。
- 可能转成决策候选。
- 可能转成风险提醒。
- 也可能只是项目背景资料。

这些建议会写进入库候选和 `85_运行记录/入库处理-*.md`，但不会直接改 `任务清单`、`决策记录` 或 `风险清单`。确认后再手动或由 Codex 按反馈回写项目文件。

### 项目进度

项目进度不是直接同步 commit，而是分三层：

- `项目进展.md`：确认后的项目进度结论。
- `变更证据.md`：commit、运行记录、重要工作区变化等证据索引。
- `项目周报.md`：每周候选和确认后的项目摘要。

生成项目进展巡检报告：

```bash
python3 .codex/skills/project-progress/scripts/project_progress.py --write
```

只预览，不写文件：

```bash
python3 .codex/skills/project-progress/scripts/project_progress.py --dry-run
```

确认后把候选摘要追加到项目文件：

```bash
python3 .codex/skills/project-progress/scripts/project_progress.py --write --apply
```

定时任务默认只写 `85_运行记录/项目进展巡检-*.md`，不自动 commit，也不直接改 `项目进展.md`。

项目进展巡检不是纯汇总。报告必须包含 `Codex 项目分析`：

- 阶段判断：当前项目推进到哪里。
- 有效进展：哪些变化真的代表项目推进。
- 证据噪声：哪些只是自动刷新、临时文件或过程记录。
- 风险与阻塞：哪些问题会影响项目质量或提交节奏。
- 下一步建议：接下来应该先做什么。
- 回写建议：是否适合把候选写入 `项目进展.md`。

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

## 长期知识转正门禁

长期知识转正门禁检查候选稿，而不是收件箱入口。它回答的是：这条候选是否已经可以代表我的知识资产。

候选稿应包含：

```yaml
处理状态: 候选
吸收状态: 待确认
转正门禁:
  状态: 待检查
```

通过转正前至少检查：

- 原始来源已保留，来源链接或来源文件可追溯。
- 解析质量足够，视频转写、OCR 或摘录没有明显空缺。
- 关键术语已经校对，英文产品名、人名和工具名没有明显误识别。
- 正文已用简体中文重写，区分来源事实、我的判断和待验证内容。
- 至少有一条可复用结论、提示词、项目影响或行动建议。
- 已建立必要的项目、领域或主题关联。

通过后，Codex 可以把候选标记为 `处理状态: 已处理`，把 `吸收状态` 改为 `已吸收` 或 `已应用`，并按需要开启飞书精选同步。未通过时，候选继续停留在待确认或待核验状态，不进入长期知识转正。

## 自动化

当前 Codex App 里启用了多个后台自动化：

- `收件箱待分拣巡检`：每 6 小时执行一次，负责分拣收件箱和刷新流转区。
- `收件箱入箱门禁审核`：每 6 小时执行一次，先运行 `parse-quality-repair --write --limit 5`，再检查入箱质量、推送状态和反馈队列异常。
- `前台反馈与待确认消费`：每 6 小时执行一次，运行 `frontdesk-feedback --write --sync-feishu`，消费普通阅读反馈和候选待确认回复。
- `前沿情报每日入箱`：每天早上运行前沿情报巡检并把通过门禁的候选入箱。
- `项目进展每日巡检`：每天 20:30 生成项目进展候选报告，读取 git、运行记录和项目文件，但不自动 commit，也不自动 `--apply`。
- `飞书仪表盘每日同步`：每天 07:00、14:00、21:00 刷新后台总览，并把结构化数据同步到飞书多维表格。

自动化配置不在仓库内，运行记录写入 `85_运行记录/`。如果怀疑自动化停了，先跑：

```bash
python3 .codex/skills/backend-control/scripts/backend_health_check.py --write
```

日常不需要翻每条自动化的执行结果，优先看：

- `85_运行记录/后台总览/当前后台状态.md`
- `85_运行记录/后台总览/OpenClaw待提醒.md`

## OpenClaw 和 Codex 的分工

OpenClaw 适合做前台秘书：

- 接收你从手机复制来的链接或片段。
- 你说“入箱”时，只调用入箱技能，把内容写入 `00_收件箱/`。
- 你说“入库”时，调用 `knowledge-intake`，先入箱，再推进到候选知识和确认问题清单。
- 当前 OpenClaw 原生 `openclaw skills list` 不会自动发现项目内 `.codex/skills/knowledge-intake`；本机 OpenClaw workspace 已补薄入口 `my-mind-knowledge-intake`，实际仍调用本仓库脚本，避免两套实现分叉。
- 调用 `prepare_openclaw_feishu_message.py` 自动发布或复用最新飞书精选，并把飞书知识库链接拆成短消息推给你。
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
  --max-transcribe-seconds 3600 \
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
- 待读内容是输入判断，不代表认可。
- 待沉淀内容是候选加工队列，不代表已经化为己有。
- 化为己有的内容必须进入 `20_资料库/`、`30_原子笔记/`、`65_洞察/`、`75_提示词库/` 等长期目录，并经过确认、复用或同步。
- 未经确认的内容不自动晋升为原子笔记、洞察或提示词。
- 外部来源摘要可以进入收件箱，但进入长期知识前需要信达雅的中文整理和必要核验。
- 自动化可以整理、提醒和准备候选，但最终哪些内容代表你的知识资产，要以你的阅读反馈和后续使用价值为准。
