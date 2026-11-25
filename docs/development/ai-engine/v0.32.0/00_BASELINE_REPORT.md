# BidCraft AI 技术标编制引擎升级 — Phase 0 基线报告

> 目标版本：v0.32.x（任务书建议）  
> 基线版本：当前 main 最新稳定提交  
> 工作分支：`feature/v0.32-ai-engine`（从 main 创建）  
> 报告状态：Phase 0 完成；架构扫描、全量测试与基线结论已回填

## 1. Git 与版本基线

| 项目 | 实测值 |
|---|---|
| 当前分支 | `feature/v0.32-ai-engine` |
| 基线分支 | `main` |
| HEAD 完整 SHA | `883a679a469fb15a12757fd5a61d87a3af5be7f8` |
| HEAD 短 SHA | `883a679` |
| 工作区 | 创建分支前 `git status --short` 为空；无未提交修改 |
| 后端版本 | `0.31.3`（`bidcraft/__init__.py:3`） |
| 前端版本 | `0.31.3`（`web/package.json:4`） |
| 最新发布标签 | `v0.31.3` |
| 现有标签 | `v0.31.3`, `v0.31.2`, `v0.31.1`, `v0.31.0`, `v0.30.0`, ... |
| 最近提交 | `883a679`(RC G2-G9), `4e20ce2`(G1 CI), `64ab219`/`0b3e59c`(CI 冒烟修复), `9784972`(v0.31.3 验收), `4d475df`(版本 bump) |

近 20 条提交已检查，均为 v0.31.3 RC / 验收 / CI 修复与之前 v0.31.2/v0.31.3 阶段提交。分支创建前工作区干净，没有覆盖用户未提交代码。

## 2. 运行环境

| 项目 | 实测值 |
|---|---|
| Python | 3.13.5 |
| pytest | 9.2.0 |
| SQLAlchemy | 2.1.8 |
| Node | v26.13.1 |
| npm | 11.20.0 |
| Alembic head | `0012_generation_lease` |
| 迁移脚本数 | 13（含 `__init__.py`，实际 version 脚本 12） |
| 后端 Python 文件数 | 179 |
| 后端测试文件数 | 207 |
| 前端测试文件数 | 39 |
| API 路由数 | 252（`tests/test_api_package.py:46`） |

## 3. 测试基线

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端 lint | `.venv/bin/python -m ruff check bidcraft tests scripts` | All checks passed |
| 前端 typecheck | `npm run typecheck` | PASS |
| 前端 lint | `npm run lint` | PASS |
| 前端单测 | `npm test -- --run` | 39 files / 320 tests passed |
| 前端构建 | `npm run build` | PASS（Vite 8，1710 modules） |
| 后端全量 | `.venv/bin/python -m pytest -o addopts="" -q` | **2125 passed, 886 skipped, 1 warning in 801.25s（0:13:21）**，零失败 |

866+ skip 由 opt-in PostgreSQL/环境依赖测试组成（主要来自 `tests/postgres/*` 与 `BIDCRAFT_TEST_PG_URL` 未设置），另有少量既定环境跳过；唯一 warning 为 Starlette/httpx `TestClient` 弃用提示，不是失败。前端测试中的 `ErrorBoundary.test.tsx` 有意打印“渲染炸了”错误堆栈用于验证边界组件；该输出不是失败，Vitest 汇总为 39/39、320/320 通过。

## 4. 后端架构

仓库实际布局为 `bidcraft/`，**不存在任务书示例中的 `backend/` 目录**。后端共有 179 个 Python 文件，按域拆分为以下实际目录/模块：

- 顶层核心：`models.py`、`db.py`、`config.py`、`auth.py`、`authorization.py`、`audit.py`、`pipeline.py`、`jobqueue.py`、`jobs.py`、`knowledge.py`、`material_policy.py`、`provenance.py`、`scoring.py`、`substantive.py`、`verdict.py`、`constraint_check.py`、`ledger.py`、`profile.py`、`calibration.py`、`mcp.py`、`plugins.py`、`export_gate.py`。
- 子包：`generation/`（outline/writer/prompts/agent/coverage）、`evaluation/`（scorer/report/coverage/risk/quality_metrics/commercial/tender_*）、`similarity/`（service/semantic/text/structure/image）、`parsing/`（loader/extract/clean/ocr/mineru）、`documents/`（document_ir/renderer/template_parser/validator）、`export/`（docx/xlsx/pdf/templates/commercial_*）、`workers/`（8 类 runner）、`services/`（job_support/purge/money/quote_service）、`orchestration/`（planner/orchestrator/registry/approval/agents/*）、`enterprise/`（assets/matching/evidence）、`collaboration/`（locks/revisions/assignments）、`api/`（app.py/shared.py/schemas.py/routes/*）、`migrations/versions/`。

### 4.1 生成链与模型调用

- 同步单章：`api/routes/projects.py:1561 generate_chapter`；同步全书：`projects.py:1675 write`；异步全书：`projects.py:1769 generate_all` → `:1849 jobqueue.submit`。
- Worker：`workers/generation.py:118 _run_generation_job`（fencing/心跳/SSE/章节幂等）。
- 生成核心：`generation/writer.py:400 generate_document` → `:325 write_chapter` → `:189 fallback_chapter` → `:87 dedupe_body` / `:140 suspicious_numbers` / `:297 continuity_digest`。
- 落库：`writer.py:523-531`（同步）或 `workers/generation.py:50 commit_generation_chapter`（异步每章 fenced 短事务 + `chapter_committed` 事件）。
- 模型客户端：`generation/agent.py:92 ModelClient`，`complete:165`、`complete_with_tools:264`、`_tool_calls:218`、`_stream:327`、`_DeltaGate:43`；OpenAI/Ollama 适配、429/5xx 重试、Usage。
- 端点：`model_config.py:64 resolve_chat_endpoint`、`:100 resolve_embedding_endpoint`。
- Prompt 全在 `generation/prompts.py:38 build_chapter_context`（facts > needs > ledger > outline > digest > KB 优先级预算）、`SYSTEM_WRITER:147`、`chapter_messages:162`、`continue_messages:203`。

### 4.2 评分、要求与事实

- 抽取：`parsing/extract.py:279 extract_scoring`、`:455 extract_requirements_and_risks`、`:583 extract`。
- 评分服务：`scoring.py:55 list_scores`、`:65 record_scores`、`:107 analysis`、`:151 hints`。
- 大纲/评分规划：`generation/outline.py:51 normalize_scoring`、`:128 _scoring_outline`、`:211/:252 save_outline/load_outline`；`generation/coverage.py:120 calculate_score_coverage`、`:200 validate_scoring_coverage`。
- 持久化与检查：`pipeline.py:251 persist_parse`、`:422 check_project`；`rules/engine.py:74 run_checks`、`:118 ledger_gate`；`verdict.py:1115 judge`；`substantive.py:564 requirement_issues`。
- 事实：`schemas.py:71 FactLedger`；`ledger.py:36 build_ledger`、`:86 save_ledger`、`:179 load_ledger`、`:299 render_ledger`、`:405 update_fact`、`:419 ledger_summary`；`profile.py:135 facts_block`；`provenance.py:86 refresh_node`、`:220 affected_by_change`、`:176 report`。

### 4.3 知识、检索与解析

- 知识：`knowledge.py:355 ingest_document`（sha256 增量）、`:470 delete_document`、`:217 index_local`、`:525 embed_texts`、`:581 build_vectors`、`:690 search_local`（bigram + cosine，`kb_vector_weight`）、`:656 _query_vector`（嵌入失败降级关键词）、`:808 search_api`（Dify hybrid）、`:879 search`（`kb_ids` 排他 / `extra_kb_ids` 追加 + 个人库隔离）、`:951 context_block`。
- 降级：`material_policy.py:134 assess`、`:378 context_block`（返回降级条数，经 `services/job_support.py:194 _knowledge_provider` 透传）。
- 解析：`parsing/loader.py:271 load_document`（txt/pdf/docx/xlsx，`:119 _read_pdf`）、`parsing/mineru.py:132 parse_pdf`、`parsing/ocr.py:232 ocr_pdf`、`parsing/clean.py:11 strip_noise`、`:109 detect_sections`。

### 4.4 章节编辑/版本/Agent/Worker

- 章节编辑：`projects.py:1359 save_chapter`（`:1365 base_revision` CAS + 确认失效 + 快照）、`:1470 review_chapter`（draft→review→confirmed→locked）。
- 协作：`collaboration/revisions.py:69 snapshot_on_save`、`:168 diff_section_revision`、`:248 restore_section_revision`；`collaboration/locks.py:114 acquire_section_lock`；`collaboration/assignments.py:104 assign_chapter`。模型 `Section:396`、`SectionRevision:1109`、`SectionEditLock:1127`、`ChapterAssignment:1142`。
- MCP/Agent：`mcp.py:41 TOOL_SPECS`（6 内置工具）、`:313 _call_tool`、`:413 _call_plugin_tool`、`:494 handle_message`、`:581 serve_stdio`；`plugins.py:767 list_tools`、`:773 call_tool`、`:813 record_call`（沙箱/allowlist/自动停用）；`orchestration/registry.py:172 register_agent`、`:255 list_tools`、`:270 call_tool`；`orchestrator.py:163 start_workflow`、`:407 dispatch_task`、`:608 run_workflow`；`planner.py:249 build_workflow`（`:177` 9 任务 DAG）；`api/routes/orchestration.py:119 create_workflow`；`workers/agent.py:38 _run_agent_job`。
- 队列：`jobqueue.py:357 _register`、`:428 submit`、`:560 claim_next`、`:1501 start_workers`、`:1617 _worker`、`:1633 _reaper_loop`；`workers/__init__.py:19-26` 注册 8 类 kind；`jobs.py:54 publish_event`；`api/routes/jobs.py:29/:35` 轮询 + SSE 续传。
- 质量/导出门：`evaluation/scorer.py:41/:96/:121`、`report.py:65 build_quality_report`、`coverage.py`、`risk.py`、`quality_metrics.py:233`、`workers/evaluation.py`；`export_gate.py:141 enforce_export_gate`（blocking Issue + 章节确认双门禁，force 需 4 字理由且写审计）。

## 5. 前端架构

### 5.1 工作区与路由

- `web/src/App.tsx:87-143`：`createBrowserRouter`；`/app/projects/:ref` 由 `WorkspacePage` 承载，15 个惰性子路由，包括 `outline`(:118)、`draft`(:119)、`ledger`(:116)、`risk`(:120)、`check`(:121)、`quality`(:126)、`enterprise`(:122)、`similarity`(:124)、`agents`(:125)。
- `web/src/pages/WorkspacePage.tsx:74`：`WorkspacePage`；`tabs` 数组 :203-289；`Outlet context` :416；`WorkspaceContextValue` :49-63 提供 `ref/basePath/project/sources/packages/readOnly/canWrite/reloadProject/reloadSources/setUnsaved`。
- `web/src/pages/useWorkspace.ts:4`：子 tab 统一 context 入口。

### 5.2 章节树、编辑器与生成控制

- `web/src/pages/tabs/DraftTab.tsx:261`：技术标编辑页。章节列表 `chapter-list` :1790-1845；编辑器 `editor-panel` :1852-2018；编辑/预览 `segmented` :1910-1927；保存/生成按钮 :1928-1961；复核条 :1965-2004；来源追溯 `trace-card` :2020+；配图工作台 `image-studio` :2073、计划 `image-plan` :2403。
- `web/src/components/ChapterCollab.tsx:56`：锁、修订、恢复；`components/AssignmentPanel.tsx:40`：任务分派。
- `web/src/pages/tabs/OutlineTab.tsx:96`：目录/写作意图与字数规划；编排方式 `OUTLINE_MODES` :422-451、总字数 :453-486、生成按钮 :500-509；章节行 :587-725（标题、写作意图、目标字数、依赖事实 `needs`、逐章生成）；`ScoreCoverage` 嵌入 :513。
- AI 生成控制：`DraftTab.tsx` 的 `startFullGeneration` :1355、`runChapter` :774、`startImageBatchGeneration` :1388、`applyPlanNow` :1458；`OutlineTab.tsx` 的 `generate` :218、`save` :165、`runChapter` :315。

### 5.3 需求、评分、事实、风险、质量

- `LedgerTab.tsx:57`：内页签类型 `facts|materials|parameters|scoring|requirements|risks`，`tabItems` :341-353，WAI-ARIA tablist :456-478；`scoring` 分支 :648-657 挂载 `ScoreBackfill`（`components/ScoreBackfill.tsx:35`）；`components/ScoreCoverage.tsx:23` 只读覆盖矩阵。
- `RiskTab.tsx:804` `SubstantiveSection`：实质性条款响应 UI，筛选 :759+，确认/不适用 :1092-1115。
- `LedgerTab.tsx:77`：事实台账；`GateBanner`（`components/Workbench.tsx:69`）、汇总 :408-453、事实表 :578-647、行组件 `FactRow` :677、只读表 `ReadonlyLedger` :862、键盘导航 :355-384。
- `RiskTab.tsx:81`：四档风险聚合、`recheck` :205、`markHandled` :241、跳转草稿 `jumpToDraft` :234、`RiskItemCard` :1199；`CheckTab.tsx:45`；`QualityTab.tsx:151`（同步 `handleRun` :174、异步 `handleRunAsync` :194、`QualityDimensions` :67、`FindingCard` :87）。

### 5.4 知识资产与检索 UI

- 项目侧无独立 KB 页签；`EnterpriseTab.tsx:44` 含企业文档清单（`EnterpriseAssetList.tsx:15`，:444）与「按需求匹配企业资料」:541-589。
- KB 管理在 `AdminConsole.tsx:332` → `AdminKnowledgeTab.tsx:90`；KB 预览弹窗 `AdminKbDocsModal.tsx:91`（`meta/docs` 分段 :373-380、`runPreview` :251-263、检索片段溯源展示 :580+）；重建/向量化在 `AdminKnowledgeTab.tsx:239/280`。

### 5.5 任务状态与 API 层

- `hooks/watchJob.ts:74` 是唯一 SSE/轮询传输层，事件集 :56-60、`jobEventUrl` :62-64、SSE→轮询降级 :134-162。新长任务必须接 `watchJob`，禁止页面自建 EventSource。
- `DraftTab.tsx:1263-1353` 全书生成（stream + delta 实时预览 :1730-1747、进度条 :1669-1728）；配图轮询 :1213-1261；`QualityTab.tsx:201-230`；`AdminKnowledgeTab.tsx:164` 重建任务。
- API 封装：`lib/http.ts:227` `apiFetch` / `request` :295 / `requestBlob` :323；`api/generation.ts`（outline :5-39、scoring-coverage :19、chapter :41-120、`startDocumentGeneration` :122、`getJob` :128）；`api/ledger.ts:5/20`；`api/compliance.ts:86-193`；`api/evaluation.ts:133-186`；`api/similarity.ts:136-206`；`api/knowledge.ts:6-365`；`api/enterprise.ts`；`api/orchestration.ts:137-258`；`api/jobs.ts:24-46`；归一化集中在 `api/shared.ts`（KB :300-387）。
- 类型集中在 `lib/types.ts`：`OutlineChapter` :508、`ChapterDetail` :525、`Ledger` :485、`ScoringCoverageReport` :460、`RequirementResponse` :689、`Issue` :605、`Job` :749、`KnowledgeBase` :109、`KbSearchPassage` :2087、`QualityReport` :2316。

### 5.6 权限与复用结论

- `lib/permissions.ts`：`canGlobalWrite` :22、`isProjectAdmin` :31、`canEditProject` :38、`canReviewChapter` :48、`canForceWrite` :53；`AuthContext.tsx:119-120` 暴露 `isSuperAdmin/canWrite`；`WorkspacePage.tsx:131,144-145` 计算 `readOnly/canWrite`，:405-415 只读横幅；`DraftTab.tsx:739` 复核负责人、`ExportTab.tsx:112` 强制导出、`CollabTab.tsx:42` 成员管理；角色类型 `types.ts:23,243,1658`。
- **不得新建第二工作台**：章节规划面板可直接放在 `OutlineTab.tsx:407-513` 的 `outline-config` 与 `ScoreCoverage` 之间，复用模式卡、字数控制、章节行、`Save/useUnsavedGuard`。
- 知识材料面板可复用 `LedgerTab` 的 tab 体系（:57/:341/:456）、`ReadonlyLedger`/`SourceQuote`、`EnterpriseAssetList.tsx:15`、`api/enterprise.ts`、`AdminKbDocsModal` 的 passage 渲染与 `api/knowledge.ts:117/130/248`。
- 质量检查面板可复用 `QualityTab.tsx:151` 与 `CheckTab.tsx:45`、`RiskTab.tsx:81` 的 `TabHeading/StatTile/verdict-banner/FindingCard`。
- 前端最小增量风险：`apiSurface.test.ts` 必须把新增 API namespace 纳入代理白名单，否则新端点被 SPA 兜底；新按钮必须沿用 `canWrite/readOnly` 与 `canReviewChapter`，只读隐藏写操作。

## 6. 数据库与迁移

### 6.1 模型布局

单文件 `bidcraft/models.py`（1193 行）：`Base`/`utcnow`/`new_id` 位于 :31/:23/:27。核心实体：

- 项目与资料：`Project` :35、`SourceFile` :54、`Fact` :74（`uq_fact_project_key` :78）、`FactCitation` :98。
- 标段/包件：`BidPackage` :131、`BoqSnapshot` :152、`BoqQuoteVersion` :179、`CommercialDoc` :1037。
- 评分与需求：`ScoringItem` :324（`section_hint` :334）、`Requirement` :341。
- 章节：`OutlineNode` :381（`parent_id` :386、`needs` :391）、`Section` :396（`revision` :405、`review_status` :409）。
- 风险/任务：`Issue` :419、`Job` :455（`owner_id` :473、`claim_token` :486、`payload/result` :469-470）、`JobEvent` :526。
- 知识：`KnowledgeBase` :692、`KbDocument` :970、`KbChunk` :739（`embedding`/`embed_key`/`embed_dim` :750-752）。
- 检索：`SimilarityReport` :992、`SimilarityHit` :1010。
- Agent：`WorkflowRun` :1052（`input_snapshot/budget` :1063/:1066）、`WorkflowTask` :1073（`input/output` :1084-1085）、`WorkflowApproval` :1092。
- 质量：`QualityReport` :1159、`QualityFinding` :1177。
- 权限/组织：`ProjectMember` :866、`AuditLog` :881、`Enterprise` :947、`EnterpriseMember` :959。
- Job owner 由事件 `_job_owner_from_payload` :494 派生。

### 6.2 迁移框架

- `bidcraft/migrations/env.py`：无根目录 `alembic.ini`，URL 运行时注入。
- `bidcraft/db.py:74 init_db`：`create_all` + `_migrate_columns` :172 + `_run_alembic` :148（老库 stamp `0001_baseline` 再 upgrade head）。
- PG 咨询锁 `_pg_migration_guard` :100 fail-closed；SQLite 文件锁 :84。
- `versions/` 12 个 revision（0001…0012），单 head = `0012_generation_lease`（`tests/test_alembic.py:12` 必须同步）。
- 幂等惯例：`inspect(bind)` 判存 + `IF NOT EXISTS`（0011/0012）、可逆 downgrade。CI AST 扫描断言单 head（`.github/workflows/ci.yml:57-95`）。
- 历史迁移禁止改动：见 `tests/fixtures/tender_real/README.md:23` 与多份 `docs/development/*FINAL_ACCEPTANCE_REPORT.md`。

## 7. 章节生成流程

### 7.1 异步全书生成（推荐主路径）

`projects.py:1769 generate_all` → 计费/GenerationLease/PendingCharge marker → `Job(kind=generate)` → `jobqueue.submit` :1849 → `workers/generation.py:118 _run_generation_job` → `generation/writer.py:400 generate_document` → `:325 write_chapter` → `generation/prompts.py:162 chapter_messages` → `ModelClient.complete:165` → `workers/generation.py:50 commit_generation_chapter`（每章 fenced 短事务 + `chapter_committed` 事件）→ `settle_pending_charge`。

### 7.2 同步单章/全书

`generate_chapter` :1561 / `write` :1675 → `acquire_generation_lease` → `pending_charge_heartbeat` → `generate_document`（直接写 Section/OutlineNode）→ `provenance.refresh_node` → `audit`。

### 7.3 规划与正文之间的现状

已有 `outline.py:51 normalize_scoring` / `:128 _scoring_outline` / `:211 save_outline`，评分点通过 `OutlineNode.needs` 注入 prompt（`prompts.py:87-93`），并有 `coverage.py:120 calculate_score_coverage` / `GET /scoring-coverage`。但**没有独立、可版本化、可确认的章节规划记录**；`save_outline` 是破坏性重建，规划状态没有确认状态机。这是 Phase 1 的主要缺口。

## 8. 知识库与检索流程

- 注入：`services/job_support.py:194 _knowledge_provider` → `knowledge.search:879`（全局 + 个人 + 企业库）→ `material_policy.context_block:378`（降级为“题录”）→ `writer.generate_document:483` → `build_chapter_context:38` 的 KB 块。
- 入库：`ingest_document:355` 按 sha256 增量；`KbDocument`/`KbChunk` 承载章节与向量；`_ready_chunk_filter:676` 做就绪门禁。
- 检索：`search_local:690`（bigram + cosine 加权）、`search_api:808`（Dify hybrid），嵌入失败降级关键词。
- 企业资产：`enterprise/assets.py:156 enterprise_kb_ids`、`matching.py:514 match_any`、`evidence.py:159 aggregate`。
- 现状缺口：无 rerank/RRF，跨库按单一 score 截断（`knowledge.search:934`）；降级仅“题录”一档；无知识资产审核/发布状态机、结构化提炼、重复资产候选组。

## 9. Agent/MCP 工具与分发入口

- 工具声明：`mcp.py:41 TOOL_SPECS`（6 内置工具）、分发 `:313 _call_tool`、插件工具 `:413 _call_plugin_tool`、消息入口 `:494 handle_message`、stdio `:581 serve_stdio`。
- 插件：`plugins.py:767 list_tools`、`:773 call_tool`、`:813 record_call`，具备沙箱、allowlist、自动停用、作用域与 Ed25519 包签名。
- Agent Registry：`orchestration/registry.py:172 register_agent`、`:255 list_tools`、`:270 call_tool`。
- DAG：`planner.py:249 build_workflow`，标准工作流 9 任务；`orchestrator.py:163 start_workflow`、`:407 dispatch_task`、`:608 run_workflow`；审批 `approval.py:79/:129`；`workers/agent.py:38 _run_agent_job`（waiting_approval 时 Job 以 done 结束）。
- 关键缺口：`ModelClient.complete_with_tools:264` 已解析 `tool_calls`，但生成链与 Agent 都未真正执行工具；`agents/technical.py:23` 仍是确定性分析，缺少 planner→tool→观察闭环。Phase 4 必须加薄工具循环，复用 `registry.call_tool:270`，不得新建第二套工具系统。

## 10. API 接口清单

`bidcraft/api/app.py:748-765` 注册 18 个 router。关键声明（均在 `bidcraft/api/routes/`）：

- `projects.py`：`GET/POST /projects` :203/:254；`/projects/{ref}` :466/:491/:710；`/facts/{key}` PATCH :1237；`/outline` :1263/:1270/:1294；`/chapters/{node_id}` :1327/:1359、`/review` :1470、`/generate` :1561、`/write` :1675、`/generate` :1769、`/scores` :650/:675、`/scoring-coverage` :657、`/requirements` :1925/:1944、`/export` :2123、`/packages` :2213-2311、`/boq*` :2328-2717、`/audit` :880。
- `similarity.py`：`/similarity/check` :38、`/reports` :144/:154、`/hits` :162/:181、`/export` :199。
- `evaluation.py`：`/evaluation/run` :44、`/evaluation/run-async` :63、`/reports` :129/:137/:150、`/findings` PATCH :161。
- `orchestration.py`：`/agents` :89、`/workflows` :106/:119/:148/:156/:171/:185、`/workflow-approvals` :210/:226。
- `collaboration.py`：章节锁/修订/分配 :64-261。
- `commercial.py` :49-179；`admin.py`：`/admin/models` :123、`/admin/kbs` :228、`/admin/kbs/{id}/reindex` :342、`/admin/audit` :1540；`jobs.py` :29/:35；`approvals.py` :15/:26；`health.py` :27/:66/:73。

鉴权与审计：

- `bidcraft/authorization.py`：角色集合 :30-34，`check_write` :50、`check_owner` :57，`_effective_role` :64（超管=admin、owner、ProjectMember editor/viewer），`authorize_project` :85（不可见→404）、`authorize_job` :115、`authorize_projects` :142、`authorize_node` :183。
- HTTP 薄封装 `bidcraft/api/shared.py`：`_project` :340、`_require_write` :350、`_require_owner` :357、`audit` :364、`_job` :372；全局角色 `bidcraft/auth.py:20-24`。
- 审计统一 `bidcraft/audit.py:record_audit` :18（actor > current_user > system）；`AuditLog` append-only；导出强制审计 `bidcraft/export_gate.py:enforce_export_gate` :145。

测试/CI/部署基线：

- pytest 文件 207（tests 根 201 + tests/postgres 6），test 函数 1626；web 测试 39 文件。
- PG opt-in：`BIDCRAFT_TEST_PG_URL`，`tests/postgres/conftest.py:15-23` fixture 内 skip，`tests/test_postgres_integration.py:30` 等 skipif；其他 skip：审批样本未触发闸门、fontconfig/中文字体缺失（`tests/test_batch4_export_engine.py:273-290`）。
- CI 9 job：python-lint、python-tests、docker-image、web、postgres、docx-render、security-dependency-audit、security-sast、security-secret-scan；门禁：pytest + calibrate/verdict 回归、PG `tests>=900/skipped=0/failures=0`（ci.yml:474-499）、ruff、single-head、Alembic preflight、pip-audit/npm audit(high)、bandit `-ll` + `scripts/bandit_gate.py`、gitleaks、镜像非 root/healthcheck。
- 配置：`bidcraft/config.py:190-205`（`BIDCRAFT_DB_URL`、MODEL_BASE_URL/API_KEY/NAME/TEMPERATURE/TIMEOUT、OFFLINE、VERDICT_LLM），KB 向量 :378-391，`model_configured` :503；`bidcraft/model_config.py:resolve_chat_endpoint` :64（有 env 回退）、`resolve_embedding_endpoint` :100（**无 env 回退**，只从 `ModelConfig.kind="embedding"` 取）；embed 缓存键 `bidcraft/knowledge.py:74/590-673`。部署 `deploy/docker-compose.yml:28-44`、`deploy/.env.example:45`。

## 11. 已有能力映射（不得重复建设）

| 任务书目标 | 当前已实现底座 | 结论 |
|---|---|---|
| ChapterPlanningEngine | `generation/outline.py:51/:128/:211/:252` + `scoring.hints:151` + `generation/coverage.py:120`；`needs` 已注入 prompt（`prompts.py:87-93`） | 已有规划雏形，补版本/状态机/确认/引用校验，**不得重写目录生成** |
| KnowledgeAssetEngine | `KbDocument`/`KbChunk` + `knowledge.ingest_document:355/delete_document:470` + `enterprise/assets.py:156`、`matching.py:514`、`evidence.py:159` + `material_policy.py:134` | 已有资产底座，补结构化提炼、审核发布状态、重复候选，**不得新建文库** |
| HybridRetrieval | `knowledge.search_local:690`（bigram+cosine）、`search_api:808`（Dify hybrid）、嵌入失败降级、文档就绪门禁 | 已有混合检索，补 rerank/适用性评分/统一权重，**不得替换向量设施** |
| AgentTooling | `ModelClient.complete_with_tools:264` + `registry.list_tools/call_tool:255/:270` + `mcp.TOOL_SPECS:41` + 插件沙箱 + DAG/预算/审批/轨迹 | 已有工具注册与分发，补生成链工具执行闭环与统一审计，**不得新建第二套工具系统** |
| ExperienceLearning | `calibration.py:100/:337/:382`、`evaluation/*`、`provenance.py`、`AuditLog` | 有底座但无在线反馈环；补“人工确认/质量 finding → 可审核经验”薄回流 |
| FactGovernance | `FactLedger` + `Fact/FactCitation` + `update_fact` 陈旧检测 + `ledger_gate` + `suspicious_numbers` | 已有事实台账与门禁，补冲突组/来源权威/确认人/章节影响追踪 |
| QualityEvaluation | `evaluation/{scorer,coverage,risk,report,quality_metrics}.py` + `verdict.judge` + `export_gate.enforce_export_gate` | 已有质量快照，补章节级统一评估与 UI，质量评分不得绕过导出门 |
| 异步任务与审计 | `Job/JobEvent`、8 worker、SSE、`AuditLog`、`record_audit` | 直接复用，长任务全部走 jobqueue + watchJob |

### 11.1 真实缺口（Phase 1-7 的工作边界）

| 级别 | 缺口 | 最小扩展 |
|---|---|---|
| P0 | `complete_with_tools` 已解析 tool_calls，但生成链与 Agent 均未执行工具 | 新增薄循环 `generation/tool_loop.py`，复用 `registry.call_tool:270` + `ChatResult.tool_calls`，预算借用 `WorkflowRun.budget`；不改 writer 签名、不加表 |
| P0 | 无“人工确认/质量 finding → 写作偏好”回流通道 | 新增 `evaluation/learning.py::lessons_block(db, project)`，从 `QualityFinding` + `SectionRevision.change_note` 生成文本，作为 `facts_text` 追加到 `writer.generate_document:441`；零 schema 变更 |
| P1 | 评分项↔章节仅 `section_hint` 单值，无绑定/守恒校验 | 新迁移 `0013` 加 `scoring_bindings(scoring_item_id,node_id,weight)`；`outline._scoring_outline` 读取优先绑定，不动旧字段 |
| P1 | 规划不可版本化，`save_outline` 破坏性重建，无确认状态 | `OutlineNode` 增 `plan_version`/`plan_status`（同 0013），`save_outline` 按版本追加而非清空 |
| P1 | 混合检索无 rerank/RRF，跨库按单 score 截断 | `knowledge.py` 加纯函数 `rerank_hybrid(passages)`（加权归一化 + 就绪/有效期二次排序），在 `search:934` 调用 |
| P1 | 质量报告导出/跨项目看板未实现 | 新增只读 `GET /evaluation/reports/{id}/export` 与聚合查询端点，保持“不称中标概率” |
| P2 | 单章只有 2 次续写，无多候选/质量重试 | `write_chapter` 增可选 `candidates`，内部复用 `verify_chapter:59` 择优，默认 1 |
| P2 | 事实确认无逐字段审批人/时间 | 迁移 0013 给 `Fact` 加 `confirmed_by/confirmed_at`，`update_fact:405` 与审计同事务 |

## 12. 功能到实际文件映射

| Phase | 主改/复用文件 |
|---|---|
| Phase 0 基线 | `docs/development/ai-engine/v0.32.0/00_BASELINE_REPORT.md` |
| Phase 1 Chapter Planning | `generation/outline.py:51/:128/:252`、`scoring.py:151`、`generation/coverage.py`、`api/routes/projects.py:1263-1324`、`web/src/pages/tabs/OutlineTab.tsx:407-513` |
| Phase 2 Knowledge Asset | `knowledge.py:355/:470`、`models.py:970/739`、`enterprise/{assets,matching,evidence}.py`、`material_policy.py:134`、`web/src/pages/tabs/EnterpriseTab.tsx` |
| Phase 3 Hybrid Retrieval | `knowledge.py:656/:690/:808/:879`、`material_policy.py:378`、`services/job_support.py:194`、`web/src/pages/tabs/SimilarityTab.tsx`、`web/src/pages/tabs/LedgerTab.tsx` |
| Phase 4 Agent Tooling | `generation/agent.py:264`、`orchestration/{registry,orchestrator,planner,approval}.py`、`mcp.py:41`、`plugins.py:767`、`workers/agent.py`、`web/src/pages/tabs/AgentsTab.tsx` |
| Phase 5 Experience + Fact Governance | `evaluation/learning.py`（新）、`evaluation/{scorer,report,quality_metrics}.py`、`calibration.py:100`、`verdict_llm.py:106`、`provenance.py:176`、`ledger.py:405`、`writer.py:140` |
| Phase 6 Quality Evaluation | `evaluation/{coverage,risk,report}.py`、`export_gate.py:141`、`api/routes/{evaluation,projects}.py`、`web/src/pages/tabs/QualityTab.tsx:151`、`web/src/pages/tabs/CheckTab.tsx:45` |
| Phase 7 全链路验收 | `tests/*`、`tests/postgres/*`、`web/src/**/*.test.ts(x)`、`.github/workflows/ci.yml`、`deploy/*` |

必须扩展的测试：后端新增规划/知识/检索/工具/经验/事实/质量单元测试；集成链“上传→解析→索引→提炼→审核→发布→规划→确认→检索→生成→质检→修改→经验→复用”；回归覆盖项目创建/删除、文件解析、章节生成/编辑/版本、事实、知识库、风险、导出、权限、组织/标段隔离、计费、Job/SSE、MCP、Docker、迁移。前端扩展 `OutlineTab.test.tsx`、`DraftTab.test.tsx`、`LedgerTab.test.tsx`、`QualityTab.test.tsx`、`AdminKbDocsModal.test.tsx`、`api/outboundContract.test.ts`、`api/apiSurface.test.ts`、`hooks/watchJob.test.ts`、`lib/permissions.test.ts`。

## 13. 兼容性分析

### 13.1 可直接复用，不重复建表

- 项目 `Project` :35；招标文件 `SourceFile` :54；标段/包件 `BidPackage` :131；评分项 `ScoringItem` :324；条款需求 `Requirement` :341；事实 `Fact` :74 + 引用 `FactCitation` :98；大纲层级 `OutlineNode.parent_id` :386；章节 `Section` :396；知识库三件套 `KnowledgeBase` :692/`KbDocument` :970/`KbChunk` :739；查重 `SimilarityReport` :992/`SimilarityHit` :1010；质量 `QualityReport` :1159/`QualityFinding` :1177；Agent `WorkflowRun` :1052/`WorkflowTask` :1073/`WorkflowApproval` :1092；异步任务 `Job` :455/`JobEvent` :526；审计 `AuditLog` :881；组织 `Enterprise` :947/`EnterpriseMember` :959；项目成员 `ProjectMember` :866。
- 多候选/质量重试可用 `WorkflowTask.input/output` :1084-1085 与 `Job.payload/result` :469-470，无需新表；生成模式/预算可用 `WorkflowRun.input_snapshot/budget` :1063/1066。

### 13.2 需新增或明确承载

- 评分项↔章节绑定/覆盖矩阵目前只读推导（`bidcraft/generation/coverage.py:1-20`、`api/routes/projects.py:657`），无持久化表。若需人工绑定，须新增迁移（建议 `0013`，唯一键 `scoring_item_id + node_id`）；否则继续复用 `ScoringItem.section_hint` :334。
- 组织级项目隔离字段不存在（`Project` 无 `enterprise_id`）；标段级技术标字段只存在于 BOQ/商务（`BoqSnapshot.package_id` :165、`BoqQuoteVersion.package_id` :191、`CommercialDoc.package_id` :1037），大纲/章节/事实/需求/质量均为项目级。
- 新实体映射原则：优先扩展现有模型；确需新增时必须新迁移、中文注释、索引、唯一约束，单 head 同步 CI。

### 13.3 主要兼容性风险与最小缓解

1. **组织隔离**：企业级字段仅在 `Qualification` :236/`Personnel` :261，`authorize_project` :85 不感知 enterprise。最小缓解：新 AI 资源沿用 `ProjectMember` + owner 口径，不新增组织维度；确需组织隔离则仅加可空 `Project.enterprise_id`（新迁移）并在 `authorize_project` 统一判定。
2. **标段隔离**：`authorize_node` :183 只校验节点属于项目，查重/质量/Agent 仅按 `project_id` 查询。最小缓解：一标段一 Project（复制 `duplicate_project` :735），或给新实体加可空 `package_id` 并在所有查询/门禁加 `project_id + package_id` 复合条件；严禁跨包聚合。
3. **项目成员权限**：新路由必须经 `_project`/`_require_write`（写）并 `audit`，否则 viewer 可越权；超管 `_effective_role` :64 全放行需保留。
4. **既有导出门禁**：`enforce_export_gate` :145 对 docx/pdf/md 校验未处置 blocking 与 `review_status ∈ {confirmed, locked}`，强制导出不能绕过章节确认。新 AI 生成章节默认 `Section.review_status="draft"` :409，会阻断导出；缓解：生成后走章节复核流程、阻断问题先处置，禁止新写路径绕过门禁或新增 `require_confirmed_chapters=False` 分支。

## 14. 基线结论与风险

### 14.1 结论

Phase 0 基线成立，允许进入 Phase 1：

- 代码基线真实且工作区无未提交修改；分支 `feature/v0.32-ai-engine` 从 `883a679` 创建，未触碰 main。
- 版本、发布标签、Alembic 单 head、API 路由数、测试基线均已实测记录。
- 三路架构扫描完成，确认任务书中建议路径与仓库实际路径存在差异：实际使用 `bidcraft/`、`web/src/`，不存在 `backend/`；所有后续实现以实际代码为准。
- 现有系统已具备厚实底座（生成、评分、事实、知识、检索、Agent、质量、Job、权限、审计），禁止重复建设；新增能力只做薄扩展。

### 14.2 已知风险与实施纪律

1. **不得大规模重构**：不得替换 `generation/writer.py`、`knowledge.py`、`orchestration/*`、`mcp.py` 等核心链路；只增薄适配层与必要字段。
2. **规划状态与大纲兼容**：新增规划版本/状态时须保留旧 `OutlineNode.needs` 与 `save_outline` 兼容，避免破坏现有生成与前端。
3. **知识资产与现有 KB 兼容**：不得新建第二文库；结构化资产必须挂在 `SourceFile`/`KbDocument`/`KbChunk` 现有来源与权限体系上。
4. **Agent 工具真实性**：`ModelClient.complete_with_tools` 已解析 tool_calls，Phase 4 必须真实执行并审计，不能让 AI 声称成功。
5. **学习闭环审核**：用户修改/质量 finding 只能产生“建议经验”，未经审核不得自动企业级生效。
6. **事实不可编造**：所有规划/知识引用的评分点 ID、事实 ID、章节 ID、知识 ID 必须真实存在；校验失败不得进入正文生成。
7. **权限与标段**：所有新 API 走 `_project`/`_require_write`/`audit`；禁止跨项目、跨标段读取或注入资料。
8. **导出门禁不可削弱**：新生成章节默认 `review_status=draft`，未处置 blocking 问题必须继续阻断导出。
9. **测试环境局限**：本地无真实 PG 常驻服务、Docker、真实模型 Key、真实标注投标文件；相关能力在报告中必须标注 NOT VERIFIED，不得声称通过。
10. **迁移纪律**：如新增 0013，只新增历史之后的 revision，禁止编辑 0001-0012；同步 `tests/test_alembic.py` 与 CI 单 head 检查。

## 15. 基线命令记录

```bash
git branch --show-current
git rev-parse HEAD
git status --short
git log --oneline -20
git tag --sort=-creatordate | head -20

.venv/bin/python -m ruff check bidcraft tests scripts
.venv/bin/python -m pytest -o addopts="" -q

cd web
npm run typecheck
npm run lint
npm test -- --run
npm run build
```
