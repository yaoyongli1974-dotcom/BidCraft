# v0.32 Phase 6 质量评估闭环报告

## 1. 范围与基线

- 分支：`feature/v0.32-ai-engine`
- 基线：`774064d`（Phase 5）
- 目标：质量评估必须使用真实 AI 引擎证据（规划确认、检索可信追踪、事实确认、工具审计、人工修改），并保持既有风险否决与导出门禁不被绕过；补齐质量报告导出、跨项目汇总和前端证据展示。
- 不改动：`export_gate.py` 逻辑、门禁阈值、历史迁移、既有导出确认规则。

## 2. 实现证据

| 编号 | 项 | 实现 | 证据 |
|---|---|---|---|
| 1 | 规划确认覆盖 | NEW `coverage.calculate_plan_coverage(db, project_id, nodes=None)`：只看真实 CONFIRMED/USED_FOR_GENERATION 且有正文的 ChapterPlan；返回 total/count/confirmed/covered/unconfirmed/ratio/measured/plans；零分母 `ratio=None/measured=False` | `tests/test_v032_quality_evaluation_engine.py` |
| 2 | 评分接入 | `scorer.py` 的 technical = evidence45/chapter35/plan20，delivery = chapter60/plan40，仅 `plan.measured` 时启用；权重不变，零分母不记满分 | `test_plan_coverage_feeds_scoring_only_when_measured` |
| 3 | 检索可信覆盖 | evidence coverage 新增 plan_refs/degraded/low_trust/missing_trace 比率、real confirmed facts、fact_confirmations（确认人/时间）；finding stable_key `chapter_plan:<id>:ref:<id>` | `test_evidence_trust_and_fact_findings` |
| 4 | 工具审计评估 | `risk.collect_tool_audit_failures` 从真实 AuditLog `agent.tool.call` 的 isError/ok/error 文本判定失败；finding stable_key `audit:<id>`；无新表 | `test_tool_audit_failures_become_findings` |
| 5 | 风险口径统一 | `collect_project_risks` 复用 `export_gate.issue_is_open`，`in_progress` 不再消失；新增 stale_evidence/stale_sections/tool_failures/pending_chapter_rows；风险摘要含 export_blockers/export_blocked | `test_risk_uses_export_gate_open_predicate` |
| 6 | 无法导出不得 PASS | scorer 在 blocking substantive 时 veto，pending chapters/stale evidence 时至少 warn；导出门禁代码未改 | `test_export_blockers_downgrade_verdict` |
| 7 | 处置溯源 | report 写 handled/resolved/ignored 时 stamp resolved_at；reopen 清空 resolution_note/resolved_by/resolved_at/disposition_from_finding_id；finding stable_key 从真实 Issue/Requirement/Plan/Fact/Audit ID 派生 | `tests/test_batch7_quality.py` 扩展 |
| 8 | 指标去重复 | `quality_metrics.issue_density` 按 evidence.stable_key/fingerprint 去重，最新版本状态胜出；无 stable key 的旧 findings 保留逐行口径，notes 更新 | `tests/test_v030_stage6_metrics.py` 扩展 |
| 9 | 报告导出 | NEW `GET /projects/{ref}/evaluation/reports/{report_id}/export`：viewer 安全，Markdown 附件含报告元数据、维度表、findings 源码锚点/evidence、coverage/metrics/lessons 摘要；跨项目 404，无 force 绕过 | `tests/test_v032_quality_api.py` |
| 10 | 汇总视图 | NEW `GET /projects/{ref}/evaluation/summary`（可选 `?refs=`）：只聚合当前用户可访问项目；无报告项目跳过不伪造分数；measured 字段明确 | `test_summary_is_scoped_and_does_not_fabricate` |
| 11 | 忽略原因校验 | Finding PATCH `status='ignored'` 空 note → 400（在 IDOR 检查之后、update_finding 之前）；审计仍写入真实项目 | `test_ignored_requires_note_and_audits` |
| 12 | 前端质量闭环 | `api/evaluation.ts` 新增 detail/export/summary 客户端，null-safe 规范化；QualityTab 历史下载、异步按 `job.result.report_id` 取详情、FindingCard 显示真实 source/evidence/resolved_at、指标分子分母样本明细；CheckTab 本地记录不再显示绿色通过 | web `57 files / 431 tests`、typecheck/lint/build |
| 13 | 不伪造状态 | grade/verdict 缺失返回 null/'—'，不再默认 C/warn；coverage/samples/mismatches 保留 null/0 与 measured 语义；web outbound/apiSurface 契约同步 | `outboundContract.test.ts`、`apiSurface.test.ts` |
| 14 | 路由计数 | 252→272（前五阶段）→ **274**（质量导出 + 汇总）；schema 仍 93；计数测试与 docstring 同步 | `tests/test_api_package.py`、`tests/test_v030_stage8_observability.py` |

## 3. 关键文件

- 评估引擎：`bidcraft/evaluation/{coverage,risk,scorer,report,quality_metrics,__init__}.py`
- API：`bidcraft/api/routes/evaluation.py`、`tests/test_v032_quality_api.py`
- 前端：`web/src/api/evaluation.ts`、`web/src/pages/tabs/{QualityTab,CheckTab}.tsx`、`web/src/lib/types.ts`

## 4. 权限与门禁

- 新增导出/汇总均为项目下 viewer-safe GET；`?refs=` 仅聚合当前用户有权访问的项目。
- 所有 finding 更新继续通过 `finding_for_project`（报告→项目归属）防止 IDOR，审计写到真实项目。
- `export_gate.py` 未被修改；质量结论只读取/汇总导出阻塞信号，不能替代或绕过门禁。
- 工具审计与事实确认均来自真实 AuditLog/Fact 字段，不把 model/tool 自我声明当作 verified。

## 5. 验证

- `ruff check bidcraft tests scripts`：All checks passed；`git diff --check`：通过
- 后端目标测试（引擎 + API + details + metrics + batch7 + export gate + eval regression）：**71 passed, 1 warning**（仅 Starlette/httpx deprecation）
- Agent 分层验证：engine 98 passed；API 22 passed（含回归 38 passed）
- Web：typecheck ✓、lint ✓、`npm test -- --run` → **57 files / 431 tests passed**、build ✓（1720 modules）
- 路由运行时计数：274；schema 93；Alembic 单 head 0015
- 全量后端 pytest：`.venv/bin/python -m pytest -o addopts="" -q` → **2257 passed, 886 skipped, 1 warning in 1779.18s**，零失败（仅 Starlette/httpx deprecation）。

## 6. 残留风险

- 工具失败判定依赖 AuditLog payload 的启发式字段（isError/ok/error 文本）；结构化 outcome 需后续 schema，现未新增。
- `chapter.reject` 仍无生产事件发出端；已能被学习与风险链路读取。
- `getEvaluationSummary` 前端未单独挂新视图；API 与客户端已具备，质量页仍以单项目详情为主。
- 零分母 coverage 变为 `measured=False`，旧的“无数据=100%”展示已修正；需下游 UI 正确显示 `—/未测量`。
- PG 真实迁移/并发路径与真实模型/Dify/RAGFlow/标注真件仍属 Phase 7 验收范围。

## 7. 回滚

1. 代码：revert `bidcraft/evaluation/*.py`、`bidcraft/api/routes/evaluation.py` 及 4 个计数/契约测试；删除 `tests/test_v032_quality_evaluation_engine.py`、`tests/test_v032_quality_api.py`、`tests/test_v032_quality_details.py`。
2. 前端：revert `web/src/`，重建 `bidcraft/web_dist`。
3. 路由计数 274→272；schema 93 不变；无迁移/表变化，无需 Alembic downgrade。
