# RC-03 章节规划一致性报告

## 1. 范围与基线

- 分支：`fix/v0.33.0-rc-reliability`
- 基线：RC-02 提交 `8dd27f5`（原始业务基线 `7fe741d` / v0.32.2）
- 审计来源：RC-03/04 只读审计 B1–B5
- 约束：不改历史迁移，不新增表/字段，不改变规划状态机的对外语义，不删测试、不放宽断言。

## 2. 已修复问题

### B1 已确认规划不随依赖变化失效

- `bidcraft/generation/chapter_plan.py`
  - 新增 `plan_dependency_staleness(db, plan)`：逐条比较规划保存时写入 JSON 的真实 ID 与当前
    `ScoringItem` / `Requirement` / `Fact` / `KbDocument` / `KbChunk` / `OutlineNode` 值。
    外部 API 知识库引用（无本地 document/chunk 行）不做臆测性判废，仍交给检索层信任分级。
  - 新增 `invalidate_stale_plan(db, plan)`：把依赖已变化的规划追加中文 `validation_errors`，
    并按现有状态机转为 `REJECTED`。
  - `load_active_plan()` 不再在最新有效版本之后回退旧版本：遇到最新非中间态 `REJECTED` 直接返回
    `None`，避免依赖失效后旧 `CONFIRMED` 规划继续被正文消费。
- `bidcraft/api/routes/projects.py::_chapter_plan_gate`
  - 生成前执行依赖一致性检查；失效时先提交判废状态再返回 409，防止异常回滚让规划回到
    `CONFIRMED`。
- `bidcraft/workers/generation.py::_resolve_worker_plan`
  - worker 侧同样执行依赖检查；失效时拒绝生成正文。

### B2 同步生成重读规划导致身份漂移

- `generate_chapter` / `write` 把门禁通过的规划对象冻结为 `frozen_plan` / `frozen_plans`，
  `plan_provider` 只返回该冻结对象，不再在生成会话里调用 `load_active_plan()`。
- 这样在模型调用期间即使规划行被改成 `DRAFT` 或换版本，writer 仍使用门禁时确认的同一规划。

### B3 `regenerate` 字段被忽略

- `create_chapter_plan` 与 `plan/generate` 别名现在把 `payload.regenerate` 传入
  `_generate_chapter_plan`；`plan/regenerate` 仍强制 `True`。

### B4 确认时按项目 meta 静默剔除跨标段知识引用

- `confirm_chapter_plan()` 优先使用 `plan.package_id`，再回退项目 meta。
- `approve_chapter_plan()` 不再用项目 meta 做 `load_active_plan` 包件过滤，确认以规划自身标段为准。

### B5 prompt v3 声称有 advisory 但未传经验

- `_generate_chapter_plan()` 调用 `lessons_block(db, project, node.id)`，并把结果作为
  `lessons_text` 传给 `build_plan_context()`。
- `generation/prompts.py::chapter_plan_messages()` 在存在经验时显式注入
  “历史人工调整 / 未审核建议经验（仅供参考，非核实事实）”段。

## 3. 测试

新增 `tests/test_v0330_chapter_plan_consistency.py`（8 项真实 DB/TestClient 回归）：

1. 依赖变化检测覆盖评分项分值、招标要求正文、事实取值、知识块删除。
2. 最新版本 `REJECTED` 不再回退到旧 `CONFIRMED`。
3. 已确认规划依赖变化后，`POST /projects/{ref}/chapters/{node}/generate` 返回 409，规划落库为 `REJECTED`。
4. 生成期间冻结门禁规划对象；行状态被外部改成 `DRAFT` 后 writer 仍拿到原 `CONFIRMED` 规划。
5. `POST /plan` 带 `regenerate=true` 生成版本 2。
6. 规划标段为 `pkg-1`、项目 meta 为 `pkg-2` 时，确认保留 `pkg-1` 的真实知识引用。
7. 规划生成 prompt 包含真实“未审核建议经验”段。
8. 现有 v0.32 规划测试保持通过。

实际结果：

```text
.venv/bin/python -m ruff check bidcraft tests scripts
All checks passed!

.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0330_chapter_plan_consistency.py tests/test_v032_chapter_planning.py
31 passed, 1 warning in 16.93s

.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0330_chapter_plan_consistency.py tests/test_v032_chapter_planning.py \
  tests/test_batch3_generation_fixes.py tests/test_v0312_generation_dual_fencing.py \
  tests/test_worker_reliability_matrix.py tests/test_export_review_gate.py \
  tests/test_api_jobs.py tests/test_v0330_tool_validation.py tests/test_v0330_agent_security.py
102 passed, 1 warning in 118.70s
```

唯一 warning 是 Starlette/httpx TestClient 弃用提示，与本次改动无关。

## 4. 失败与修复记录

- 首轮 5 个新测试失败：
  - 知识块删除未判废：补上 `chunk_id` 存在但本地块已删除的显式原因。
  - 409 后规划未落库为 `REJECTED`：门禁在抛 HTTP 异常前显式 `db.commit()`。
  - 冻结测试捕获了错误的 kwarg：writer 实际通过 `plan_provider` 取规划，测试改为验证 provider 返回值。
  - 标段测试生成时没有真实检索引用：改为先用真实 provider 生成带引用的规划，再走 API 确认。
  - advisory 未进入 prompt：`chapter_plan_messages()` 补上显式 advisory 段。
- 所有修复均为最小改动，未删测试、未降低断言。

## 5. 残余风险

- 依赖比较基于规划 JSON 中保存的字段；若模型输出故意省略 `score`/`criteria` 等字段，
  这些字段的变化不会触发判废，但真实 ID 删除/标题变化/知识退回未发布仍会触发。
- PG 真库并发规划一致性尚未单独跑；本阶段使用 SQLite 真库 + TestClient，最终 PG 回归在 RC-12 覆盖。
- 未新增迁移，无数据库结构变化。

## 6. 回滚

逐文件回退以下 diff 即可，无数据迁移：

- `bidcraft/generation/chapter_plan.py`
- `bidcraft/api/routes/projects.py`
- `bidcraft/workers/generation.py`
- `bidcraft/generation/prompts.py`
- 删除 `tests/test_v0330_chapter_plan_consistency.py`
