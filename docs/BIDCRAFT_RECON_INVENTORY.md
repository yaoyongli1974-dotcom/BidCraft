# BidCraft 对标易标改造 — 侦察复用清单与实施计划（阶段 0）

> 基线 HEAD `1d75c90` / v0.23.0。本文件是「先审查、后改造」的产物：逐模块列出**已有能力（可复用）**、**缺失能力**、**建议修改/新增文件**与**复杂度**。经 5 路只读侦察交叉核对，所有函数/行号均来自当前检出源码。
>
> 任务书基准写 v0.22.0（`6f3d8e7`），实际 HEAD 已是 v0.23.0；按「以当前检出的源码为准」条款，本改造从 v0.23.0 起。

## 通用结论

- **技术栈与架构全部保留**：FastAPI + SQLAlchemy 2.x + Pydantic 2.x + Alembic + SQLite/PG；React/TS/Vite；现有 CSS；DB 驱动 Job Queue + JobEvent + SSE/轮询。
- **可复用的底座非常厚**：规则引擎 `rules/engine.py`、五层判定 `verdict.py`、constraint_check（数值/单位/主体）、事实台账 `ledger.py`、查重内核 `dedup.py`、embedding 全链路 `knowledge.py`、Job Queue/fencing/幂等 `jobqueue.py`、MCP 工具沙箱 `plugins.py`/`mcp.py`、审批链 `boq.py`、章节复核状态机、导出门禁 `export_gate.py`、审计 `audit.py`、Alembic programmatic 配置。
- **禁止重复建设**：不重写规则引擎、不重写章节复核状态机（继续用 `review_chapter()`）、不重写报价审批链（复用 `boq.normalize_chain/applicable_stages/completed_stages`）、不重写 Job Queue、不新建知识库向量配置中心。

### 必须先修的 3 个确定性缺陷（顺手纳入对应模块）

1. **PDF 导出漏传模板**：`api/routes/projects.py:1917-1923` 导出 PDF 分支的 `export_docx` 未传 `template=`，导致同项目 DOCX 与 PDF 排版不一致（模块2修）。
2. **评分 `needs` 是死字段**：`generation/writer.py` 的模型路径未把大纲节点的 `needs`（必须引用的事实）注入 prompt，仅 fallback 用（模块1修）。
3. **生成时全局企业知识库被排除**：`services/job_support.py:148-158` 把个人库 ID 放进 `kb_ids`，而 `knowledge.search` 在 `kb_ids` 非空时只查这些库（`knowledge.py:568-569`），与注释「Global knowledge bases stay available」矛盾（模块3修）。

---

## 模块1 AI 技术标生成引擎升级 — 复杂度：高

**已有能力**：`generation/outline.py`（`_scoring_outline:61` 按分值权重分配字数、`build_outline:140` 三模式、`save_outline:168`、`load_outline:193`）；`generation/writer.py`（`generate_document:328` 逐章循环 + `before/after_chapter` 钩子 + 确定性 `fallback_chapter:135` + `dedupe_body:33` + `suspicious_numbers:86` + `continuity_digest:240`）；`generation/agent.py::ModelClient.complete:86`；`scoring.py`（`score_payload:32`/`analysis:107`/`hints:151`）；`parsing/extract.py::extract_scoring:258`；`workers/generation.py::_run_generation_job:39`（fencing + 章节幂等账本 + SSE）；API `outline/chapters/generate/write/generate`。

**缺失**：评分项↔章节绑定表与覆盖矩阵（`section_hint` 从未参与大纲）；评分项去重/总分守恒校验；`needs`/章节级条款与证据未注入 prompt；逐章指令重写/多候选/质量重试；大纲层级（`parent_id`/`children`）未落地；重生成即清空正文（`save_outline` 破坏性重建）；模式未持久化；无全书后处理。

**建议文件**：新增 `generation/coverage.py`、`generation/scoring.py`（评分绑定，注意任务书写的 `generation/scoring.py` 实际拆在 `parsing/extract.py` + `scoring.py`）；改 `generation/outline.py`/`prompts.py`/`writer.py`、`scoring.py`、`models.py`（ScoringBinding）、`api/routes/projects.py`（`GET /score-coverage`）、`api/schemas.py`、`workers/generation.py`、前端 `OutlineTab.tsx`/`DraftTab.tsx`/`api/generation.ts`；新增 `PlanningTab.tsx`/`ScoreCoverage.tsx`。测试 `test_scoring_planning.py`/`test_generation_context.py`/`test_generation_quality.py`/`test_scoring_coverage_api.py`。

## 模块2 DOCX 专业文档引擎 — 复杂度：中（强制 Word 原生模板/分节/多级编号则中高）

**已有能力**：`export/docx.py`（`export_docx:266`、东亚字体、样式、封面、TOC 域、页码域、表格、图片、待填占位标红）；`export/templates.py`（4 套预设、`normalize:89`、`load:125`、`save:135`）；`export/pdf.py::export_pdf:129` + `_uno_pdf.py`（LibreOffice/UNO 更新域）；API `export-templates`/`export-template`/`export`。

**缺失**：Document IR 中间表示；Word 原生模板继承（`Document()` 无 `base_template`）；自定义模板持久化（`save` 只收预设 id）；分节/横向页/首页不同/奇偶页不同；Heading 4-6 与多级自动编号；表头跨页重复/列宽/合并/三线表/题注；图目录/表目录/交叉引用；核心属性/水印/待填清单报告；导出后校验；**PDF 漏传模板缺陷**。

**建议文件**：新增 `documents/{document_ir,template_parser,renderer,validator}.py`，可选 `services/docx-openxml/`（.NET 10，仅可选增强，不设为强依赖）；改 `export/docx.py`/`templates.py`、`api/routes/projects.py`（修 PDF 模板 + 模板 CRUD + `/export/preview`、`/export/{delivery_id}/report`）、`api/schemas.py`、`workers/export.py`、`mcp.py`/`cli.py` 统一模板；前端 `ExportTab.tsx` + 新增 `DocumentTemplateTab.tsx`/`DocumentPreview.tsx`。测试 `test_document_ir.py`/`test_docx_template_parser.py`/`test_docx_renderer.py`/`test_docx_validator.py`/`test_openxml_service.py`。

## 模块3 企业知识库 2.0 — 复杂度：高

**已有能力**：`models.py`（KnowledgeBase/UserDoc/KbChunk/Qualification/Personnel）；`knowledge.py`（`tokens_for:148`、`_chunks:159`、`index_local:199`、`build_vectors:315`、`embed_texts:259`、`search_local:410`、`search:550` 向量+词法混合）；`qualifications.py`（`qualification_payload:160`、CRUD、证据、到期 `status_of:139`、`preflight:426`）；`personnel.py`；`material_policy.py`；`knowledge_audit.py`；API `/my/*`、`/admin/kbs/*`。

**缺失**：文档级实体（`KbDocument`：版本/哈希/页码/有效期/密级/标签）；企业库上传/单文档增删重建；增量索引（现全量删后重建）；检索从 Python 全表线性扫描升级；企业/部门/项目级授权（现仅全局+个人二分）；资质/人员企业共享（现 owner 私有）；证据哈希/去重；**全局库被排除的阻断级缺陷**；知识库体检只支持 md/txt 且仅 CLI。

**建议文件**：改 `models.py`（KbDocument + KbChunk 增列）、`db.py`、`knowledge.py`（`ingest_document`/`delete_document`/增量/修 kb_ids 语义）、`services/job_support.py`（修检索范围）、`material_policy.py`、`knowledge_audit.py`、`api/routes/admin.py`/`my.py`、`api/schemas.py`、前端 `knowledge.ts`/`AdminKnowledgeTab.tsx`/`AdminKbDocsModal.tsx`；新增 `AdminKbDocumentPanel.tsx`、`KnowledgeSearchPreview.tsx`。测试 `test_kb_documents.py`/`test_knowledge_scope.py`/`test_enterprise_*.py`。

## 模块4 标书查重系统 — 复杂度：中

**已有能力**：`dedup.py`（`split_paragraphs:34`/`normalize:46`/`similarity:52`/`collect:106`/`compare:148`/`render:258`，字符二元组 Jaccard）；`textqa.py::_similarity:302`/`check_duplication:321`；`knowledge_audit.py::extract_headings:78`/`similarity:112`（标题，含"包含即1.0"语义）；`images.py`（ImageAsset + 安全路径读取）；embedding 全链路；CLI `bidcraft dedup`。

**缺失**：报告实体（SimilarityReport/Hit）；项目内/跨项目数据源适配器（现只吃文件系统目录）；图片感知哈希（无 Pillow 核心依赖）；语义查重向量缓存；四维查重 API/UI/Job；统一相似度入口（现三份实现语义不同）；`compare()` 二次读盘。

**建议文件**：新增 `similarity/{text,structure,semantic,image,service}.py`（或扁平 `similarity.py` + `dedup_sources.py`）、`workers/similarity.py`、`api/routes/similarity.py`、`imagehash.py`、迁移建 `similarity_reports/hits`；改 `models.py`、`dedup.py`、`textqa.py`、`jobqueue.py`（kind + `_rebuild_args`）、`workers/__init__.py`、`api/app.py`、`pyproject.toml`（`dedup-image` 可选 extra）；前端 `SimilarityTab.tsx`/`SimilarityResult.tsx`。测试 `test_similarity_*.py`/`test_image_dedup.py`/`test_dedup_sources.py`。**查重须走 Job Queue，禁止 HTTP 线程同步比对。**

## 模块5 商务标自动编制 — 复杂度：中（含签章/多税率则高）

**已有能力**：`models.py`（BoqSnapshot/BoqQuoteVersion/QuotaItem）；`boq.py`（解析/对齐、不可变版本、多级会签或签+金额/包件/币种条件审批 `normalize_chain:768`/`applicable_stages:846`/`completed_stages:880`、税额/币种/调价/定额补价、`pending_for_user:1241`）；`export/{boq_xlsx,xlsx,docx,templates}.py`；`services/quote_service.py`；API BOQ 全套 + `/approvals/pending`。

**缺失**：完全没有「商务标」实体/汇编器/版本/审批；定稿报价版本未成为导出依据（现导出活动快照）；标准商务表格（投标函/开标一览表/资格响应/业绩/人员/偏离表）；报价-商务标一致性校验；商务标导出走 export_gate。

**建议文件**：改 `models.py`（CommercialDoc/Version）、`api/routes/projects.py`/`approvals.py`、`api/schemas.py`；新增 `commercial.py`（纯函数汇编器，只读已批准数据不生成价格）、`export/commercial_{xlsx,docx}.py`、`services/commercial_service.py`、`api/routes/commercial.py`（可选）、前端 `api/commercial.ts`/`CommercialTab.tsx`/`CommercialVersions.tsx`。**`boq.py` 不改动，只 import 复用审批链。** 测试 `test_commercial_{template,mapping,fill,validation}.py`。

## 模块6 AI Agent 编排 — 复杂度：高（限定单轮工具调用 MVP 可降为中）

**已有能力**：`generation/agent.py::ModelClient`（协议适配+重试+流式，但 `complete()` 只返回字符串、无 `tools`/`tool_calls`）；`jobqueue.py`（submit/原子认领/fencing/`step_status`/`mark_step_done`/`raise_if_aborted`/reaper）；`jobs.py` + `api/routes/jobs.py`（JobEvent + SSE 断点续传）；审批范式（章节复核状态机 + 报价审批链）；`plugins.py`（沙箱/allowlist/审计/自动停用）+ `mcp.py::TOOL_SPECS`（6 内置工具，可作为 function 定义）；`netguard.py`（SSRF 防护）。

**缺失**：Agent 循环（planner/executor/预算/终止）；模型层 tool_calls 解析；编排持久化（WorkflowRun/Step/Approval）；统一工具注册表（副作用声明/审批触发）；审批闸门（Job 无 `waiting_approval` 状态，与 reaper 租约语义冲突需显式解决）；预算熔断；轨迹查询 API；匿名 `generation/agent.py` 实为 chat client。

**建议文件**：新增 `agent/{loop,tools,policy,prompts}.py`、`workers/agent.py`、`api/routes/agents.py`、迁移建 `workflow_runs/steps/approvals`；改 `generation/agent.py`（加 `complete_with_tools`，保留 `complete()` 签名）、`models.py`、`jobqueue.py`（kind/`_rebuild_args`/`waiting_approval` 租约豁免）、`workers/__init__.py`、`config.py`（agent_* 配置）、`api/app.py`、`mcp.py`（抽 `tool_specs`）。测试 `test_agent_{registry,orchestrator,workflow,approval,recovery}.py`。**Agent 只能通过授权工具调用业务 Service，不得直接改库；离线模式 fail-closed。**

## 模块7 项目协作与版本管理 — 复杂度：中

**已有能力**：`models.py`（ProjectMember 唯一约束、Section.revision/review_status/locked_*）；`projects.py`（`save_chapter:1286` 乐观锁 CAS + 确认失效 + R18 快照、`review_chapter:1386` 四态状态机、成员 CRUD、审计游标分页）；BOQ 版本/审批链（可照搬范式）；前端 `CollabTab.tsx`/`DraftTab.tsx` 复核条与冲突处理。

**缺失**：章节正文历史表（现只有 revision 计数，无旧正文）；版本列表/diff/回滚；编辑租约（现 `locked` 是终态冻结，并发靠 CAS 事后 409）；评论/批注/@提及/审阅指派；成员能力窄（无 reviewer/approver）。

**建议文件**：新增 `SectionVersion`/`SectionComment` 模型 + 迁移、`chapter_versions.py`（snapshot/list/diff/restore，save_chapter 落库前调用）、`api/routes/collaboration.py`、前端 `api/generation.ts` 版本 API、`components/VersionDiff.tsx`/`lib/diff.ts`、`DraftTab.tsx` 版本抽屉、`CollabTab.tsx` 评论区块。测试 `test_chapter_versions.py`/`test_section_comments.py`/`test_section_locks.py`/`test_chapter_assignments.py`。**恢复须生成新版本不覆盖历史；已确认章节恢复前必须按现有机制撤回；不重复实现章节审核状态机。**

## 模块8 投标质量评估中心 — 复杂度：中（含趋势+评分覆盖闭环则中高）

**已有能力**：`rules/engine.py`（`run_checks:74`/`build_context:11`/`ledger_gate:118`/`summarize_results:133`）+ 规则包；`pipeline.py::check_project:263`（聚合核对 + 保留人工处置）；五层判定 `verdict.py::judge:1041`、`requirement_issues`；文本质量 `textqa.py`/`terms.py`；来源追溯 `provenance.py::report:176`；风险总览 `api/shared.py::_risk_overview_payload:669` + `GET /risk-overview`；`GET /requirements`；`scoring.py::analysis:107`；判定回归 `calibration.py`。

**缺失**：质量总分/评分卡（0-100、等级、维度权重）；跨维度单一报告；质量快照/趋势（`check_project` 每次 DELETE 后重插 Issue，历史只在 AuditLog）；评分项覆盖闭环（`section_hint` 未参与核对）；质量报告导出；批量质量看板。**命名陷阱**：`bidcraft/compliance.py` 是法务/账号合规，与投标质量无关；正确入口是 `rules/engine.py`+`pipeline.check_project`+`substantive.py`+`verdict.py`+`export_gate.py`。**`Risk`（招标废标条款）≠ `Issue`（运行时问题），门禁只认 Issue。**

> **状态说明（v0.28 核对，避免误读为已可用）**：上文所列的 **「质量报告导出」** 与 **「批量质量看板」** 为 **未实现 / 暂缓**。
> 已经落地的是质量评估中心本身（`evaluation/`、`POST /evaluation/run`、`POST /evaluation/run-async` 与
> `GET /evaluation`、`/evaluation/reports[...]` 的不可变快照读取与 finding 处置，见 `docs/evaluation.md`）；
> **没有报告导出端点，也没有前端导出按钮或跨项目质量看板 UI**。暂缓原因：报告快照含逐条 finding 与证据，
> 导出格式（DOCX/PDF/XLSX）与脱敏/水印口径尚未定稿；现有 `POST /projects/{ref}/export` 的门禁针对投标文件
> 正文，复用会混淆语义。待产品确定口径后再补独立导出端点与看板，在此之前不对外声称可用。

**建议文件**：新增 `evaluation/{coverage,risk,scorer,report}.py`（或 `quality.py`）、`QualitySnapshot` 模型 + 迁移、`api/routes/evaluation.py`、前端 `EvaluationTab.tsx`/`components/QualityScorecard.tsx`/`api/evaluation.ts`。权重配置化；**blocking 一票否决、质量分不抵消废标风险、不称中标概率、强制导出不受分数影响**。测试 `test_evaluation_*.py`/`test_quality_report.py`（后者含导出/看板测试，随上面两项暂缓一并推迟）。

---

## 实施计划（四批次，按任务书第十四部分）

| 批次 | 模块 | 关键顺序 | 复杂 |
|---|---|---|---|
| 第一批 核心基础 | 模块1 生成引擎 + 模块2 DOCX | 先修 PDF 漏传模板 + needs 死字段 + 补基线测试 → 评分覆盖矩阵（只读）→ DOCX Document IR/模板继承 | 高/中 |
| 第二批 生产能力 | 模块3 知识库 + 模块5 商务标 + 模块4 查重 | 先修全局库被排除缺陷 → KbDocument + 增量索引 → 商务标汇编（复用 boq）→ 四维查重 | 高/中/中 |
| 第三批 智能体协作 | 模块6 Agent + 模块7 协作 | ModelClient tool_calls → Agent 循环/审批（解决 waiting_approval 与 reaper）→ 章节版本/编辑锁 | 高/中 |
| 第四批 质量交付 | 模块8 质量评估 + 端到端 | 聚合层 + 评分卡 + 快照/趋势 + 报告导出 + 全链路 E2E | 中 |

**版本策略**：每批次独立提交、独立发版（建议 v0.24.0 起逐批），不把八模块合并成一个大版本（任务书本身建议）。

**每模块执行纪律**：先读源码 → 输出复用清单（本文件已完成）→ 最小改动 → 相关单测 → 模块测试 → 全量回归 → 独立 commit。禁止删测试/降断言/跳测试；禁止未经验证宣布完成。
