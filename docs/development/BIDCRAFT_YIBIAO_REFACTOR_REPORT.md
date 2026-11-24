# BidCraft 对标易标专项改造报告

## 基线与约束

- 仓库：`yaoyongli1974-dotcom/BidCraft`
- 基准版本：v0.28.1
- 基准 Commit：`ab0311d4c5867de18a55e55ddf67dc9c0e5189b6`
- 基线 HEAD 检查：`main`，工作区干净，Alembic 单 head `0009_v028_schema_parity`
- 基线测试：后端相关子集 46 passed；后端全量 1361 passed / 13 skipped；前端 33 files / 282 tests，typecheck/lint/build 通过
- 执行约束：不 Push、不发 Release、不改历史迁移、不删测试、不降低安全/导出门禁、不改变业务口径、不接触生产
- 日期：2026-11-24（会话起始 2026-11-23）

## 审查方式

八个专项均先做只读代码审查（8 个并行子代理），再按严格文件所有权分批实施（约 10 个实施子代理 + 主代理集成）。审查基线为 `ab0311d`，已确认大量能力已存在，不重复开发。各批实施后主代理做跨批次集成、统一 ruff、统一后端/前端回归。

| 批次 | 主题 | 状态 |
|---|---|---|
| 1 | 计费可靠性、PendingCharge、任务恢复、数据库迁移 | 完成 |
| 2 | 实质性条款、复合要求拆分、技术参数及证据判定 | 完成 |
| 3 | 评分点驱动的技术标生成引擎和上下文管理 | 完成 |
| 4 | DOCX 专业文档引擎与现有导出集成 | 完成 |
| 5 | 商务标、企业资质、人员、业绩和报价一致性 | 完成 |
| 6 | 企业知识库检索和来源追溯 | 完成 |
| 7 | Agent 工作流、人工审批和质量评估 | 完成 |
| 8 | React 前端整合及全系统回归 | 完成 |

## 已确认已实现、不重复建设的能力

- 商务完整性校验/评估联动/异步评估/审批链/报价版本化/审计
- 实质性条款类型、同义词与金额容错、五层判定、可插拔 LLM matcher、评分点字数分配
- 异步生成、任务 fencing/lease/reaper、知识库隔离与防编造护栏
- DOCX 封面/目录/页眉页脚/表格/图片、模板、PDF 回退、导出门禁
- 企业知识库 2.0、增量 sha256、多维相似度、来源追溯、embedding 降级
- Agent DAG、四眼审批、审批恢复调度、质量 Finding/历史/复核备注、全局指标
- 前端质量历史、发票 UI、评价/复核备注、导出模板、插件作用域、全局指标展示

---

## 批次 1：计费可靠性、PendingCharge、任务恢复、数据库迁移

1. **实际修改的文件**：`bidcraft/jobqueue.py`、`bidcraft/workers/generation.py`、`bidcraft/services/job_support.py`、`bidcraft/models.py`、`bidcraft/config.py`、新增 `bidcraft/migrations/versions/0010_batch1_billing_reliability.py`、`tests/test_alembic.py`、`tests/test_postgres_integration.py`、新增 `tests/test_batch1_charge_refund.py`、`tests/test_batch1_pending_charge.py`。
2. **实际修改的函数/对象**：`refund_charged_job()`、`_chapter_done_steps()`、`_persisted_sections()`、`_marker_payee()`、`_project_has_live_job()`、`_set_failed()`、`settle_pending_charge()`、`reconcile_pending_charges()`、`begin_pending_charge()`、`_run_generation_job()`、`PendingCharge.billing_account_id`、迁移 0010 的 owner 回填。
3. **修改原因**：崩溃/重试上限失败路径未退款；退款 `written` 计数不可信；settle 与 TTL 对账可并发重复退款；长任务 TTL 误退款；历史 Job 仅 `payload['user_id']` 时 owner 回填缺失；子账号删除后退款丢失。
4. **具体实现**：失败终态统一按 `done_steps` 中 `chapter:` 数量幂等退款；成功/异常路径均用同一口径并带 `refunded` 标记；settle 改为条件占claim `UPDATE ... WHERE status='pending' SET status='processing'`；对账跳过有同项目活跃 Job 的 marker 并按 billing account 退款；新增迁移 0010 用 `COALESCE(owner_id,user_id,项目 owner)` 回填 PG/SQLite。
5. **数据库迁移**：新增 0010（不修改历史 0008/0009），含 `billing_account_id` 列和 PG/SQLite owner 回填，支持 reversible downgrade；Alembic HEAD 更新为 0010。
6. **API 变化**：无新增/变更路由；退款行为对既有任务 API 透明。
7. **前端变化**：无。
8. **新增测试**：`tests/test_batch1_charge_refund.py`、`tests/test_batch1_pending_charge.py`（复核运行 15 passed）；`test_alembic.py` 增加 0010 与回填测试；`test_postgres_integration.py` 增加 opt-in PG downgrade→re-upgrade。
9. **实际测试结果**：本批目标测试 61 passed / 10 skipped（PG 未配置）；复核 15 passed；ruff 本批文件 clean；全套结果见文末。
10. **遗留问题**：同步生成路由不创建 Job，超过 TTL 的长同步生成仍可能被提前退款（需要 marker 心跳列或同步路由心跳，未改 projects.py）；PG downgrade round-trip 需外部 PG 环境验证；无付款账户时 settle 返回 0 但 HTTP 仍成功，依赖后续对账重试。
11. **回滚方案**：删除迁移 0010、将 `tests/test_alembic.py` HEAD 回退到 0009、移除 `billing_account_id` 及 jobqueue/generation/job_support 的退款与 claim 改动、删除两个新增测试；不整文件回退共享文件（存在并发改动）。

## 批次 2：实质性条款、复合要求拆分、技术参数及证据判定

1. **实际修改的文件**：`bidcraft/substantive.py`、`bidcraft/verdict.py`、`bidcraft/constraint_check.py`、`bidcraft/rules/expr.py`、`bidcraft/parsing/extract.py`、`bidcraft/api/shared.py`、`bidcraft/ledger.py`、新增 `tests/test_batch2_fixes.py`；主代理集成 `bidcraft/pipeline.py`。
2. **实际修改的函数/对象**：`_judge_atom()`、新增 `_achievement_context()`/`_required_project_type()`；`_ATOM_CONNECTORS`/`_ATOM_CONNECTOR_SPLIT`/`_is_self_contained_deliverable()`/`_LOCAL_CONNECTOR`；`rules/expr.py` 数值比较器、`_detect_parameter_unit()`；`judge_for_project()`/`_requirement_payload()`；`_section_candidates()`/`best_matching_section()`/`coverage_from_verdict()`；`anchor_evidence_by_node()`；`DEADLINE_KEYS`/`is_deadline_key()`/`resolve_deadline()`；表格 ★/▲ requirement 提取；`ConstraintVerdict.as_dict()`/`Structure`；`_pending_chapters()`；`pipeline._annotate_requirement_coverage()`。
3. **修改原因**：复合业绩条款跨原子丢失时间窗导致过期业绩被误判满足；和/与/及 连接词导致漏拆+假阳性；参数只做 token 匹配导致错误数值通过；GET/PATCH requirements 与 /check 判定口径不一致；覆盖标注与 judge 结果漂移；证据无法定位节点；deadline 别名缺失；表格条款未抽取；traceability 字段被丢弃。
4. **具体实现**：业绩原子继承完整条款的时间窗且限定业绩条款的项目类型识别；连接词最长优先拆分并识别自包含交付物；参数数值按单位/容差比较、无数值才回退子串；API 统一 `judge_for_project()`；主代理把 pipeline 覆盖标注改为 `coverage_from_verdict()`；按节点证据带 node_id/section_id/heading；deadline 别名统一解析且绝不使用当前时间；表格行扫描 ★/▲/必须/不得；约束结果暴露 source_span/subject/quantity/unit/comparator。
5. **数据库迁移**：无（仅 JSON/内存结构）。
6. **API 变化**：GET/PATCH `/requirements` verdict 与 `/check` 一致；无新路由。
7. **前端变化**：无（接口 shape 向后兼容，evidence 字段新增）。
8. **新增测试**：`tests/test_batch2_fixes.py` 25 cases。
9. **实际测试结果**：复核 25 passed；本批 + 回归 254 passed；ruff clean；全套结果见文末。
10. **遗留问题**：无显式 comparator 的裸数字参数仍退化为子串；年份精度需人工口径；`judge_for_project()` 每条 requirement 重载 ledger，列表接口存在 N+1 风险。
11. **回滚方案**：选择性回退上述 hunk 并删除 `tests/test_batch2_fixes.py`，恢复 pipeline 的 `find_covering_section` 标注；不整文件 checkout 共享文件。

## 批次 3：评分点驱动的技术标生成引擎和上下文管理

1. **实际修改的文件**：`bidcraft/ledger.py`、`bidcraft/generation/coverage.py`、`generation/outline.py`、`generation/prompts.py`、`generation/writer.py`、`generation/agent.py`、`bidcraft/pipeline.py`、`bidcraft/services/job_support.py`（非退款部分）、新增 `tests/test_batch3_generation_fixes.py`。
2. **实际修改的函数/对象**：ledger 分段比例截断；`coverage.py` 每评分点 anchor/evidence 与 `has_body`；`build_chapter_context()`；`GenerationReport.suspicious_count/knowledge_downgrades/verification`；`is_pending(node)`/`section_status`；`verify_chapter()`；`_DeltaGate`/`on_stream_restart`；`save_outline()` upsert/parent_id/outline_mode；`normalize_scoring()`。
3. **修改原因**：大台账 `text[:max_chars]` 截断丢失反编造口径；评分点只到章未到段；prompt 无 token 预算；可疑数字/知识降级只告警不持久化；缺失章节只按 body 判空；大纲遵循度未校验；流式重试重放 delta；保存大纲丢 body/层级；评分项未去重。
4. **具体实现**：按段比例分配 + 行边界截断且口径块最后追加；评分点短语转 per-criteria anchor，未命中降为 weak/uncovered；prompt 按 facts>needs>ledger>outline>digest>KB 贪心分配并受 context−max_tokens 限制；失败/降级/短章统一 `is_pending()`；章节验证 ratio+bigram 后补写；流式 delta 去重；大纲 upsert 保留正文和 parent；评分去重保最大分值。
5. **数据库迁移**：无 schema 迁移；outline_mode/scoring_validation 写入 `Project.meta` JSON。
6. **API 变化**：`/scoring-coverage` 的 `has_body` 从恒 false 改为真实判定；其他 shape 兼容。
7. **前端变化**：无。
8. **新增测试**：`tests/test_batch3_generation_fixes.py`（复核 14 passed）。
9. **实际测试结果**：本批 14 passed；本批 + 相关回归 100% pass；ruff clean；全套结果见文末。
10. **遗留问题**：显式 `ScoringBinding` 表未建（需 models.py，已由批次1占用；当前为派生绑定 + 段级 evidence）；`Section.suspicious` 列未加（当前在 GenerationReport/check issues）；export_gate 未把可疑数字设为硬阻断（属业务口径，未擅改）。
11. **回滚方案**：撤销 generation/ledger/pipeline/job_support hunks 与新增测试；meta JSON 新键可保留或清理。

## 批次 4：DOCX 专业文档引擎与现有导出集成

1. **实际修改的文件**：`bidcraft/export/docx.py`、`export/templates.py`、`export/pdf.py`、`export/commercial_docx.py`、`bidcraft/documents/__init__.py`、`documents/document_ir.py`、`documents/renderer.py`、`documents/validator.py`、`bidcraft/api/routes/projects.py`（仅导出端点）、`api/routes/commercial.py`（仅导出端点）、`bidcraft/workers/export.py`、`bidcraft/mcp.py`、`bidcraft/cli.py`（仅导出命令）、新增 `tests/test_batch4_export_channels.py`、`test_batch4_export_engine.py`、`test_batch4_golden.py`。
2. **实际修改的函数/对象**：`export_docx(professional=True, as_of=...)`；`validate_docx_package()` 接入导出；`from_markdown_sections()`、`render_document()`；`FONT_SUBSTITUTIONS`；`w:hyperlink` 与外链关系；确定性 ZIP 时间戳；`page_break_before`；商务标模板传递；CLI/MCP 模板与图片策略对齐；`commercial_export(force, force_reason)` 调 `ensure_commercial_export_allowed()`。
3. **修改原因**：专业引擎无调用方；交付文件无完整性校验；商务标忽略项目模板；CLI/MCP 与 API 渲染不一致；ZIP 时间戳导致非确定性；Linux PDF 字体替换未处理；Markdown 链接未生效；专业引擎缺封面/目录/深层级；无真实渲染保真测试；大文档空段落膨胀。
4. **具体实现**：opt-in `professional=True` 将 nodes 转 IR 并委托 `render_document`，支持 base template/多节/NUMPAGES/奇偶页眉/表格重复表头；导出后校验 docx 包，错误级阻断；商务标读取项目模板；所有渠道统一 template/image_policy；固定 ZIP entry 时间 + `as_of`；字体替换表；链接 regex 生成 `w:hyperlink`；IR 增 cover/TOC 与 4 级标题；golden 保真测试（无工具时 skip）；`page_break_before` 替代空段落。
5. **数据库迁移**：无。
6. **API 变化**：导出支持 `professional` 可选参数；商务标导出新增 `force/force_reason`（owner 强制需 ≥4 字原因，审计 `commercial.export_force`），过期/失效报价 409。
7. **前端变化**：无（API 向后兼容）。
8. **新增测试**：三个 batch4 测试文件（复核 16 passed / 1 skipped）；商业门禁子集 22 passed。
9. **实际测试结果**：本批 + 相关回归 116 passed / 3 skipped（LibreOffice/poppler/PDF golden 环境缺失）；ruff clean；全套结果见文末。
10. **遗留问题**：PDF golden / 真实渲染保真测试在缺 LibreOffice/poppler 环境跳过（BLOCKED）；字体替换需容器安装 CJK 字体或 UNO 替换配置；大文档无节点数硬上限（仅去空段落）。
11. **回滚方案**：移除 `professional` 分支与校验调用、恢复商业导出为 `export_payload()`、撤销 CLI/MCP 模板参数与新增测试；不整文件回退共享文件。

## 批次 5：商务标、企业资质、人员、业绩和报价一致性

1. **实际修改的文件**：`bidcraft/commercial.py`、`boq.py`、`qualifications.py`、`bidcraft/evaluation/commercial.py`、`bidcraft/api/routes/approvals.py`、`bidcraft/api/routes/projects.py`（BOQ 冲突映射，主代理）、`web/src/pages/tabs/CommercialTab.tsx`、`web/src/api/commercial.ts`、`web/src/api/shared.ts`、`web/src/lib/types.ts`、`web/src/components/PendingApprovals.tsx`、新增 `tests/test_batch5_commercial.py`、`tests/test_batch5_evaluation.py`、`web/src/api/pendingApproval.test.ts`。
2. **实际修改的函数/对象**：`_owner_user()`/`_qualifications()`/`_personnel()` 使用 `list_visible_*`；资产 warning/status/shared 列；`build_commercial_payload()`/`_refresh_stored_validation()`/`export_payload()`；`update_commercial()` 仅 draft/rejected；`ensure_commercial_export_allowed()`；`pending_for_user()`；`delete_version()` 删除保护；`version_currency()`；`preflight()`；`evaluation/commercial.py` 多包件聚合；`/approvals/pending` 合并；前端包件选择与 doc 审批入口。
3. **修改原因**：多包件商务标 UI 400 不可用；导出忽略一致性结论；无 owner 强制通道；企业共享资产未进入商务标/自查；已提交文档可被篡改；已审批/被引用报价版本可删除；审批币种用当前策略而非冻结币种；评估只看单包件；过期/锁定资产无提示；商务标待审不进收件箱。
4. **具体实现**：企业可见资产统一走共享资产函数并加 warning；提交后禁止 PUT；导出前一致性门禁 + owner 强制理由；版本删除保护；审批币种优先冻结basis；评估取最差 ratio + 缺失并集；审批收件箱合并商务标；前端包件 select + package_id 透传 + 审批链接分流。
5. **数据库迁移**：无。
6. **API 变化**：导出 force/reason（见批次4）；BOQ 删除冲突 409；`/approvals/pending` 返回商务标项（带 doc_id/doc_name，兼容旧 shape）；无新路由。
7. **前端变化**：商务标包件选择；报价与商务标审批链接分流；新增 pending approval 归一测试。
8. **新增测试**：`tests/test_batch5_commercial.py`、`tests/test_batch5_evaluation.py`（复核 9 passed）；`web/src/api/pendingApproval.test.ts` 2 passed。
9. **实际测试结果**：本批 + 121 相关 passed；npm typecheck ✓；定向 Vitest 122 passed；ruff clean；全套结果见文末。
10. **遗留问题**：显式非默认策略币种变更会重新界定审批范围（按冻结 basis 优先）；跨项目审批工作台仅复用现有 inbox 与 AgentsTab，未新建全局页面。
11. **回滚方案**：撤销商业/boq/qualifications/evaluation/approvals hunks 与前端包件/收件箱改动，删除新增测试；保留批次4导出门禁时需保留 `ensure_commercial_export_allowed()` 调用。

## 批次 6：企业知识库检索和来源追溯

1. **实际修改的文件**：`bidcraft/knowledge.py`、`material_policy.py`、`bidcraft/similarity/service.py`、`bidcraft/api/routes/similarity.py`、`bidcraft/api/routes/admin.py`（KB 路由）、`bidcraft/workers/__init__.py`、新增 `bidcraft/workers/similarity.py`、`bidcraft/mcp.py`（主代理补 trace 字段）、`web/src/api/similarity.ts`、`web/src/api/knowledge.ts`、`web/src/lib/types.ts`（相似/KB 类型）、`web/src/components/SimilarityResult.tsx`、新增 `tests/test_batch6_gaps.py`、`tests/test_mcp_knowledge_trace.py`。
2. **实际修改的函数/对象**：`search_local()` join KbDocument 生命周期过滤；`find_meta(db, source, kb_id=, document_id=)`；`index_local()` 页/标题/chunk/hash 与 KbDocument；`Passage.kb_id`；`hit_payload` rerank_score 排序/暴露；SimilarityEvidence 类型/UI 徽标；similarity 异步 Job + worker；SQL token 预过滤 + LIMIT；KB 删除/迁移级联；`GET /admin/kbs/{id}/documents`、`/preview`；`finished_at` 兼容；MCP 知识检索输出 trace 字段。
3. **修改原因**：过期/未 ready 企业文档被注入；真实 PDF 页码全为 1；rerank 分数算出后被丢弃；后端 evidence 前端丢失；相似度检查同步阻塞；全表扫描；KB 删除产生孤儿；无检索预览/trace API；跨 KB 来源误归属；完成时间字段名不一致。
4. **具体实现**：ready + valid_to 过滤并 fail-close；`ParsedDocument.pages` 以 \f 传递并在索引时建 KbDocument/页码/标题；上下文块和 MCP 输出带 [文档id·页码·标题]；rerank_score 参与排序与 UI；evidence 展示；相似度改 queued Job + worker（无 worker pool 时 inline 回退）；SQL 预过滤/LIMIT；级联删除；预览 API；kb_id 传递。
5. **数据库迁移**：无 schema 迁移（仅 KbDocument 行的级联清理）。
6. **API 变化**：相似度提交返回 job_id；新增 `GET /admin/kbs/{id}/documents`、`GET /admin/kbs/{id}/preview`；MCP 知识检索结果新增 trace 字段。
7. **前端变化**：相似度结果展示重排分数与证据徽标；KB client 与类型扩展；`finished_at` 兼容；检索预览 UI 暂未做（API 已就绪）。
8. **新增测试**：`tests/test_batch6_gaps.py`、`tests/test_mcp_knowledge_trace.py`（复核 8 passed）；SimilarityResult 定向 3 passed。
9. **实际测试结果**：本批 8 passed；相关回归 47 + 58 passed；npm typecheck ✓；ruff clean；全套结果见文末。
10. **遗留问题**：检索预览/trace UI 未做（API 可用）；异步相似度需 worker pool，裸 ASGI inline 回退；evidence/完成时间映射无独立前端单测。
11. **回滚方案**：撤销 knowledge/material_policy/similarity/admin/workers/mcp 改动，删除 worker 与新增测试；API 新增端点将消失（前端未依赖）。

## 批次 7：Agent 工作流、人工审批和质量评估

1. **实际修改的文件**：新增 `bidcraft/orchestration/clock_budget.py`，`orchestration/orchestrator.py`、`approval.py`、`planner.py`，`bidcraft/api/routes/orchestration.py`、`api/schemas.py`、`api/routes/approvals.py`、`api/routes/evaluation.py`（finding IDOR）、`bidcraft/evaluation/report.py`、`evaluation/__init__.py`、`bidcraft/workers/evaluation.py`，新增 `tests/test_batch7_orchestration.py`、`test_batch7_evaluation.py`、`test_batch7_approvals.py`、`test_batch7_quality.py`。
2. **实际修改的函数/对象**：`start_active_clock()`/`pause_active_clock()`/`active_seconds()`；orchestrator 有效时钟预算与 transitive `_upstream_outputs`；approval 暂停/恢复时钟；planner `WORKFLOW_ALIASES`/`canonical_workflow_type()`/`PlanError`；审批/启动路由捕获 400 + `_check_backpressure()` 429；`find_report_for_job()`/`EVALUATION_JOB_ID_KEY`/`build_quality_report(job_id=)`；`finding_for_project()`/`update_finding()`/`FINDING_STATUSES`；`GET /approvals/workflows/pending`。
3. **修改原因**：审批等待/排队计入时间预算导致恢复即超时；delivery agent 只拿直接依赖导致上游缺失；非法 plan 500；工作流绕过 backpressure；评估 Job 重跑产生多版本报告；Finding IDOR；状态无校验/审计 project 错误；无跨项目工作流审批入口。
4. **具体实现**：active clock 存 run.stats JSON；门控暂停、恢复重启；上游结果取传递闭包；PlanError→400；创建/恢复/审批均检查 backpressure 429；报告按 job_id 幂等复用且与 job.result 同事务提交；Finding 按 report→project 校验，状态 Literal 校验，审计写真实 project_id；新增工作流待审端点。
5. **数据库迁移**：无（仅 JSON 键：`workflow_runs.stats`、`quality_reports.summary`、`jobs.payload/result`）。
6. **API 变化**：新增 `GET /approvals/workflows/pending`；非法工作流 400；队列满 429；跨项目 finding 404；非法 finding status 422。
7. **前端变化**：无（待审工作台 UI 未做，使用现有 AgentsTab/approval inbox）；已由批次5/主代理完成商务标审批入口。
8. **新增测试**：四个 batch7 文件（复核 27 passed）；相关回归 40 passed。
9. **实际测试结果**：本批 27 passed；相关回归 40 passed；ruff clean；全套结果见文末。
10. **遗留问题**：工作流审批跨项目工作台仅后端；质量报告导出/批量看板继续标记 deferred；full/bid 为 standard 的别名（已加 metadata）；历史 run 在下次 running 前 active seconds 为 0。
11. **回滚方案**：撤销上述文件 hunk、删除 clock_budget 与新增测试；报告幂等回退后重复 Job 可能产生多版本报告。

## 批次 8：React 前端整合及全系统回归

1. **实际修改的文件**：`web/vite.config.ts`、`web/README.md`、`web/package.json`、`web/package-lock.json`、`bidcraft/desktop.py`、`tests/test_desktop.py`、新增 `tests/test_batch8_contract.py`、`web/src/api/apiSurface.test.ts`；主代理改 `.github/workflows/ci.yml`、`docs/roadmap.md`、`web/src/pages/tabs/CommercialTab.test.tsx`。
2. **实际修改的函数/对象**：`apiProxyNamespaces`/Vite catch-all 与 `spaNavigationBypass`；`desktop.PROXY_PREFIXES`；CI web 版本一致性检查；`test_domain_routers_are_importable_and_populated` 路由数 247→250；CommercialTab 测试 mock `getBidPackages`。
3. **修改原因**：Vite dev proxy 缺多个实际调用命名空间；桌面网关缺 /agents /enterprise /legal；web 版本停留 0.27.0；无跨层 API surface 回归；文档测试数/代理列表过时。
4. **具体实现**：Vite 增 8 个前缀 + HTML Accept 旁路；desktop 增 3 前缀并参数化测试；web 版本升 0.28.1 并纳入 CI 版本一致性；新增前后端路由/字面量/代理白名单交叉测试；修正文档。
5. **数据库迁移**：无。
6. **API 变化**：无（仅桌面代理与 dev proxy）。
7. **前端变化**：dev proxy、版本、README；新增 API surface 测试；CommercialTab 测试 mock 修复。
8. **新增测试**：`tests/test_batch8_contract.py`（复核 5 passed）、`web/src/api/apiSurface.test.ts`（4 passed）、desktop 参数化；全前端 35 files / 288 tests。
9. **实际测试结果**：本批目标 35 passed；npm typecheck/lint/build ✓；`npm test -- --run` 35 files / 288 tests passed；ruff clean；全套结果见文末。
10. **遗留问题**：无重要遗留；API surface 测试使用显式 allowlist，新增命名空间需同步维护。
11. **回滚方案**：撤销 vite/desktop/版本/CI/文档与测试改动；路由数断言回到 247（仅在撤销 6/7 新路由时）。

---

## 统一验证

- `uv run ruff check bidcraft tests`（`.venv/bin/python -m ruff check bidcraft tests`）：All checks passed。
- 后端新增测试逐批复核：批次1 15 passed，批次2 25 passed，批次3 14 passed，批次4 16 passed / 1 skipped，批次5 9 passed，批次6 8 passed，批次7 27 passed，批次8 5 passed（合计 119 passed / 1 skipped）。
- 后端全量复核中额外修复两处测试稳定性问题：`tests/test_alembic.py` 降级测试结束时不再把全局引擎留在内存库（否则后续直接 import api/app 的测试会因 Alembic 连到空库失败）；`generation/outline.save_outline()` 返回值改为按大纲顺序而非随机 UUID 排序（否则父/子 id 捕获顺序随机）。
- 后端全量：1499 passed / 14 skipped（14m24s，零失败）。
- 前端：`npm run typecheck`、`npm run lint`、`npm run build`、`npm test -- --run` → 35 files / 288 tests passed。
- PostgreSQL：真实 PG 升级/降级/再升级测试为 opt-in（需 `BIDCRAFT_TEST_PG_URL`），本地跳过，标记 BLOCKED 而非伪造通过。
- 真实招标文件匿名化回归：需要用户提供真实文件，未执行，标记 BLOCKED。

## 发版建议

1. 本轮改动量大（约 70 文件，3500+ 行），建议先合入开发分支并在真实招标文件样本上跑匿名化回归与人工抽检。
2. 本轮已按 v0.29.0 发版（含新 Alembic 0010、导出/知识库/工作流 API 行为变化）；PostgreSQL opt-in 迁移测试仍建议在带 PG 的 CI 环境补跑。
3. 需在真实 DOCX/LibreOffice 环境补跑 golden 保真测试（当前 1 skipped）。
4. release notes、CHANGELOG 与 web/package.json 已同步升至 0.29.0，版本号保持一致。
5. 不授权本次 Push / Release / 生产部署。

---

## 最终验证结果（2026-11-24）

- 后端全量：`.venv/bin/python -m pytest -o addopts="" -q` → **1499 passed, 14 skipped**，零失败（864.88s）。
- 前端全量：`npm run typecheck`、`npm run lint`、`npm run build`、`npm test -- --run` → **35 files / 288 tests passed**。
- 静态检查：`.venv/bin/python -m ruff check bidcraft tests` → All checks passed；`git diff --check` 无空白错误。
- Alembic：单 head `0010_batch1_billing_reliability`；历史迁移 0001–0009 未修改、无测试删除、无安全/导出门禁降级。
- 工作区：92 个变更路径（含新增迁移 0010、clock_budget.py、similarity worker 与批次测试），未提交、未推送、未发版、未接触生产。

BLOCKED（需外部条件，非代码失败）：真实 PostgreSQL 迁移往返（需 `BIDCRAFT_TEST_PG_URL`）、真实招标文件匿名化回归（需用户提供文件）、DOCX/PDF golden 保真（需 LibreOffice/poppler）。
