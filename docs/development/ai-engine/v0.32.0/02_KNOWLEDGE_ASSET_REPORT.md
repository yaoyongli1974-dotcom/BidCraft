# v0.32 Phase 2 知识资产引擎报告

## 1. 范围与基线

- 分支：`feature/v0.32-ai-engine`
- 基线：`42d00b8`（Phase 1）
- 目标：在既有知识库上增加结构化知识资产、审核生命周期、溯源与重复候选处理，不建第二套文库。
- 不变：不弱化检索门禁、不新建旁路文库、不改导出/风险门禁。

## 2. 实现清单

| 编号 | 项 | 实现 | 证据 |
|---|---|---|---|
| 1 | 结构化资产字段 | `KbDocument` 新增 7 个可空中文字段注释列：`structured/review_status/reviewed_by/reviewed_at/published_at/rejected_reason/source_file_id` | `tests/test_kb_assets_v032.py` |
| 2 | 可回滚迁移 | 新增 `0014_kb_asset_metadata.py`，down `0013_chapter_planning`，inspect 守卫幂等；新增两张重复组表；旧行 `review_status=NULL` 作为 legacy 语义 | `tests/test_alembic.py::test_0014_kb_asset_metadata_is_reversible` |
| 3 | 审核状态机 | `knowledge_assets.py` 定义 draft/review/approved/published/rejected/archived 与合法跃迁；非法跃迁 ValueError，不写库 | `test_review_state_machine_and_illegal_transition` |
| 4 | 结构化抽取 | `propose_document_assets()` 从真实 `KbChunk` 文本按固定正则提取标题/证号/合同号/金额/有效期/业主/来源 URL/备注；无模型、同输入同输出；保存到 `structured` 并置 draft | `test_propose_document_assets_uses_real_chunks` |
| 5 | 真实溯源 | `asset_payload()` 返回 `document_id`、`chunks[].id/page/heading/chunk_index/source`，并把结构化字段合并到素材可信度裁决；不产生虚构 ID | `test_asset_payload_trace_and_assessment` |
| 6 | 检索门禁 | `knowledge._ready_chunk_filter()` 只放行 legacy(NULL)/published 文档块；draft/review/approved/rejected/archived 不进入检索 | `test_retrieval_gate_only_allows_legacy_or_published` |
| 7 | 重复候选 | `find_duplicate_candidates()` 按同库内 sha256 与规范化题录/证号聚合；`resolve_duplicate_group()` 支持 resolve/dismiss 幂等裁决 | `test_duplicate_candidates_and_idempotent_resolution` |
| 8 | 素材策略合并 | `material_policy._document_meta` 优先读取结构化字段，兼容旧 `options['docs']`；过期/缺失仍按现有策略裁决 | `test_policy_merges_structured_fields` |
| 9 | 管理端 API | 5 条 `/admin/kbs/{kb_id}/assets*` 路由：list/duplicates/resolve/extract/review；全部 require_super；静态 duplicates 路由先于动态 document_id；审计 `kb.document_extract/review`、`kb.duplicate_resolve` | `tests/test_kb_assets_api_v032.py`（15 tests） |
| 10 | 企业端 API | 5 条 `/enterprise/{enterprise_id}/assets*` 路由：list/duplicates/resolve/extract/review；读用 accessible enterprise + container KB，写用 owner；调用方 `kb_id` 永不信任；审计 `enterprise.*` | 同上（owner/member/outsider/cross-enterprise） |
| 11 | 前端工作台 | 新增 `KnowledgeAssetPanel`，挂在 AdminKnowledgeTab 与企业 EnterpriseTab；展示源/字段/状态/溯源/重复组/裁决与审核按钮；权限 `canWrite && selected.is_owner`；动作由服务端结果驱动 | `knowledgeAssets.test.ts`、`knowledgeAssetMeta.test.ts`、`KnowledgeAssetPanel.test.tsx`、EnterpriseTab 测试 |
| 12 | 契约同步 | `test_api_package.py` 路由 259→269、schema 91→93；`test_v030_stage8_observability.py` 路由断言同步；`apiSurface.test.ts` 加入新字面量 | 58 targeted passed；web 364 tests |

## 3. 关键文件

- 服务：`bidcraft/knowledge_assets.py`
- 模型/迁移：`bidcraft/models.py`、`bidcraft/migrations/versions/0014_kb_asset_metadata.py`
- 检索/策略最小改动：`bidcraft/knowledge.py::_ready_chunk_filter`、`bidcraft/material_policy.py::_document_meta`
- API：`bidcraft/api/schemas.py`、`bidcraft/api/shared.py`、`bidcraft/api/routes/admin.py`、`bidcraft/api/routes/enterprise.py`
- 前端：`web/src/components/KnowledgeAssetPanel.tsx`、`web/src/lib/knowledgeAssetMeta.ts`、`web/src/api/{knowledge,enterprise}.ts`、`web/src/pages/admin/AdminKnowledgeTab.tsx`、`web/src/pages/tabs/EnterpriseTab.tsx`

## 4. 权限与审计

- 管理端全部 `require_super`；企业端读取要求企业可访问，写操作要求企业 owner（member 403、outsider 404）。
- KB 始终由企业容器解析，不接受请求体中的 `kb_id`；跨企业文档访问必须失败。
- 所有 extract/review/duplicate resolve 动作写审计。`decision`/`document_id`/`group_id`/`actor` 进入 detail。
- legacy 文档（review_status NULL）保持原有检索行为，不会因为迁移被误挡。

## 5. 验证

- `.venv/bin/python -m ruff check bidcraft tests scripts`：All checks passed；`git diff --check` clean。
- 后端目标测试（知识资产服务 + API + Alembic + API package + batch8 contract + route count）：**58 passed, 1 warning**（仅 Starlette/httpx deprecation）。
- Web：typecheck/lint pass；`npm test -- --run` → 44 files / 364 tests passed；`npm run build` pass（Vite 8，1715 modules）。
- 全量后端 pytest：`.venv/bin/python -m pytest -o addopts="" -q` → **2174 passed, 886 skipped, 1 warning in 1656.23s**，零失败（仅 Starlette/httpx deprecation）。

## 6. 失败与风险记录

- API agent 在共享工作区遇到并发/超时导致的历史 lastfailed 噪声；其权威结果为目标 34 passed。主会话在 agents 全部结束后重新执行统一验证，58 passed。
- 资产字段复用 `KbDocument.structured`，没有创建第二套文库；`source_file_id` 暂无独立 setter，重新抽取已发布资产会回到 draft，需人工重新审核（刻意保守）。
- 当前结构化抽取是确定性正则，不是模型抽取；对复杂版式/扫描件的字段召回仍需真实文件验证。
- 重复候选仅同库内聚合，跨企业去重尚未启用（避免越权暴露）。

## 7. 回滚

1. 代码：revert Phase 2 列出的 modified 文件，删除新增 `knowledge_assets.py`、0014 迁移、`test_kb_assets_v032.py`、`test_kb_assets_api_v032.py` 与前端新增面板/测试。
2. 路由/schema 断言回到 259/91。
3. 数据库：`alembic downgrade 0013_chapter_planning` 删除 KbDocument 新列与两张重复表；不会删除原始 KB 文档/块。
4. 已发布资产的结构化字段会随新列删除；无其它业务数据依赖。

@3324@