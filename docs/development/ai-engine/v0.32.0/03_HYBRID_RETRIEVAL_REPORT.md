# v0.32 Phase 3 混合检索与溯源报告

## 1. 范围与基线

- 分支：`feature/v0.32-ai-engine`
- 基线：`bf0d1b9`（Phase 2）
- 目标：本地 bigram/cosine 与外部 Dify/RAGFlow 结果用 RRF 统一重排；检索结果带真实 KbDocument/KbChunk/page/heading 溯源和分级可信降级；项目/标段作用域内复用，规划与正文只能引用真实 ID；项目侧提供只读检索预览。
- 不改动：无新表/迁移，无历史迁移编辑，无导出门禁弱化，无 mock 充当真实 DB/门禁证据。

## 2. 实现证据

| 编号 | 项 | 实现 | 证据 |
|---|---|---|---|
| 1 | Passage 溯源扩展 | `knowledge.Passage` 增加 `chunk_id/chunk_index/score_source/score_components/rerank_score/freshness/readiness`；`search_local` 写入真实 `Chunk.id/chunk_index`，`trace_label` 输出 `块 <id>#<index>` | `tests/test_v032_retrieval_core.py`、`tests/test_knowledge_vector.py` |
| 2 | RRF 混合重排 | `knowledge.rerank_hybrid()` 纯函数：RRF_K=60，按 KB 与 score component 建立 rank list，叠加 per-KB min-max 分数，再用 freshness/readiness/稳定顺序打破平局；`search()` 最终截断前调用，原始 `score` 不被覆盖 | `test_rrf_reorders_cross_provider_passages` |
| 3 | 项目/标段作用域 | `search(..., project_id=None, package_id=None, with_diagnostics=False)`；`_kb_scope()` 对声明作用域的 KB fail-closed；失败 KB 写入 `diagnostics["failed"]`，不再静默整库丢失 | `test_kb_scope_and_diagnostics` |
| 4 | 禁止伪造来源 | `_passages_from_items` 映射真实 `id/document_id/page/position/heading`；无元数据时 source 为空、进入 title_only/suppressed，不再用 `kb.name` 冒充来源 | `test_external_metadata_without_source_is_suppressed` |
| 5 | 分级可信降级 | `material_policy.Verdict.tier` = full/excerpt/title_only/suppressed；`assess()` 与 `passage_verdict(s)()` 给出可信级别；`context_block()` 按级别渐进注入，返回契约 `(text, downgrades)` 不变 | `tests/test_material_policy.py`、`test_v032_retrieval_core.py` |
| 6 | CLI/MCP/Agent 统一 | CLI 生成改用 `job_support.knowledge_context`，MCP `search_knowledge` 增加 chunk/rerank 字段，Agent knowledge evidence 增加 kb/document/page/heading/chunk/rerank/trust/degrade | `test_cli_uses_trusted_context`、`test_mcp_and_agent_evidence_trace` |
| 7 | KnowledgeContext 契约 | `job_support.knowledge_context()` 返回 tuple 兼容对象，含 `text/downgrades/refs/diagnostics`；每个 ref 带真实 id/chunk/document/page/heading/source/snippet/score/rerank/trust/degrade；`__iter__` 仍 yield `(text, downgrades)` | `test_knowledge_context_contract` |
| 8 | 规划真实 ID 治理 | `chapter_plan.normalize_retrieval/retrieval_ref_payload/scoped_knowledge_provider`；所有 ID 引用必须有项目内真实 UUID，事实可用合法 key；无 ID/伪造/个人库越权引用被丢弃并写 `validation_errors`；取消无条件 document fallback；prompt 版本 `chapter_plan_v2` | `tests/test_v032_chapter_planning.py`、`tests/test_v032_retrieval_generation.py` |
| 9 | 规划/正文复用真实引用 | `build_plan_context/plan_context_text` 用 provider 真实 refs 持久化 chunk/document/page/heading/score/rerank/trust/degrade；`writer.py` 消费 KnowledgeContext 属性并记录 `unused_plan_refs`；suspicious-number 白名单只认规划引用 | `test_plan_traces_real_provider_refs`、`test_suspicious_whitelist_narrows_to_plan_refs_and_unused_refs_are_reported` |
| 10 | 生成时序收口 | worker 用 `Job.payload["plans"]` 或重新执行 gate，确认状态才可用（auto 例外）；`commit_generation_chapter` 在 fenced 事务内调用 `provenance.refresh_node`，写入 FactCitation；审计里的 planning id 必须等于实际使用 plan | `test_worker_rejects_stale_plan`、`test_worker_chapter_commit_refreshes_provenance` |
| 11 | 导出门禁联动 | `export_gate.stale_section_evidence/stale_gate_message` 检测过期小节并阻断普通导出；force 审计写入 `stale_sections`，原有 blocking/确认门禁不变 | `test_stale_section_blocks_export_and_force_audit_records_evidence` |
| 12 | 项目侧只读预览 | 新增 `GET /projects/{ref}/retrieval/preview?q=&limit=`，viewer 可用、项目/标段作用域、返回 passage 全部溯源/重排/可信/降级字段与 diagnostics，审计 `retrieval.preview`；路由数 269→270 | `test_retrieval_preview_is_viewer_readable_and_traced`、`tests/test_api_package.py` |
| 13 | 前端溯源 UI | `RetrievalTraceBadges/RetrievalTraceMeta/KnowledgeRefList/RetrievalPreviewPanel`；LedgerTab 只读查询预览，ChapterPlanningPanel/KnowledgeAssetPanel 显示真实块 ID、页码、标题、来源、RRF、可信等级和降级徽标；null/undefined 映射为 null，不伪造 0/trusted | web `50 files / 388 tests`、`retrievalTrace`/`knowledgeAssets`/`KnowledgeAssetPanel` tests |
| 14 | 未知信号诚实表达 | `retrieval_ref_payload` 与预览路由对缺失 `trust_tier/degraded` 保留 `null`，UI 映射 `""→null`；新增显式测试防止把“未知”伪装成“未降级” | `test_retrieval_preview_preserves_unknown_trust_signals` |

## 3. 关键文件与函数

- 检索核心：`bidcraft/knowledge.py`、`material_policy.py`、`services/job_support.py`、`cli.py`、`mcp.py`、`orchestration/agents/knowledge.py`
- 治理与生成：`bidcraft/generation/{chapter_plan,prompts,writer}.py`、`bidcraft/workers/generation.py`、`bidcraft/export_gate.py`、`bidcraft/api/routes/projects.py`
- 前端：`web/src/lib/retrievalTrace.ts`、`web/src/api/{knowledge,retrieval,generation}.ts`、`web/src/components/{RetrievalTraceBadges,RetrievalPreviewPanel,KnowledgeRefList,ChapterPlanningPanel,KnowledgeAssetPanel}.tsx`、`web/src/pages/tabs/LedgerTab.tsx`

## 4. 权限与作用域

- 新建预览只挂在既有 `/projects/{ref}` 下，复用 `_project` 与成员/角色权限；viewer 可只读，写操作仍需 canWrite。
- 知识库按 `options.project_id/package_id` fail-closed；个人库仅调用者本人可用，企业库经 `assets.accessible_enterprise_ids` 判定；任意全局 chunk/document ID 不被规划接受。
- 管理端 KB 预览仍 super-admin；企业资产审核仍 owner-only；未放宽任何权限。

## 5. 验证

- `ruff check bidcraft tests scripts`：All checks passed
- `git diff --check`：通过
- 目标后端（Phase3 新测试 + Alembic/API package/契约/导出门禁/知识/规划/策略回归）：**96 passed, 1 warning**（仅 Starlette/httpx deprecation）
- 邻接回归：138 passed（检索核心）、31 passed（双重 fencing/provenance/worker extraction）、11 passed（retrieval core）
- Web：typecheck ✓、lint ✓、build ✓、`npm test -- --run` → **50 files / 388 tests passed**
- 全量后端 pytest：`.venv/bin/python -m pytest -o addopts="" -q` → **2199 passed, 886 skipped, 1 warning in 1680.19s**，零失败（仅 Starlette/httpx deprecation）。

## 6. 失败与修复记录

- 检索核心 agent 早期遇到并发编辑导致的规划测试瞬时失败；治理 agent 落地后目标测试恢复全绿。
- 前端 agent 发现治理预览端点把缺失可信信号强制转成 `""`/`false`，无法区分“未知”与“明确的未降级”。已在 `retrieval_ref_payload` 和预览路由保留 `null`，并新增回归测试。
- 全量回归首次运行 2 failed / 2197 passed / 886 skipped；两个失败都是后台生成 Job 卡在 `running`。`faulthandler` 显示 worker 线程阻塞在 `commit_fenced_result` 的 SQL 执行：长 generation session 仍持有 SQLite 单写锁，而 Phase 3 加重了 fenced chapter 写入（provenance/plan refs/unused refs），使竞争稳定暴露。最小修复：`workers/generation.py::commit_chapter` 在调用 `commit_generation_chapter` 前先 `work_db.commit()`，与既有 `flush_delta`/`on_chapter_start`/`on_progress` 的 SQLite 单写模式一致；未改测试断言。临时调试文件已删除，两个失败测试及 Phase 3 目标套件 63 passed，最终全量回归正在重跑。

## 7. 残留风险

- 真实 Dify/RAGFlow/向量端点本地未配置；跨外部 provider 的 RRF 仅通过真实数据构造的 passage 验证，生产 provider 延迟/限流/返回差异 **NOT VERIFIED**。
- `job_support._report_payload`（本阶段未拥有）未序列化 `unused_plan_refs`；目前通过 GenerationReport、同步审计与 async done event 可见。
- 预览是同步 GET，未走 `watchJob`；真实大库上的 P95 延迟仍需后续性能阶段观测。
- 无新迁移；Phase 2 的 0014 仍是 Alembic head。

## 8. 回滚

1. 代码：revert 本阶段三个 agent 所属文件（见 git diff），删除 `tests/test_v032_retrieval_core.py`、`tests/test_v032_retrieval_generation.py` 与前端新增文件；`tests/test_api_package.py`/`test_v030_stage8_observability.py` 路由 270→269。
2. 数据库：无 schema 变化，仅删除表内新增字段/JSON 数据需按业务确认；无需 Alembic downgrade。
3. 前端：revert `web/src/`，删除 `RetrievalTraceBadges.tsx`、`RetrievalPreviewPanel.tsx`、`retrieval.ts`、`retrievalTrace.ts` 及相关测试。
