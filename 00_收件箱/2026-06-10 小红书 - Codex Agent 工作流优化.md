---
类别: 收件箱
资料类型: 社媒链接
来源平台: 小红书
标题: Codex Agent 工作流优化
作者或频道: Kriswillwin
作者编号: 65acfa6c000000000e003c78
发布时间: 2026-06-04
捕获时间: "2026-06-10 12:58:34 +0800"
来源链接: "https://www.xiaohongshu.com/explore/6a21650b000000000702651f"
原始链接: "http://xhslink.com/o/7ZGjSx5sdPA"
内容编号: 6a21650b000000000702651f
时长: 
封面图: "http://sns-webpic-qc.xhscdn.com/202606101258/dd8e30ab8437743384f54f1d663250e2/notes_pre_post/1040g3k032109k4j87u005pdcv9m3gf3o00eki1o!nd_prv_wlteh_jpg_3"
点赞数: 21
评论数: 3
收藏数: 19
分享数: 3
解析工具: 公开页面 HTML
解析器: XiaoHongShu HTML
解析状态: 已解析
外部转录链接: 
外部转录来源: 
字幕来源: 
字幕语言: 
处理状态: 已分拣
关联项目:
  - 个人数据资产系统
关联领域: []
主题:
  - Codex
  - 提示词
  - 工作流
标签:
  - 社媒链接
  - 小红书
敏感状态: 未知
---

# Codex Agent 工作流优化

## 基础信息

- 来源平台：小红书
- 作者或频道：Kriswillwin
- 作者编号：65acfa6c000000000e003c78
- 作者主页：https://www.xiaohongshu.com/user/profile/65acfa6c000000000e003c78
- 发布时间：2026-06-04
- 时长：未知
- 来源链接：https://www.xiaohongshu.com/explore/6a21650b000000000702651f
- 内容编号：6a21650b000000000702651f
- 缩略图：http://sns-webpic-qc.xhscdn.com/202606101258/dd8e30ab8437743384f54f1d663250e2/notes_pre_post/1040g3k032109k4j87u005pdcv9m3gf3o00eki1o!nd_prv_wlteh_jpg_3
- 解析状态：已解析

## 解析说明

yt-dlp 解析失败：ERROR: [XiaoHongShu] 6a21650b000000000702651f: No video formats found!; please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. Confirm you are on the latest version using  yt-dlp -U

已使用小红书公开页面备用解析补充基础信息。

## 平台信息

- 真实链接：https://www.xiaohongshu.com/discovery/item/6a21650b000000000702651f?app_platform=harmony&app_version=9.33.4&share_from_user_hidden=true&xsec_source=app_share&type=normal&xsec_token=CBh5VnVUaLWtLOPNIgsJJj4J3wwk1CPCoUYBMVOSWfvtU=&author_share=1&&apptime=1781066673ignoreEngage=true&shareRedId=N0s2RkQ8Ozs2NzUyOTgwNjY0OTc6ODg6&share_id=5ae63874ce2f428099986d93bcb89a2d&xhsshare=CopyLink
- 笔记编号：6a21650b000000000702651f
- 作者编号：65acfa6c000000000e003c78
- 笔记类型：normal
- 图片数：1
- 点赞数：21
- 评论数：3
- 收藏数：19
- 分享数：3
- 封面图：http://sns-webpic-qc.xhscdn.com/202606101258/dd8e30ab8437743384f54f1d663250e2/notes_pre_post/1040g3k032109k4j87u005pdcv9m3gf3o00eki1o!nd_prv_wlteh_jpg_3

## 文案摘录

基于自己的工作流优化了一下 Codex 的 subagents 使用方式
	
小 Feature：feature_coder ↔ feature_reviewer 对抗闭环，最后 human review
大 Feature：方案确认 → squad 实现 → 验收，人主要介入阶段 1 和阶段 3
	
核心目标：减少上线后问题，让 Codex 不只是“会写代码”，而是按工程流程先查证、再判断、再实现、最后验收。
	
工作流一：小 Feature 对抗闭环
适合范围明确、改动小、风险中低的任务。
Human 给需求和边界
↓
feature_coder 实现最小改动
↓
feature_reviewer 对抗审查
↓
feature_coder 修复 blocker
↓
feature_reviewer 复审
↓
Human 最后 review
	
工作流二：大 Feature 阶段门控
适合跨模块、跨仓库、上线风险较高、需求需要先收敛的任务。
阶段 1：方案确认，人主导
↓
阶段 2：Squad 实现，agent 主导
↓
阶段 3：验收，人主导
	
* 主 Agent 负责理解需求、控制范围、最终决策。
* 小 Feature 通过 coder-reviewer 对抗提高细节质量。
* 大 Feature 通过阶段门控降低方向和验收风险。
* Subagents 负责独立取证、审查、复现、查文档。
* 默认只读；只有 coder、UI 复现、验收取证允许 workspace-write。
* 不提交、不推送、不做破坏性操作。
* 所有结论必须基于真实文件、日志、请求链或官方来源。
* 控制并发和递归，避免 token 和时间失控。#vibecoding大赏[话题]# #codex[话题]#

## 标签和分类

### 分类
- 暂无

### 标签
- vibecoding大赏
- codex

## 字幕可用性

### 手动字幕
- 未发现

### 自动字幕
- 未发现

## 为什么保存

待补充。

## 初步想法

待补充。

## 后续处理建议

- 判断是否需要进入资料库。
- 如果有字幕或后续转写，再进行摘要、关键观点和待验证事实提取。
- 如果与项目相关，再萃取到项目上下文、任务清单或问题清单。

## 沉淀记录

- 2026-06-10：已整理为候选提示词：[Codex Agent 工作流优化](../75_提示词库/Codex%20Agent%20工作流优化.md)。状态：候选，尚未确认长期知识或项目决策。

## 原始链接

https://www.xiaohongshu.com/explore/6a21650b000000000702651f
