# RC-08 建议经验治理报告

## 1. 范围与基线

- 基线分支：`fix/v0.33.0-rc-reliability`，HEAD `591891c`。
- 本模块仅实施 RC-08（经验治理），未触碰其他 RC 模块文件。
- 审计基线：`learning.py` 的 allow-list / `_collect` / `lesson_items`（去重、cap 12、项目范围）/ `lessons_payload`（8 键）/ `lessons_block`；`projects.py` 的 lessons GET 与 owner-only `update_project`；`_project_payload` 的审批链回显；Web 的 `learning.ts` / `types.ts` / `QualityTab`。

## 2. 问题与契约变更

旧经验聚合只读且完全无治理：无法标记不可信/过期/撤销的条目，也看不到来源置信度。RC-08 在不做 schema 迁移的前提下，把治理状态放进 `project.meta.lesson_policy`：

- `grades`: `dict[lesson_id, "A"|"B"|"C"|"D"]`
- `trust_levels`: `dict[lesson_id, "low"|"medium"|"high"]`
- `min_trust`: `"low"|"medium"|"high"|None`
- `ttl_days`: `0–3650`（0 = 不过期，非法值归 0，超上限夹取）
- `revoked_ids`: 去重保序的字符串列表

契约变化（只新增，不删旧断言）：

1. `Lesson` 每条新增 4 键：`grade`、`trust`、`expires_at`、`revoked`。`LESSON_KEYS` 由 8 键变 12 键。
2. `lessons_payload` 新增顶层 `policy`，由 3 键变 4 键；路由响应再带 `limit`。
3. `ProjectUpdate` 新增 `LessonPolicyUpdate` 请求模型；`_project_payload` 新增 `lesson_policy` 回显。
4. 新增 1 条 owner-only 路由 `PATCH /projects/{ref}/lessons/{lesson_id}`；路由总数 `274 → 275`。
5. GET lessons 新增可选 `include_revoked=true` 查询参数（默认 false，旧调用方零变化）。

## 3. 测试先行

先新增 `tests/test_v0330_lesson_governance.py`（8 用例）并更新 `tests/test_v032_learning.py` 契约：

- 策略归一化白名单/夹取/去重；
- PATCH 往返 + 部分字段 PATCH 保留 `revoked_ids`；editor/viewer 403；
- 撤销 / 过期 / `min_trust` 在去重与 cap 之前生效；`lessons_block` 派生自过滤后的行；
- `grade/trust/expires_at/revoked` 跟随策略；
- owner-only 撤销：跨项目真实 id 404、不存在 id 404、审计 `learning.revoke`、来源行零改写；
- `include_revoked` 只把撤销项放回，过期项仍丢弃。

首次运行：6 failed / 0 passed（缺 `LESSON_ITEM_KEYS`、`normalize_lesson_policy`、撤销路由等），精确复现缺口。

## 4. 生产实现

### 4.1 `bidcraft/evaluation/learning.py`

- 新增 `LESSON_POLICY_KEY` / `LESSON_GRADES` / `LESSON_TRUST_LEVELS` / `LESSON_ITEM_KEYS` / `LESSON_POLICY_KEYS`，按来源的默认等级与信任。
- `normalize_lesson_policy(raw)` / `load_lesson_policy(project)`：统一归一化与延迟读取，永不写库。
- `lesson_source_ids(db, project)`：用真实来源行 + 本项目节点校验撤销目标，杜绝跨项目 id。
- `revoke_lesson(db, project, lesson_id)`：只把 id 追加到 `meta.lesson_policy.revoked_ids`，不触碰来源行。
- `lesson_items(..., include_revoked=False)`：先撤销/过期/最低信任过滤，再走原有节点校验、去重与 cap 12；每条附加 4 个治理字段。
- `lessons_payload(..., include_revoked=False)`：回显归一化策略；`lessons_block` 直接复用过滤后的 `lesson_items`。

### 4.2 API 层

- `schemas.py`：新增 `LessonPolicyUpdate`，`ProjectUpdate.lesson_policy` 部分字段 PATCH。
- `shared.py`：重导出请求模型；`lessons_payload` 包装透传 `include_revoked`；`_project_payload` 延迟导入 `load_lesson_policy` 并回显策略。
- `routes/projects.py`：
  - `update_project` 的 `lesson_policy` 分支：`_require_owner`，与旧策略按字段合并后归一化写入 meta，`keys` 记 `lesson_policy`；
  - `project_lessons` 增加 `include_revoked` 与审计 detail；
  - 新路由 `revoke_project_lesson`：`_require_owner` → `revoke_lesson` → `learning.revoke` 审计，404 走 `_http_error`。

### 4.3 Web

- `lib/types.ts`：`Lesson` 增 4 字段；新增 `LessonPolicy`；`LessonsResponse.policy`；`Project.lesson_policy` 与 `LessonPolicyInput`。
- `api/learning.ts`：治理字段归一化、策略归一化（含去重）、`include_revoked` 查询参数、`revokeProjectLesson()`。
- `api/project.ts`：`updateProject` 接受 `lesson_policy`。
- `QualityTab.tsx`：等级/信任/有效期/撤销徽标、策略摘要、`显示已撤销` 开关（切换 `include_revoked`）、owner/admin 才显示撤销按钮并调用新路由；失败用 toast 提示，不伪装成功。
- `styles/workspace.css`：治理徽标与撤销态样式。

## 5. 验证（实际执行）

```bash
.venv/bin/python -m pytest -q tests/test_v0330_lesson_governance.py tests/test_v032_learning.py
# 13 passed

.venv/bin/python -m pytest -q tests/test_api_package.py tests/test_batch8_contract.py \
  tests/test_v030_stage8_observability.py tests/test_v032_fact_governance.py \
  tests/test_project_duplicate.py tests/test_v032_quality_api.py tests/test_approvals_pending.py
# 62 passed（路由计数 275、请求模型 94、旧 lessons 契约保持）

.venv/bin/python -m ruff check <全部 owned Python 文件>
# All checks passed!

cd web && npm run typecheck            # 通过
cd web && npm run lint                  # 通过
cd web && npx vitest run src/api/learning.test.ts \
  src/pages/tabs/QualityTab.lessons.test.tsx src/lib/api.test.ts
# 137 passed
cd web && npm test -- --run             # 57 files / 442 passed
```

未运行全量后端套件（按任务约束）。

## 6. 兼容性与残余风险

- 无数据库迁移；治理状态完全存在 `project.meta`，旧项目读到空策略，行为与 RC-08 前一致。
- 旧后端未回传 `policy`/治理字段时，Web 归一化按空策略/空串兜底，不把建议伪装成事实。
- `include_revoked=true` 只恢复撤销项；过期与低信任条目任何视图都不返回，避免绕过 TTL/信任门禁。
- 撤销是“软标记”：来源行仍在审计与质量报告中可见；如需彻底删除需另行流程，不在本 RC 范围。
- 等级/信任默认值按来源硬编码（`quality_finding`/`audit_log` = B/medium，`section_revision`/`chapter_plan` = C/low），可被策略按 id 覆盖；后续如需企业级默认策略应另行设计。
- 前端 `BoqVersions` 与 `learning` API 的联动由各自模块负责；本模块只改 owned 文件。

## 7. 回滚

代码层按需回退：`learning.py` 的治理 helper 与过滤、`schemas.py` 的 `LessonPolicyUpdate`、`shared.py` 的回显与包装、`routes/projects.py` 的 PATCH 分支与新路由、Web 的治理 UI/类型/API，并删除本报告与 `tests/test_v0330_lesson_governance.py`、恢复 `test_v032_learning.py` 契约与两处计数断言。已写入的 `project.meta.lesson_policy` 可保留（读取端会忽略旧代码不认识的键），无需数据库回滚操作。
