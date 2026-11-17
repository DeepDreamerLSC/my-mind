---
类别: 运行记录
记录类型: 决策审视
项目: edu-agent
项目键: edu-agent
生成时间: 2026-09-28 20:34:20 +0800
审视模型建议: gpt-5.4 / xhigh
最高风险: 高
建议动作: 把任务清单压缩成 3 个以内的本周最小动作。
---

# edu-agent 决策审视

## 总览

- 项目阶段：PDF 题目入库与智能排版质量门禁阶段
- 目标：把 PDF 题目入库、裁题/OCR、存储、OpenAPI、前端对接和质量验证收敛成可复核交付。
- 风险等级：高
- 结论：需要先收敛方向和工作区，再继续扩大实现。
- 近期 commit：5
- 工作区改动：1339
- 项目文件：7
- 自动修改项目文件：否

## 当前隐含假设

- 当前阶段仍然是「PDF 题目入库与智能排版质量门禁阶段」，继续投入应服务于：把 PDF 题目入库、裁题/OCR、存储、OpenAPI、前端对接和质量验证收敛成可复核交付。
- 近期 commit 和工作区变化能够代表真实进展，而不是只代表自动化产物增加。
- 项目巡检判断：判断置信度：中
- 项目巡检判断：Codex 判断：优先判断 edu-agent 的代码变化是否已经形成可复核交付，而不是只记录文件列表。
- 项目巡检判断：后端服务层有变化，可能涉及文件上传下载、OpenAPI、模型网关、任务处理或存储抽象。

## 反方视角

- 反方意见：如果今天只允许做一件事，最可能产生价值的是验证一个真实使用闭环，而不是继续完善框架。

## 偏航风险

- 任务清单未完成项较多，容易把忙碌误认为推进。
- 后端代码有变化但测试证据不足，可能提前进入功能扩张。
- 工作区已有 1339 个未提交改动，当前决策横跨多个批次。
- 近期已有 commit 但工作区仍未闭合，项目结论可能跨越多个未复核上下文。

## 机会成本

- 继续沿当前路径投入，会推迟一次更小范围的验收和复盘。

## 下个最小可逆动作

- 把任务清单压缩成 3 个以内的本周最小动作。
- 先按代码、文档、运行状态分批收敛，再继续新增能力。
- 用一次真实工作场景验证：这个项目输出是否帮你更快、更稳地做决策。

## 现在先别做

- 暂时不要把新增报告数量当作项目质量。

## OpenClaw 可提醒问题

- edu-agent 当前最高风险是否符合你的直觉，还是需要 Codex 重新审视证据？
- 是否先暂停新增功能，优先整理当前工作区并分批提交？

## 证据概览

- 后端：1327 项
- 其他：7 项
- 配置：5 项

## Commit 证据

- `b14d7819e` 2026-09-03T09:02:07+08:00：Merge remote-tracking branch 'origin/pr212' into local-preview-211-212
- `0a1f7e01f` 2026-09-03T00:59:33Z：!211 Phase 1: 小讲师题图链身份治理（模型网关+控制台）
- `f0824825f` 2026-09-03T00:58:43Z：!209 保留 Silver 24 题图基线 r6：修复验证与 #206 严格合同冒烟
- `27efca291` 2026-09-03T08:49:02+08:00：Phase 1(评审第2轮 P1 修复·补): 零增长收口——AdminApp 恢复基线/前端自取数
- `509adc9af` 2026-09-03T08:44:13+08:00：Phase 1(评审第2轮 P1 修复): 身份缺失阻断/fail-fast/v1 契约校验/reload 回放/脚本 AsyncClient

## 工作区样例

- `M` `configs/model_registry.prompt-lab-debug-tutor.yaml`
- `M` `configs/model_registry.test-vision.yaml`
- `M` `configs/model_runtime.test.yaml`
- `M` `edu_agent/__init__.py`
- `D` `edu_agent/app/__init__.py`
- `D` `edu_agent/app/admin/__init__.py`
- `D` `edu_agent/app/admin/access_control_schemas.py`
- `D` `edu_agent/app/admin/alert_state.py`
- `D` `edu_agent/app/admin/assets/login.html`
- `D` `edu_agent/app/admin/audit.py`
- `D` `edu_agent/app/admin/auth.py`
- `D` `edu_agent/app/admin/capabilities.py`
- `D` `edu_agent/app/admin/capabilities_registry.py`
- `D` `edu_agent/app/admin/conversation_governance_capabilities.py`
- `D` `edu_agent/app/admin/data_scope.py`
- `D` `edu_agent/app/admin/identity_schemas.py`
- `D` `edu_agent/app/admin/local_session.py`
- `D` `edu_agent/app/admin/prepared_question_read_access.py`
- `D` `edu_agent/app/admin/schemas.py`
- `D` `edu_agent/app/admin/services/__init__.py`
- 另有 1319 项未展开。

## 证据来源

- 项目进展巡检: `85_运行记录/项目进展巡检-edu-agent-2026-09-28-2034.md`
- 建议分析: `85_运行记录/建议分析-2026-09-28-0715-2.md`
- 项目文件: `10_项目/edu-agent/任务清单.md`
- 项目文件: `10_项目/edu-agent/决策记录.md`
- 项目文件: `10_项目/edu-agent/问题清单.md`
- 项目文件: `10_项目/edu-agent/项目上下文.md`
- 项目文件: `10_项目/edu-agent/项目总览.md`
- 项目文件: `10_项目/edu-agent/项目进展.md`
- 项目文件: `10_项目/edu-agent/风险清单.md`
