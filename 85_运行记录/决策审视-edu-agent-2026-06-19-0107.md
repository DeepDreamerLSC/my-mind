---
类别: 运行记录
记录类型: 决策审视
项目: edu-agent
项目键: edu-agent
生成时间: 2026-06-19 01:07:54 +0800
审视模型建议: gpt-5.4 / xhigh
最高风险: 中
建议动作: 把任务清单压缩成 3 个以内的本周最小动作。
---

# edu-agent 决策审视

## 总览

- 项目阶段：PDF 题目入库与智能排版质量门禁阶段
- 目标：把 PDF 题目入库、裁题/OCR、存储、OpenAPI、前端对接和质量验证收敛成可复核交付。
- 风险等级：中
- 结论：可以继续推进，但下一步必须保持小而可逆。
- 近期 commit：20
- 工作区改动：16
- 项目文件：7
- 自动修改项目文件：否

## 当前隐含假设

- 当前阶段仍然是「PDF 题目入库与智能排版质量门禁阶段」，继续投入应服务于：把 PDF 题目入库、裁题/OCR、存储、OpenAPI、前端对接和质量验证收敛成可复核交付。
- 近期 commit 和工作区变化能够代表真实进展，而不是只代表自动化产物增加。
- 项目巡检判断：判断置信度：高
- 项目巡检判断：Codex 判断：优先判断 edu-agent 的代码变化是否已经形成可复核交付，而不是只记录文件列表。
- 项目巡检判断：后端服务层有变化，可能涉及文件上传下载、OpenAPI、模型网关、任务处理或存储抽象。

## 反方视角

- 反方意见：如果今天只允许做一件事，最可能产生价值的是验证一个真实使用闭环，而不是继续完善框架。

## 偏航风险

- 任务清单未完成项较多，容易把忙碌误认为推进。
- 近期已有 commit 但工作区仍未闭合，项目结论可能跨越多个未复核上下文。

## 机会成本

- 继续沿当前路径投入，会推迟一次更小范围的验收和复盘。

## 下个最小可逆动作

- 把任务清单压缩成 3 个以内的本周最小动作。
- 用一次真实工作场景验证：这个项目输出是否帮你更快、更稳地做决策。

## 现在先别做

- 暂时不要把新增报告数量当作项目质量。

## OpenClaw 可提醒问题

- edu-agent 当前最高风险是否符合你的直觉，还是需要 Codex 重新审视证据？

## 证据概览

- 测试：8 项
- 后端：5 项
- 配置：2 项
- 其他：1 项

## Commit 证据

- `ec978593d` 2026-06-19T01:03:57+08:00：Require capability-specific gates for question image comparisons
- `47c73f6cc` 2026-06-19T00:54:28+08:00：Reduce risk before splitting oversized ingestion modules
- `c9e58d9b2` 2026-06-19T00:38:32+08:00：Freeze oversized module line budgets
- `9ae4a6944` 2026-06-19T00:31:48+08:00：Ignore runtime artifact drift
- `290d402c7` 2026-06-19T00:28:33+08:00：Scope workbook list and page-range access
- `5568e4341` 2026-06-19T00:24:28+08:00：Scope workbook repository mutations
- `767529038` 2026-06-19T00:07:51+08:00：Run config preflight before ingestion smoke
- `2795373d7` 2026-06-19T00:05:34+08:00：Lint production registry URL defaults
- `c2da8b302` 2026-06-18T23:58:10+08:00：Fail fast on layout sidecar preflight
- `d1b56c4f2` 2026-06-18T23:43:45+08:00：Gate asset edit sidecars on health preflight

## 工作区样例

- `M` `configs/model_registry.test-vision.yaml`
- `M` `configs/model_registry.yaml`
- `M` `edu_agent/app/models/providers/external_http.py`
- `M` `edu_agent/app/pdf_question_crops/service.py`
- `M` `edu_agent/app/question_ingestion/layout_elements.py`
- `M` `edu_agent/app/question_ingestion/ocr_auxiliary_layout.py`
- `M` `edu_agent/app/question_ingestion/stage_descriptors.py`
- `M` `tests/unit/test_deployment_model_boundaries.py`
- `M` `tests/unit/test_external_http_provider.py`
- `M` `tests/unit/test_model_registry.py`
- `M` `tests/unit/test_question_ingestion_eval_runner.py`
- `M` `tests/unit/test_question_ingestion_ocr_auxiliary_layout.py`
- `M` `tests/unit/test_question_ingestion_stage_descriptors.py`
- `M` `tests/unit/test_run_question_ingestion_priority_quality_gate.py`
- `??` `scripts/serve_paddleocr_vl_layout_sidecar.py`
- `??` `tests/unit/test_paddleocr_vl_layout_sidecar.py`

## 证据来源

- 项目进展巡检: `85_运行记录/项目进展巡检-edu-agent-2026-06-18-2034.md`
- 建议分析: `85_运行记录/建议分析-2026-06-18-2034.md`
- 项目文件: `10_项目/edu-agent/任务清单.md`
- 项目文件: `10_项目/edu-agent/决策记录.md`
- 项目文件: `10_项目/edu-agent/问题清单.md`
- 项目文件: `10_项目/edu-agent/项目上下文.md`
- 项目文件: `10_项目/edu-agent/项目总览.md`
- 项目文件: `10_项目/edu-agent/项目进展.md`
- 项目文件: `10_项目/edu-agent/风险清单.md`
