# v0.32 Phase 5 经验回流与事实治理报告

## 1. 范围与基线

- 分支：`feature/v0.32-ai-engine`
- 基线：`afe5dba`（Phase 4）
- 目标：把已处置质量问题、人工修订、审批与导出强制放行记录变成可溯源、去重、受限的“建议经验”，回流到规划/生成 prompt；同时给事实确认补齐执行人、时间、证据来源、覆盖保护与失效联动。
- 约束：不改历史迁移、不放宽导出/评审门禁、经验不得伪造、不自动写入知识库或企业资产。

## 2. 实现证据

| 编号 | 项 | 实现 | 证据 |
|---|---|---|---|
| 1 | 经验模块 | NEW `evaluation/learning.py::lesson_items/lessons_payload/lessons_block`：上限 12、去重、真实 ID 标签，来源限定本项目 handled QualityFinding（含 resolution_note/by/at）、SectionRevision(edit)、AuditLog 评审/导出强制、ChapterPlan confirmed/used/rejected 的 user_adjustments | `tests/test_v032_learning.py` |
| 2 | 经验口径 | 文案明确“未审核建议经验 / ADVISORY”，不写成已确认事实或法规；无自动 KB/enterprise 写入；`node_id` 用真实 OutlineNode 过滤；来源 ID 不存在即不生成 | `test_lesson_items_are_project_scoped_and_advisory`、`test_lessons_block_never_writes_kb` |
| 3 | 规划/正文回流 | `writer.generate_document` 在 facts_text 后追加 advisory 段；`chapter_plan.build_plan_context(..., lessons_text='')`，`plan_context_text` 渲染 user_adjustments/lessons；prompt 版本 `chapter_plan_v3` | `test_plan_context_renders_lessons_as_advisory`、`test_v032_chapter_planning.py` |
| 4 | 处置继承 | `evaluation/report.py` 按稳定 key 继承 prior dispositions（handled/ignored/resolved、note、actor）；证据 `disposition_from_finding_id` 可审计，不丢失人工处置 | `tests/test_batch7_quality.py` 扩展 |
| 5 | 人工修改率 | `quality_metrics.manual_edit_rate` 只计 `SectionRevision.change_type='edit'`；legacy revision>1 回退保留兼容，不把 restore 算作人工编辑 | `tests/test_v030_stage6_metrics.py` 扩展 |
| 6 | 迁移 | NEW `0015_fact_confirmation`（down `0014_kb_asset_metadata`）：facts.confirmed_by VARCHAR(80)、confirmed_at DATETIME，中文注释、可空无默认、inspect 守卫、可逆；`tests/test_alembic.py` HEAD 0015 + NULL legacy + no-default PRAGMA | `test_0015_fact_confirmation_is_reversible_and_nullable` |
| 7 | 事实确认真实性 | `ledger.update_fact(..., actor=None, now=None, force=False)` 服务端写 confirmed_by/at；confirmed/manual 必须有真实 SourceFile + 文本内含 source_quote + 合法页码；旧证据复用有边界；写角色权限不变 | `test_confirmation_requires_real_evidence_and_stamps_actor` |
| 8 | 覆盖保护 | `save_ledger(..., force=False)` 保留 confirmed/manual；force 覆盖时清空确认并在同一事务记 `fact.update` 审计；CLI 传 actor='cli'，审计进业务服务而非仅 HTTP 路由 | `test_force_overwrite_audited_and_clears_confirmation`、`test_cli_confirmation_audited` |
| 9 | 失效联动 | 事实值/单位变化时，引用该事实的章节 Section.review_status 回 `review`，清确认戳，同事务写 `fact.invalidation`；导出门禁仍阻断，未新增绕过 | `test_fact_change_invalidates_citing_sections` |
| 10 | 来源回写 | `save_ledger` 按真实 hit 关联 SourceFile.id；`load_ledger` 输出真实文件名/ID，不再总是空来源；FactItem/PATCH response 暴露 confirmed_by/at/source_* | `test_source_file_roundtrip` |
| 11 | 变更说明 | `ChapterSave.change_note` ≤500 进入 SectionRevision 与审计 detail（上一阶段 notes 恒空的问题） | `tests/test_v032_fact_governance.py` |
| 12 | 经验 API/UI | NEW viewer-safe `GET /projects/{ref}/lessons?node_id=&limit=`（审计 `learning.read`，路由 271→272）；前端 QualityTab 只读经验面板 + resolved_at/by；LedgerTab 事实确认人/时间/来源列、证据输入、默认 manual、真实 stale 警告；CheckTab 只读处置 | `tests/test_v032_fact_governance.py`、55 files / 414 web tests |
| 13 | 防伪造契约 | 前端 normalizer 对 null 不补 confirmed/verified；updateFact 只发 value/status/source_*，不前端自报确认人/时间；apiSurface/outboundContract 同步严格 body-key 断言 | web `outboundContract.test.ts` |
| 14 | 边界明确 | 冲突状态多候选表/字段与来源权威排序需额外 schema，本阶段 **不实现**；不引入企业/标段新维度（一项目一标段口径） | 报告 §6 |

## 3. 关键文件

- 经验：`bidcraft/evaluation/learning.py`、`evaluation/report.py`、`evaluation/quality_metrics.py`、`generation/writer.py`、`generation/chapter_plan.py`
- 事实：`bidcraft/ledger.py`、`models.py`、`migrations/versions/0015_fact_confirmation.py`、`db.py`、`cli.py`
- API：`bidcraft/api/routes/projects.py`、`api/schemas.py`、`api/shared.py`
- 前端：`web/src/api/{ledger,learning}.ts`、`web/src/pages/tabs/{LedgerTab,QualityTab,CheckTab}.tsx`、`web/src/lib/types.ts`

## 4. 权限与审计

- 经验查询 viewer 安全，写经验无入口；只有服务端代码从真实记录推导。
- 事实确认沿用既有 `_require_write`，未新增 owner 权限规则；确认人/时间由服务端写，客户端无法伪造。
- 所有事实更新、强制覆盖、失效联动、CLI 确认、经验读取均有 AuditLog，事实/章节/项目 ID 可追溯。

## 5. 验证

- `ruff check bidcraft tests scripts`：All checks passed；`git diff --check`：通过
- 后端目标测试（新经验/事实/迁移/API package/路由/契约/度量/处置/规划校准）：**104 passed, 1 warning**（仅 Starlette/httpx deprecation）
- Agent 分层验证：learning 46+30+21 passed；fact governance 137 passed
- Web：typecheck ✓、lint ✓、`npm test -- --run` → **55 files / 414 tests passed**、build ✓（1720 modules）
- Alembic 单 head：`0015_fact_confirmation`
- 全量后端 pytest：`.venv/bin/python -m pytest -o addopts="" -q` → **2241 passed, 886 skipped, 1 warning in 1749.73s**，零失败（仅 Starlette/httpx deprecation）。

## 6. 残留风险 / 明确边界

- `QualityFinding` 没有 `resolved_at` 列，处置时间存 evidence JSON；前端从 payload/evidence 映射，无 schema 变更。
- `chapter.reject` 目前只有读取端无生产者；相关经验规则已实现但生产事件尚待工作流补齐。
- legacy `manual_edit_rate` 回退条件 `revision>1` 保留，旧数据可能把非 edit 修订算作人工编辑；新数据口径已正确。
- 冲突状态多候选检测、来源权威排序、企业/标段维度未实现（需额外 schema/权限模型，按约束不做）。
- 学习内容是建议，不是已审核知识；复核责任仍在人，不得据此替代投标事实证据。

## 7. 回滚

1. 代码：删除 `evaluation/learning.py`、`tests/test_v032_learning.py`、`tests/test_v032_fact_governance.py`、`web/src/api/learning.ts`；revert 本阶段 modified 文件。
2. 数据库：`alembic downgrade 0014_kb_asset_metadata` 删除两个 nullable 确认列；业务数据中的确认信息随列删除，无需历史回填。
3. API 路由 272→271；`tests/test_api_package.py`/`test_v030_stage8_observability.py` 同步回退；前端重建 web_dist。
