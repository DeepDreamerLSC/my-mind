# OpenClaw前台协作设计稿

## 版本信息

- 状态：草稿，可试运行
- 更新时间：2026-06-13
- 适用范围：`my-mind` 项目的前台触达、移动端反馈、后台巡检协作和 OpenClaw 接入边界
- 已审视的仓库证据：
  - `design/个人数据资产飞轮设计稿.md`
  - `design/收件箱自动分拣与沉淀设计稿.md`
  - `design/Codex后台与OpenClaw前台分工设计稿.md`
  - `10_项目/个人数据资产系统/项目上下文.md`
  - `00_收件箱/`
  - `85_运行记录/`
  - `.codex/skills/inbox-capture/`
  - `.codex/skills/inbox-triage/`
  - `.codex/skills/inbox-distill/`
  - `.codex/skills/knowledge-intake/`
  - `.codex/skills/frontdesk-push/`
  - `.codex/skills/frontdesk-feedback/`
  - 本机飞书 CLI：`lark-cli 1.0.32`

---

## 一句话定位

OpenClaw 是 `my-mind` 的前台交互层，负责把资料、提醒和反馈带到用户面前；Codex 是后台整理层，负责巡检、判断、候选沉淀、回写和维护知识库结构。

---

## 分工原则

### OpenClaw 负责前台

- 接收用户从微信、Telegram、Slack、语音或其他入口丢来的链接和片段。
- 用户说“入箱”时，调用既有入箱脚本，把内容写入 `00_收件箱/`。
- 用户说“入库”时，调用 `knowledge-intake`，先保存原始材料，再推进候选知识和确认问题。
- 读取 Codex 生成的前台推送文件，并把待读、待沉淀和项目进度摘要推给用户。
- 接收用户的短反馈，并追加到反馈队列。
- 做提醒、触达、轻量查询和移动端交互。

### Codex 负责后台

- 扫描 `00_收件箱/`。
- 通过 `my-mind 后台总控日更` 每天 07:10 执行一次低风险巡检；旧的独立 `收件箱待分拣巡检` 已暂停。
- 生成收件箱巡检报告、待读队列和待沉淀建议。
- 生成给 OpenClaw 推送的精简摘要。
- 消费 OpenClaw 写入的反馈队列。
- 把反馈回写到来源笔记的 `## 阅读思考`。
- 在用户明确确认后调用 `inbox-distill` 或 `knowledge-intake` 生成候选沉淀物。
- 维护项目上下文、任务清单、决策记录和长期知识结构。

### 不共享的职责

- OpenClaw 不直接晋升长期知识。
- OpenClaw 不直接修改 `20_资料库/`、`30_原子笔记/`、`10_项目/` 和 `65_洞察/`。
- OpenClaw 不自动确认事实、决策或项目方向。
- OpenClaw 不自动删除、移动、提交或推送仓库内容。
- Codex 不承担即时消息触达和移动端常驻提醒。

### 飞书负责手机阅读入口

- 本地 `my-mind` 是唯一可信源，保留 Markdown、状态、回链和长期知识结构。
- 飞书知识库或飞书文档只作为手机阅读镜像，不替代本地知识库。
- Codex 负责把精选待读、项目摘要和候选沉淀发布为飞书精选索引页和单篇文章。
- Codex 负责把本地已精选的长期资料同步到飞书知识库，OpenClaw 不判断哪些内容应入选精选。
- OpenClaw 负责把飞书链接推给用户，并收集用户在手机端的短反馈。
- 用户在飞书或 OpenClaw 中的回复先进入 `前台反馈队列.jsonl`，再由 Codex 回写本地文件。

---

## 目录边界

### OpenClaw 可写

第一版只允许 OpenClaw 手动追加写入：

```text
85_运行记录/前台反馈队列.jsonl
```

写入方式必须是 append，不覆盖历史内容。

OpenClaw 可通过受控脚本触发写入，但不手写文件内容：

```text
inbox-capture -> 00_收件箱/
knowledge-intake -> 00_收件箱/、候选知识文件、85_运行记录/入库处理-*.md
```

其中 `knowledge-intake` 只在用户明确说“入库 / 入到知识库 / 保存到知识库 / 直接沉淀”时使用；它生成的是候选，不代表长期知识已确认。

### OpenClaw 可读

```text
85_运行记录/前台推送-*.md
85_运行记录/飞书发布记录.jsonl
85_运行记录/收件箱分拣巡检-*.md
05_流转区/
00_收件箱/*.md
```

OpenClaw 发送前台阅读提醒时必须调用 `.codex/skills/feishu-publish/scripts/prepare_openclaw_feishu_message.py`，只转发飞书知识库精选索引链接、少量重点标题和回复格式。这个脚本会先检查最新前台推送是否已有已发布 bundle；没有就先发布，再生成最终消息。OpenClaw 不应直接调用 `build_openclaw_feishu_message.py` 作为默认入口，也不要退回发送原文链接。读取 `05_流转区/`、最新前台推送或 `00_收件箱/` 只用于状态核验、入箱反馈、入库处理和异常说明，不用于生成最终阅读消息。

执行归属上，OpenClaw 是前台触发器和消息转发者：默认不再设置 07:00、14:00、21:00 三次定时推送，而是读取 `my-mind 后台总控日更` 生成的飞书精选和待确认摘要，在用户询问或早间固定触达时转发。发布、去重、更新、记录、门禁和异常修复仍由 Codex 维护的 `feishu-publish` skill 负责。OpenClaw 不需要另写一套飞书发布逻辑。

### Codex 可写

```text
00_收件箱/
05_流转区/
85_运行记录/
75_提示词库/
20_资料库/
30_原子笔记/
10_项目/个人数据资产系统/
```

其中长期知识目录只在用户明确要求沉淀或确认后写入。

---

## 前台推送文件

Codex 后台生成：

```text
85_运行记录/前台推送-YYYY-MM-DD-HHMM.md
```

OpenClaw 只读取最新一份并推送给用户。

已落地 skill：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py
```

可选参数：

```bash
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --limit 5
python3 .codex/skills/frontdesk-push/scripts/generate_frontdesk_push.py --dry-run
```

建议格式：

```markdown
# my-mind 前台推送

## 今天最值得读

### 1. 标题

- 来源：平台 / 作者
- 价值：为什么值得读
- 建议动作：已读 / 沉淀 / 跳过 / 继续解析
- 建议沉淀方向：
- 来源文件：`00_收件箱/...md`
- 原文链接：平台原文链接
- 分享链接：用户最初丢入的分享入口
- 转录链接：外部转录或原始长文链接，如果存在

#### 内容摘录

正文摘录、OCR 摘录或视频摘要。

#### 阅读时重点

- 读的时候需要判断的问题。

#### 质量提醒

- OCR、转写、来源完整性或事实核验风险。

### 2. 标题

- 来源：
- 价值：
- 建议动作：
- 来源文件：

## 项目进度

- 已完成：
- 当前卡点：
- 下一步建议：

## 可回复指令

- `1 已读：你的想法`
- `1 沉淀成提示词`
- `1 跳过`
- `1 继续解析`
```

前台推送文件本身应具备手机阅读价值，可以包含较完整的摘录、阅读重点、质量提醒和原文跳转链接；默认可以覆盖全量待读。Codex/feishu-publish 会把它拆成“单篇文章 + 精选索引页”。OpenClaw 直接发给用户的聊天消息必须短，且必须由飞书消息出口脚本生成，优先发送飞书知识库精选索引链接、少量重点标题和可回复指令。

---

## 飞书手机阅读层

用户的主要阅读场景在手机，因此 OpenClaw 前台协作需要接入飞书阅读入口。

第一版建议结构：

```text
林总工的知识库
├── 📱 my-mind 手机待读
│   ├── 今日待读
│   └── 待读归档
├── 10_项目精选
├── 20_资料库精选
├── 75_提示词库精选
└── 🧹 已纳入本地待清理
```

OpenClaw 的前台职责主要覆盖 `📱 my-mind 手机待读`。`10_项目精选`、`20_资料库精选` 和 `75_提示词库精选` 由 Codex 后台根据本地同步标记维护。

### 发布边界

适合发布到飞书：

- 当前全量待读资料。
- `05_流转区/` 中的待读、待沉淀和待核验精选。
- `85_运行记录/前台推送-*.md` 的手机阅读版。
- 项目今日最小推进动作。
- 需要用户确认的候选沉淀。
- 需要用户判断的解析异常。
- 已经由 Codex 标记为 `精选同步` 的资料、提示词和项目摘要。

不适合发布到飞书：

- 全量收件箱。
- 全量资料库或原子笔记。
- 未筛选的 OCR 噪声、转写原文和调试日志。
- 隐私或敏感资料。
- 尚未通过入箱门禁的内容。
- 没有本地同步标记、仅因为“目录存在”而被动纳入的内容。

### 推荐流程

1. Codex 生成 `85_运行记录/前台推送-*.md`。
2. OpenClaw 调用 `prepare_openclaw_feishu_message.py`，由 `feishu-publish` 自动发布或复用飞书精选 bundle。
3. `feishu-publish` 追加 `85_运行记录/飞书发布记录.jsonl`。
4. OpenClaw 把脚本输出的飞书阅读链接推给用户；没有真实链接时，提示需要先配置飞书发布命令或让 Codex 修复发布链路。
5. 用户在手机飞书里阅读。
6. 用户回复 OpenClaw：`1 已读：...`、`1 沉淀`、`1 跳过` 或 `1 继续解析`。
7. OpenClaw 追加写入 `85_运行记录/前台反馈队列.jsonl`。
8. Codex 消费反馈队列，回写 `阅读思考` 或进入候选沉淀。

前台推送有冷却机制：Codex 会维护 `85_运行记录/前台推送状态.json`，OpenClaw 不应在同一批条目尚未反馈时重复催促；如果只需要提醒用户，优先提示“上次阅读链接仍待反馈”。

前台推送还会默认跳过已经有正式/已吸收长期知识回链的收件箱来源。也就是说，用户确认并通过转正门禁的条目，不应继续作为待读或待沉淀内容进入飞书精选；需要排查时才显式使用 `--include-promoted`。

长期精选知识库同步另走后台链路：Codex 扫描本地 `飞书同步` 字段，创建或更新飞书精选目录页面，并把结果写入 `85_运行记录/飞书知识库同步记录.jsonl`。OpenClaw 只在需要时读取这些链接并转发给用户。

### 可用命令方向

已落地 skill：

```bash
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --dry-run
python3 .codex/skills/feishu-publish/scripts/publish_feishu_reading.py --write-local
```

真实发布通过命令模板接入，避免把飞书凭证、space id 或租户细节写死到仓库。发布身份必须是用户态，否则 bot 创建的文档可能无法出现在用户手机飞书知识库里。

```bash
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config strict-mode user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli config default-as user
OPENCLAW_HOME="$HOME/.openclaw" lark-cli auth status --verify
```

确认 `identity=user` 后再发布：

```bash
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --doc-format markdown --content @{markdown_file_rel}' \
MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id' \
MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='手机待读目录node_token' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
```

目录规则由 Codex 侧发布脚本负责，OpenClaw 不需要判断。`MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN` 指向 `📱 my-mind 手机待读`，只用于精选索引页；单篇文章默认读取 `85_运行记录/飞书知识库目录映射.local.json`，自动进入 `20_资料库精选/` 对应主题目录。第一版用环境变量传入目标空间，不把空间 ID 写死进仓库：

```bash
export MY_MIND_FEISHU_WIKI_SPACE_ID='目标知识库space_id'
export MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN='手机待读目录node_token'
MY_MIND_FEISHU_PUBLISH_COMMAND='OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create --api-version v2 --wiki-space my_library --title {title} --doc-format markdown --content @{markdown_file_rel}' \
python3 .codex/skills/feishu-publish/scripts/publish_frontdesk_bundle.py --publish
```

第一版不做飞书到本地的双向同步。飞书页面允许重建，本地 `my-mind` 保持源库地位。

---

## 前台反馈队列

OpenClaw 接收到用户回复后，追加写入：

```text
85_运行记录/前台反馈队列.jsonl
```

已落地 skill：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py --channel 微信 "1 已读：我觉得重点是高频流程应该沉淀成 skill。"
```

只预览不写入：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/append_frontdesk_feedback.py --dry-run "2 跳过"
```

建议格式：

```json
{"时间":"2026-06-11 21:00:00 +0800","来源":"OpenClaw","渠道":"微信","推送文件":"85_运行记录/前台推送-2026-06-11-2100.md","目标序号":"1","动作":"已读","内容":"我觉得重点是高频流程应该沉淀成 skill。"}
```

动作枚举：

- `已读`
- `沉淀`
- `跳过`
- `继续解析`
- `补充想法`

Codex 后台消费规则：

- `已读`：把内容回写到来源笔记的 `## 阅读思考`。
- `补充想法`：把内容回写到来源笔记的 `## 阅读思考`。
- `沉淀`：根据目标类型调用 `inbox-distill` 或生成候选资料。
- `继续解析`：触发 OCR、字幕、转写或其他补抓能力。
- `跳过`：记录跳过原因，不删除来源。

已落地消费命令：

```bash
python3 .codex/skills/frontdesk-feedback/scripts/consume_frontdesk_feedback.py --write
```

---

## 入箱与入库流程

### 语义区分

- `入箱`：只保存原始材料，适合“先存着”“以后再看”。
- `入库`：先保存原始材料，再自动分拣并尽量生成候选知识，适合“我觉得这条值得沉淀”。
- `转为长期知识`：用户确认某条候选值得转正，但仍由 Codex 后台执行长期知识转正门禁。
- `沉淀完成`：候选经过确认、改写和复用后，进入长期知识目录，才算真正化为己有。

OpenClaw 不需要向用户解释全部后台步骤，只需要按用户措辞选择入口；有疑问时把 `knowledge-intake` 生成的最小问题清单转发给用户。

### 入箱

OpenClaw 收到链接或片段后，调用：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<链接>"
```

用户主动发来的链接默认写入 `阅读状态: 已读`，因为这通常表示用户已经看过并判断值得保存。自动发现、巡检抓取或用户明确表示尚未阅读的链接，才显式增加 `--reading-status 未读`。

抖音、公开视频等视频链接默认会在公开页面或 `yt-dlp` 暴露媒体/音频地址且时长未超过上限时自动转写，不需要用户二次提示，也不需要 OpenClaw 追加特殊参数。普通入箱默认上限是 360 秒；用户明确说“入库”时，`knowledge-intake` 默认把上限提高到 3600 秒，也就是 1 小时。

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<视频链接>"
```

如果生成文件里的 `内容摘录来源` 为空，说明自动转写未成功或被明确跳过，回复用户时必须说明：`已入箱基础信息，视频内容尚未转写，需后台继续解析`，不要把这种状态描述为“内容已解析”。

如果失败原因是“超过转写上限”，OpenClaw 应表述为“当前前台上限未覆盖，需要后台提高上限重跑”，不要表述为“无法转写”。

只有在紧急提速或排查转写后端时，才显式跳过视频转写：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py --no-extract-content "<视频链接>"
```

小红书图文前台入箱默认使用 PP-OCRv5 文本 OCR，质量优先，Apple Vision 只作为兜底。紧急情况下需要完全跳过 OCR 时，显式增加：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py --no-image-ocr "<小红书链接>"
```

需要后台文档级解析实验时，再显式使用 PaddleOCR-VL：

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py --image-ocr-backend paddleocr-vl --max-ocr-images 9 "<小红书链接>"
```

OpenClaw 入箱后返回：

- 文件路径。
- 解析状态。
- 内容摘录是否已生成。
- 是否执行 OCR 或转写。
- 是否需要后台继续处理。

### 入库

OpenClaw 收到用户明确说“入库”“保存到知识库”“直接沉淀”时，调用：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --raw "<链接或文本>"
```

如果已经有收件箱文件：

```bash
python3 .codex/skills/knowledge-intake/scripts/knowledge_intake.py --write --source "00_收件箱/某条笔记.md"
```

OpenClaw 入库后返回：

- 原始材料是否已保存。
- 是否生成候选资料、候选提示词或候选洞察。
- 候选路径。
- 需要用户确认的问题清单。

OpenClaw 不直接编辑 `20_资料库/`、`75_提示词库/` 或 `65_洞察/` 正文；这些写入由 `knowledge-intake` 或 Codex 后台执行，并保持候选/待确认状态。

### 转为长期知识

OpenClaw 收到“转为长期知识”“确认沉淀”“这条可以转正”时，应先识别对应候选路径或来源序号，再把用户确认意图写入反馈队列或交给 Codex 后台处理。

OpenClaw 返回给用户时，只说：

- 已记录你的转正意图。
- 后台会检查来源、解析质量、术语、中文表达、用户判断和知识关联。
- 通过后才会更新为长期知识并按需同步飞书精选。

OpenClaw 不应直接把候选的 `处理状态` 改为 `已处理`，也不应直接开启 `飞书同步`。如果后台门禁发现转写错词、内容空缺或缺少你的判断，OpenClaw 只转发最小补充问题。

当前 OpenClaw 原生 `openclaw skills list` 不会自动发现项目内 `.codex/skills`；本机已在 OpenClaw workspace 增加薄入口 `my-mind-knowledge-intake`。这个入口只委托本仓库脚本，不复制业务逻辑。

---

## 后台巡检流程

Codex 定时或手动运行：

```bash
python3 .codex/skills/inbox-triage/scripts/triage_inbox.py --mark-sorted --write --report-dir 85_运行记录
```

当前已落地的 Codex App 自动化配置：

- 启用任务：`my-mind 后台总控日更`
- 旧独立任务：`收件箱待分拣巡检` 已暂停
- 频率：每天 07:10 一次
- 运行环境：本地 `my-mind` 工作区
- 核心动作：
  - 自动把新的 `待分拣` 条目标为 `已分拣` 并追加 `分拣记录`
  - 汇总全部 `已分拣` 条目生成阅读队列
  - 刷新 `05_流转区/当前流转总览.md`、待读队列、待沉淀队列和待核验队列
  - 明确提醒“读后反馈可以回写到来源笔记的 `阅读思考`”
  - 禁止在用户未确认前直接沉淀为资料库、原子笔记、提示词库或项目草稿

当前协作约束：

1. 后台巡检报告是 OpenClaw 前台推送的上游输入之一，但不应整篇转发给用户。
2. OpenClaw 应优先消费 `05_流转区/` 和前台推送里的优先项，而不是重新解释 `00_收件箱/` 全量内容。
3. 如果用户仅回复 `已读` 或 `补充想法`，Codex 只回写 `阅读思考`，不直接沉淀。
4. 如果用户明确回复 `沉淀` 或主动说 `入库`，OpenClaw 可以调用 `knowledge-intake`，但只能让脚本生成候选和确认问题，不手写长期知识目录。
5. 如果用户明确说 `转为长期知识`，OpenClaw 只记录确认意图或请求 Codex 后台执行转正门禁，不直接改状态。
6. 如果来源笔记为 `内容质量: 需继续解析`，默认不进入前台推送，应先交给 Codex 后台补 OCR、字幕或转写。

后续增强为：

1. 生成完整巡检报告。
2. 生成 `前台推送-YYYY-MM-DD-HHMM.md`。
3. 读取 `前台反馈队列.jsonl`。
4. 回写阅读思考或触发沉淀。
5. 生成处理记录。

---

## 阶段计划

### 阶段一：前台推送最小闭环

目标：Codex 生成前台推送文件，OpenClaw 读取并推给用户。

验收标准：

- `85_运行记录/前台推送-*.md` 能生成。
- OpenClaw 能读取最新推送文件。
- 用户能在前台看到 3 条以内的待读或待沉淀事项。

### 阶段二：飞书手机阅读入口

目标：Codex 把精选推送发布为飞书单篇文章和精选索引页，OpenClaw 把精选索引链接发给用户，用户能在手机上阅读。

验收标准：

- Codex 能生成适合手机阅读的飞书单篇文章和精选索引页。
- 精选索引页留在 `📱 my-mind 手机待读`，单篇文章进入 `20_资料库精选/` 对应主题目录。
- 同一天的精选索引或旧版 `今日待读` 页面按标题更新，不重复新增飞书页面。
- 飞书页面保留本地来源路径和建议动作。
- OpenClaw 能把飞书链接发送给用户。
- 用户能基于飞书阅读内容回复短反馈。

### 阶段三：反馈队列

目标：OpenClaw 把用户回复写入 `前台反馈队列.jsonl`。

验收标准：

- OpenClaw 只 append，不覆盖。
- 每条反馈包含时间、来源、渠道、推送文件、目标序号、动作和内容。
- Codex 能解析反馈队列。

### 阶段四：反馈回写

目标：Codex 消费反馈队列，把用户想法回写到来源笔记的 `## 阅读思考`。

验收标准：

- `已读` 和 `补充想法` 能正确回写。
- 来源笔记保留反馈时间和来源。
- 已消费反馈有处理记录，避免重复处理。

### 阶段五：确认式沉淀

目标：用户在 OpenClaw 中回复 `沉淀` 后，Codex 生成候选沉淀物。

验收标准：

- 提示词类内容可生成 `75_提示词库/` 候选。
- 资料类内容可生成 `20_资料库/` 候选。
- 来源笔记追加沉淀记录和回链。
- 不自动标记为已晋升。

---

## 风险和边界

- 前台消息可能包含噪声，必须先进入反馈队列，不能直接写长期知识。
- OpenClaw 常驻运行，权限应比 Codex 后台更窄。
- 外部链接和社媒内容存在 prompt injection 风险，不能让链接内容影响 OpenClaw 的工具权限。
- 推送要短，避免把巡检报告原文全部发给用户。
- 自动化只负责低风险动作，长期知识和关键决策必须保留人工确认。
- 飞书只作为阅读镜像，不作为本地知识库的替代品。
- 第一版不做飞书双向同步，避免飞书编辑和本地 Markdown 状态冲突。

---

## 第一版建议

先做四件事：

1. Codex 生成 `85_运行记录/前台推送-*.md`。
2. Codex 把精选推送发布成飞书单篇文章和精选索引页。
3. OpenClaw 调用飞书消息出口脚本，只发送飞书知识库精选索引链接、少量摘要和回复格式。
4. OpenClaw 把用户回复 append 到 `85_运行记录/前台反馈队列.jsonl`。

这四件事跑通后，再做反馈回写和确认式沉淀。
