---
类别: 运行记录
记录类型: 代码质量审视
项目: edu-agent
项目键: edu-agent
生成时间: 2026-06-19 01:07:54 +0800
审视模型建议: gpt-5.4 / xhigh
质量等级: 红色
建议动作: 后端变更至少运行相关单测或接口 smoke；前端变更至少运行 lint/build 或目标页面 smoke。
---

# edu-agent 代码质量审视

## 总览

- 质量等级：红色
- 结论：先停下来做质量门禁和拆批，再继续新增功能。
- 近期 commit：20
- 工作区路径：16
- Diff 行数：382+ / 34-
- 自动修复：否
- 自动提交：否

## 质量结论

- 先停下来做质量门禁和拆批，再继续新增功能。

## 主要风险

- 变更范围中等：16 个路径，382+/34- 行，需要明确提交边界。
- 配置或环境相关文件有变化，需要确认没有真实凭证、生产地址或本地私有路径。
- 新增 diff 出现 AI 代码味道或提交 hygiene 风险，需要人工复核。

## AI 代码味道

- `configs/model_registry.test-vision.yaml` 新增本机绝对路径，可能降低可移植性。
- `configs/model_registry.yaml` 新增本机绝对路径，可能降低可移植性。
- `tests/unit/test_model_registry.py` 新增本机绝对路径，可能降低可移植性。

## 必要验证

- 后端变更至少运行相关单测或接口 smoke；前端变更至少运行 lint/build 或目标页面 smoke。

## 提交前门禁

- 配置变更必须单独 review，并确认 `.env`、token、内网地址没有进入提交。
- 接口契约、存储路径、模型/OCR 调用和前端展示必须有一条可复核验证证据。

## 建议分批

- 当前无需强制拆批；如果准备提交，仍先确认代码、文档、运行状态边界。

## 需要确认

- 代码变更是否回应了最新决策审视，而不是绕开它：`85_运行记录/决策审视-edu-agent-2026-06-19-0107.md`

## 变更分类

- 测试：8 项
- 后端代码：5 项
- 配置：2 项
- 脚本：1 项

## Diff 统计

- `tests/unit/test_external_http_provider.py`：82+ / 0-
- `edu_agent/app/models/providers/external_http.py`：34+ / 21-
- `configs/model_registry.test-vision.yaml`：48+ / 0-
- `tests/unit/test_question_ingestion_ocr_auxiliary_layout.py`：44+ / 0-
- `configs/model_registry.yaml`：33+ / 6-
- `tests/unit/test_model_registry.py`：37+ / 0-
- `tests/unit/test_run_question_ingestion_priority_quality_gate.py`：30+ / 4-
- `edu_agent/app/question_ingestion/stage_descriptors.py`：19+ / 0-
- `edu_agent/app/question_ingestion/layout_elements.py`：14+ / 0-
- `tests/unit/test_deployment_model_boundaries.py`：14+ / 0-
- `tests/unit/test_question_ingestion_stage_descriptors.py`：11+ / 0-
- `edu_agent/app/pdf_question_crops/service.py`：6+ / 2-
- `edu_agent/app/question_ingestion/ocr_auxiliary_layout.py`：6+ / 1-
- `tests/unit/test_question_ingestion_eval_runner.py`：4+ / 0-

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

## 证据来源

- 代码仓库：`/Users/linsuchang/Desktop/work/edu-agent`
- 决策审视：`85_运行记录/决策审视-edu-agent-2026-06-19-0107.md`
