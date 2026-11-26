# v0.33.1 Phase 0：当前系统架构图（基于 main 3734997）

本文件只描述当前代码事实，不主张尚未实现的能力。行号为审计时的实际位置，后续阶段如发生位移以函数名为准。

## 1. 生成、章节规划与 Worker 执行链

1. API 入口：`bidcraft/api/routes/projects.py:2302 generate_all()`（`POST /projects/{ref}/generate`）。同步章节入口为同文件 `generate_chapter()` / `write()`。
2. 规划门禁：`projects.py:267 _chapter_plan_gate()` 根据 `review` / `review_strict` / `auto` 检查 confirmed plan，状态或依赖失效返回 409；`projects.py:232 _plan_metadata()` 把规划 ID 与引用冻结进 Job payload。
3. 入队：`projects.py:2350 acquire_generation_lease()`、`:2358` 扣费、`:2363 begin_pending_charge()`、`:2371-2400` 创建含 `plans` / `planning_ids` / `generation_lease_token` / `pending_charge_id` 的 Job、`:2416 jobqueue.submit()`。
4. 章节规划 HTTP：`projects.py:1527` 查询、`:1544 _generate_chapter_plan()`、`:1633/:1642/:1651` 创建/生成/重生成、`:1661` 修改、`:1707` 确认、`:1740` 历史。
5. 规划引擎：`bidcraft/generation/chapter_plan.py:439 build_plan_context()`、`:574 _deterministic_plan()`、`:686 _model_plan_output()`、`:926 validate_chapter_plan()`、`:812 plan_dependency_staleness()`、`:910 invalidate_stale_plan()`、`:1068 generate_chapter_plan()`、`:1199 confirm_chapter_plan()`、`:1230 mark_plan_used()`。检索引用由 `:205 normalize_retrieval()`、`:238 scoped_knowledge_provider()` 注入，写入时由 `bidcraft/generation/writer.py:582-591` 消费。
6. Worker：`bidcraft/jobqueue.py:1613 _execute()`、`:1694 _worker()`、`:1710 _reaper_loop()`；runner registry `:363-372`。`bidcraft/workers/generation.py:192 _run_generation_job()` 使用 `:82 _resolve_worker_plan()`、`:204 fenced_job()`、`:232 pending_charge_heartbeat()`、`:399 generate_document()` 及 `before_chapter` / `after_chapter` / `commit_chapter` / `fail_chapter` 钩子。
7. 章节短事务：`workers/generation.py:115 commit_generation_chapter()` 经 `jobqueue.fenced_chapter_write()` 在一个短事务内写 Section、OutlineNode、事实引用、plan-used、`done_steps` 与 `chapter_committed` 事件。最终化 `:423 commit_fenced_result()` → `settle_pending_charge()` / `_job_refund()` → `:458` 释放 GenerationLease；`JobAborted` 保留 running 供 reaper 接管。
8. 生成内核：`bidcraft/generation/writer.py:469 generate_document()`、`:390 write_chapter()`、`:59 verify_chapter()`、`:314 unused_plan_refs()`；工具循环 `bidcraft/generation/tool_loop.py:326`，错误类型 `:71/:75/:79`。

## 2. Fencing、租约与账务一致性

- Claim：`jobqueue.py:529 _claim_statement()`（仅 PG 使用 `FOR UPDATE SKIP LOCKED`）、`:566 claim_next()`。
- 写栅栏：`:88 fenced_job()`、`:187 assert_claim_current()`（独立会话避免 identity map 陈旧）、`:208 commit_fenced_result()`、`:256 fenced_chapter_write()`、`:310 step_status()`、`:326 mark_step_done()`。
- 租约与心跳：`:916 acquire_generation_lease()`、`:1011 assert_generation_lease()`、`:594 _renew_lease_once()`、`:620 _heartbeat()`（3 次瞬时失败容忍）、`:824 reap_expired()`。
- 账务：`:1031 begin_pending_charge()`、`:1098 pending_charge_heartbeat()`、`:1402 settle_pending_charge()`、`:1490 reconcile_pending_charges()`；RC-05 的单一归属由 `mark_job_refund_settled()` / `clear_job_marker()` 保证。
- 回归证据：`tests/test_v0330_generation_concurrency.py`、`tests/postgres/test_v0330_migration_concurrency.py:41-66`、`tests/test_v0312_generation_dual_fencing.py`。

## 3. 知识资产、检索与向量就绪

- Passage / 诊断：`bidcraft/knowledge.py:65 Passage`、`:95 SearchResult.diagnostics`，包含 document/page/heading/chunk 溯源、freshness/readiness 与 trust 信息。
- 向量统计：`:121 _vector_fields()`、`:168 kb_payload()`。当前 `total = kb.chunk_count`（`:122`），已嵌入按 `embed_key` 计数（`:135-142`）。
- 发布门禁：`:786 releasable_review_filter()` 仅允许 NULL/空/`published`；被 `build_vectors()`（`:797-823`）、`search_local()`、企业匹配 `bidcraft/enterprise/matching.py:193,209`、删除/查重清理（`:512,544`）复用。
- 检索合并：`:671 rerank_hybrid()`、`:906 search_local()`、`:1102 search_api()`、`:1194 search()`。
- 资产生命周期：`bidcraft/knowledge_assets.py:196 asset_payload()`、`:260 propose_document_assets()`、`:290 review_asset()`、`:426 find_duplicate_candidates()`、`:491 resolve_duplicate_group()`；API 位于 admin `:407-656`、enterprise `:191-327`，重建索引 worker 为 `bidcraft/workers/knowledge.py:27`。
- 已确认缺口：向量统计口径把不可发布块也计入总数，导致 UI `web/src/lib/format.ts:134-146` 可能显示“向量待更新”，不等于真实嵌入失败。

## 4. 审批链与可解释性

- 核心规则：`bidcraft/boq.py:713-862` 常量与归一化；`:913 _stage_explain()`、`:1004 _stage_applies()`、`:1029 _chain_explain()`、`:1059 applicable_stages()`、`:1101 completed_stages()`、`:1163 version_payload()`（`:1213 approval_explain`）。
- 待办入口：`:1509 pending_for_user()`；当前在 `:1588` 仅当 chain 含 `rules` 才输出 `approval_explain`，`:1624` 写入字段。商务标 `bidcraft/commercial.py:1515 pending`、`:1580` 同样门控，`:1622` 写字段。
- API：`bidcraft/api/routes/approvals.py:15 approvals_pending()` 合并 BOQ + commercial；工作流审批为独立链 `bidcraft/orchestration/approval.py:79,129,197,251` / `api/routes/orchestration.py:295,311`。
- 前端：`web/src/lib/types.ts:315-329,358,1528`、`web/src/api/project.ts:349 normalizeApprovalExplain()` 与 `:392 explainApprovalStages()`、`web/src/api/boq.ts:127`、`web/src/pages/tabs/BoqVersions.tsx:189 ApprovalExplainPanel` 与 `:1702-1750`。
- 已确认缺口：普通条件链在 pending inbox 无 explain；`web/src/api/shared.ts:497-526` 的 pending normalizer 尚未透传 explain，`PendingApprovals` 不渲染。Phase 1 需后端去门控、API/前端透传、精确键测试同步更新。

## 5. 质量评估、学习治理与真实件门禁

- 报告聚合：`bidcraft/evaluation/report.py:127 build_quality_report()`，summary 在 `:216-224` 组装，`:275-282` 追加 `metrics` / `tender_quality` / `retrieval_benchmark` / `calibration`，`:315 report_payload()`。
- 未测量契约：`bidcraft/evaluation/quality_metrics.py:305 collect_quality_metrics()` 每指标 `measured`；Markdown 见 `api/routes/evaluation.py:79`，summary 见 `:311`；前端 `web/src/api/evaluation.ts` 与 `web/src/pages/tabs/QualityTab.tsx` 对齐。
- 检索基准：`bidcraft/evaluation/retrieval_metrics.py:35-53` 九个指标键，`:142 evaluate_benchmark()`、`:192 load_benchmark()`、`:230 retrieval_status()`、`:252 collect_retrieval_metrics()`；数据根 `tests/fixtures/retrieval_real`。
- 专家校准：`bidcraft/evaluation/quality_calibration.py:25-39`、`:174 load_ratings()`、`:198 expert_status()`、`:218 collect_quality_calibration()`；数据根 `tests/fixtures/expert_ratings_real`。
- 真实招标件：`bidcraft/evaluation/tender_dataset.py:18-23` 常量、`:33` 全文合成标记扫描、`:50 load_dataset()`（`:78-82` 强制 `source == "user-provided"`）、`:131 dataset_status()`；`tests/test_v0330_dataset_gate.py:127-134` 数据不足时显式 DATA BLOCKED skip。
- 经验学习：`bidcraft/evaluation/learning.py:138 normalize_lesson_policy()`、`:197 load_lesson_policy()`、`:234` 来源 ID、`:254 revoke_lesson()`、`:399 lesson_items()`；路由 `api/routes/projects.py:1357,1395`。
- 当前限制：三个真实数据目录只有 README 与示例 manifest；真实模型 / function-calling、生产等价 PG/PgBouncer 均未验证。

## 6. API、应用与前端集成

- `bidcraft/api/app.py:395-437 create_app()`（lifespan `:422`），router 注册 `:748-765`，内置 SPA `bidcraft/web_dist` `:316`。
- HTTP 客户端：`web/src/lib/http.ts:227 apiFetch()`、`:323 requestBlob()`、`:380 uploadForm()`；路由 `web/src/App.tsx:87 createBrowserRouter`；任务进度 `web/src/hooks/watchJob.ts:63,135` 使用 SSE 并轮询回退。
- API 模块与后端域一一对应：generation、jobs、project、boq、commercial、evaluation、knowledge、knowledgeAssets、retrieval、learning、enterprise、orchestration。
- 当前限制：pending inbox 的 explain 透传不完整；前端仍保留本地 `_stage_explain` 镜像，Phase 1 应优先以后端真实 explain 为准。

## 7. CI 覆盖边界

- 9 个 CI job；PG job `.github/workflows/ci.yml:493-518` 使用 `BIDCRAFT_TEST_PG_URL`，执行 `tests/postgres` 与选定根目录测试，断言 `tests>=900`、`skipped==0`、`failures==0`。
- PG job 未覆盖 `tests/test_v0330_generation_concurrency.py`、`tests/test_v0312_generation_dual_fencing.py` 及 v0.33 tool-loop / worker-matrix 根目录套件；这些在 SQLite 下运行。无 PgBouncer / 生产权限 / 多机覆盖。
- 真实招标件状态步骤非门禁；没有 `retrieval_status()` / `expert_status()` 的 CI 状态步骤。
