# v0.32 Phase 1 章节规划引擎报告

## 1. 范围与基线

- 分支：`feature/v0.32-ai-engine`
- 基线：`42c38ac`（Phase 0 报告，源自 main `883a679`）
- 目标：为每一章提供可版本化、可确认、可追溯的规划，并把规划关联到正文生成记录。
- 不改动范围：无旧版本 bump、无历史迁移编辑、无测试删除/断言放宽、无生产部署。

## 2. 实现清单

| 编号 | 项 | 实现 | 证据 |
|---|---|---|---|
| 1 | 规划模型 | `bidcraft/models.py::ChapterPlan`：31 个字段带中文 `comment=`，唯一约束 `(project_id,node_id,version)`，索引 `(project_id,node_id,status)` 与 `(project_id,package_id)` | `tests/test_v032_chapter_planning.py::test_migration_created_plan_table_with_unique_and_indexes` |
| 2 | 可回滚迁移 | 新增 `bidcraft/migrations/versions/0013_chapter_planning.py`，down `0012_generation_lease`，inspect 守卫幂等创建/删除表及索引，未编辑历史迁移 | `tests/test_alembic.py::test_0013_chapter_planning_table_is_reversible` |
| 3 | 状态机 | `chapter_plan.py` 定义 NOT_CREATED/GENERATING/DRAFT/REVIEW_REQUIRED/CONFIRMED/USED_FOR_GENERATION/REGENERATING/UPDATED/REJECTED 与合法转移表 | `test_confirm_state_machine_and_idempotent_mark_used` |
| 4 | 真实 ID 校验 | `validate_chapter_plan()` 对评分点、需求、事实、知识、章节 ID 做真实 DB 校验；伪造/跨项目/跨包件引用被丢弃并写入 `validation_errors` | `test_generate_plan_accepts_only_real_ids`、`test_model_output_invalid_ids_are_dropped_and_recorded`、`test_plan_history_and_cross_scope_rejection` |
| 5 | 版本更新 | `update_chapter_plan()` 追加 UPDATED 新版本并使旧确认失效；`regenerate` 追加新版本 | `test_update_adds_version_and_invalidates_previous`、`test_regenerate_selects_latest_and_excludes_rejected` |
| 6 | 规划生成 | `generate_chapter_plan()` 在无模型端点时使用真实项目数据的确定性回退；模型输出解析后同样经过真实 ID 过滤；原文保留在 `raw_response` | `test_generate_plan_accepts_only_real_ids` 等 |
| 7 | 规划门禁 | `projects.py::_chapter_plan_gate()`：review 模式对已有规划章节要求确认；review_strict 对无规划新章节也阻断；auto 放行但记录 warning | `test_review_mode_blocks_generation_without_confirmed_plan`、`test_review_strict_mode_blocks_chapter_without_any_plan`、`test_auto_mode_allows_generation_with_warning` |
| 8 | 正文关联 | 同步路径在审计 detail 写 planning_id/version/status/引用 IDs/strategy/prompt_version；异步路径写入 `Job.payload`，worker 在 fenced chapter commit 中幂等标记 `USED_FOR_GENERATION` | `test_generation_records_plan_version_in_audit_and_marks_used`、`test_async_generation_job_carries_plan_metadata` |
| 9 | Prompt 注入 | `prompts.py::build_chapter_context/chapter_messages` 与 `writer.py::generate_document` 支持 `chapter_plan/plan_text`，规划策略进入正文 prompt | `test_plan_context_text_contains_strategy_and_real_ids`、`test_chapter_messages_embed_plan_strategy` |
| 10 | API | 7 条新路由：`GET/POST/POST generate/POST regenerate/PUT/POST confirm/GET history`，均在 `/projects/{ref}/chapters/{node_id}/plan*`；带鉴权、写权限与审计 | `test_api_generate_get_confirm_and_history`、`test_api_generate_alias_and_wrapped_updates`、`test_api_plan_rejects_foreign_node_and_cross_project`、`test_api_package.py` |
| 11 | 前端工作台 | 新增 `ChapterPlanningPanel`，在既有 OutlineTab 中展示目标/评分点/需求/条件/难点/策略/措施/知识引用/事实引用/计划表格/图示/禁用假设/质量准则/校验错误/历史；生成、重生成、编辑保存、确认、刷新；权限与只读门禁；DraftTab/OutlineTab 正文生成受模式门禁约束 | `web/src/api/generation.planning.test.ts`、`web/src/pages/tabs/OutlineTab.planning.test.tsx`；typecheck/lint/340 tests |
| 12 | 契约对齐 | 后端补充 `POST /plan/generate` 别名、`package_id`、`updates` 包装字段，前端契约无需 404 回退（兼容保留） | `test_api_generate_alias_and_wrapped_updates` |

## 3. 关键文件和函数

- 服务：`bidcraft/generation/chapter_plan.py`
  - `chapter_plan_payload`, `load_active_plan`, `load_plan_history`
  - `build_plan_context`, `generate_chapter_plan`, `validate_chapter_plan`
  - `update_chapter_plan`, `confirm_chapter_plan`, `mark_plan_used`, `plan_context_text`
- 路由：`bidcraft/api/routes/projects.py`
  - `_planning_mode`, `_plan_metadata`, `_chapter_plan_gate`
  - `get_chapter_plan`, `create_chapter_plan`, `create_chapter_plan_alias`, `regenerate_chapter_plan`, `modify_chapter_plan`, `approve_chapter_plan`, `chapter_plan_history`
- Prompt/生成：`bidcraft/generation/prompts.py`, `bidcraft/generation/writer.py`, `bidcraft/workers/generation.py`
- 前端：`web/src/components/ChapterPlanningPanel.tsx`, `web/src/hooks/useChapterPlans.ts`, `web/src/lib/chapterPlan.ts`, `web/src/api/generation.ts`, `web/src/pages/tabs/{OutlineTab,DraftTab}.tsx`

## 4. 模式与兼容性决策

- 默认模式 `review`：一章一旦生成过规划，必须先确认才能写正文。
- 历史章节从未规划时，默认 `review` 视为旧章节，放行并记录 warning，避免既有项目全面阻断；新增 `review_strict` 模式可对无规划章节强制阻断。
- `auto` 模式放行但记录 warning，适合批处理与向后兼容。
- 模型不可用时使用确定性回退并在 `raw_response` 明确记录，不伪造模型结论。

## 5. 验证

- `.venv/bin/python -m ruff check bidcraft tests scripts`：All checks passed
- `git diff --check`：通过
- 目标后端测试（含 Phase 1、Alembic、API package、正文生成/评审/计费/性能回归）：**77 passed, 1 warning**（仅 Starlette/httpx deprecation）
- 并发/ fencing 回归：76 passed, 1 skipped（PG opt-in）
- Web：`npm run typecheck`、`npm run lint`、`npm test -- --run` → 41 files / 340 tests passed；`npm run build` 通过（Vite 8）
- 全量后端 pytest：`.venv/bin/python -m pytest -o addopts="" -q` → **2145 passed, 886 skipped, 1 warning in 807.83s**，零失败（仅 Starlette/httpx deprecation）。

## 6. 失败与修复记录

- 后端代理首轮调用因上游路由错误失败，未产生文件改动；重启后完成。
- 前后端契约初版不一致：后端首次生成在 `POST /plan`，更新为扁平字段；前端按文档契约先请求 `POST /plan/generate` 且带 `updates` 包装。已通过后端别名与双形态兼容收口，并新增测试防止回归。
- 收口期间误插入重复 docstring 导致 `projects.py` 语法错误；已立即修复，`py_compile`、ruff、目标测试均通过。
- 全量回归暴露两处契约型测试未同步：
  1. `tests/test_batch8_contract.py` 把 `/plan/{generate,regenerate,confirm,history}` 当作根命名空间；已把 4 个路径后缀加入 `FRONTEND_NON_API` 明确白名单（不是放宽断言）。
  2. `tests/test_v030_stage8_observability.py` 仍断言 252 条路由；7 条章节规划路由使总数变为 259，已更新期望值并同步 docstring。
  修正后 19 passed；该阶段完整重跑为 **2145 passed**（见 `10_TEST_REPORT.md` 阶段增量表）。

## 7. 残留风险

- 真实模型端点未配置，模型 JSON 生成质量与精心 prompt 效果 **NOT VERIFIED**；当前验证覆盖确定性回退与模型输出解析/ID 校验。
- 0013 迁移仅在 SQLite 验证可逆；PG 路径按现有幂等风格编写但本地无 PG 服务，留待后续阶段/CI 验证。
- 默认 `review` 对历史无规划章节兼容放行；仅 `review_strict` 强制新章节无规划阻断。
- 前端在大量章节时会对每章请求一次规划（有缓存/去重），超大目录的批量性能仍需后续观测。

## 8. 回滚

1. 代码：revert 本阶段列出的 modified 文件，删除新增 `chapter_plan.py`、`0013_chapter_planning.py`、`test_v032_chapter_planning.py` 与前端新增文件；`tests/test_api_package.py` 路由数 259→252、schema 91→88。
2. 数据库：`alembic downgrade 0012_generation_lease` 删除 `chapter_plans` 及索引；无历史数据回填需求，规划数据随表删除。
3. 前端：仅还原本阶段列出的 web/src 文件。

@3263@