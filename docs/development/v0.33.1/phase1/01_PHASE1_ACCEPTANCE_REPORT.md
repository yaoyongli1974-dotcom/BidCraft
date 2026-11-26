# v0.33.1 Phase 1：两个 P0 残留收口报告

基线：分支 `fix/v0.33.1-phase0`，起点 `448b6e7`（Phase 0 文档提交），业务基线仍为 main `373499729a651c4d7bb69527379b0201b7ec7d1a`。本阶段只做两个 RC 遗留缺口的收口，不做新功能、不改历史迁移、不新增路由/模型。

## 1. 范围与结论

| 项 | 结论 |
|---|---|
| P1.1 普通审批链待办解释 | PASS |
| P1.2 `vector_ready` 可发布口径 | PASS |
| 新增迁移 | 无 |
| 新增 API 路由 / Pydantic 模型 | 无（仅 pending 项新增 optional 字段） |
| 前端契约 | additive-only，旧后端仍可用本地兜底 |

## 2. P1.1：所有审批链待办都返回 `approval_explain`

### 2.1 实测缺口

- `bidcraft/boq.py:1588` 与 `bidcraft/commercial.py:1580` 仅在 chain 含 `rules` 时附加 `approval_explain`，导致 `min_amount` / `packages` / `currencies` only 及空链待办不可解释。
- 精确键测试 `tests/test_approvals_pending.py:23`、`tests/test_quote_approval_condition.py:27` 锁死旧 11-key 形态。

### 2.2 生产改动

- `bidcraft/boq.py` 新增 `empty_chain_explain()`：空链返回 `index=1`、`applicable=true`、空条件、空 `missing_data`、`owner_only=true` 和中文 note；不编造审批人。
- `bidcraft/boq.py::pending_for_user`：空链附加 owner 解释；非空链无条件使用 `_stage_explain()` 生成当前可审级解释，删除 rules 门控。
- `bidcraft/commercial.py::pending_for_user`：同样复用 `boq.empty_chain_explain()` 与 `boq._stage_explain()`，删除 rules 门控。
- `approval_explain.index` 保持原链索引语义（与审批记录 `stage` 同源），不是 UI 的 1 起序号。

### 2.3 前端同步

- `web/src/lib/types.ts`：`StageApprovalExplain` 增加可选 `owner_only` / `note`；`KnowledgeBase` 增加 `vector_excluded`。
- `web/src/api/shared.ts::normalizePendingApproval`：解析对象/数组两种 explain 形态，保留条件、未知数据、owner-only 与 note，坏值防御性丢弃。
- `web/src/components/PendingApprovals.tsx`：待办项渲染当前级命中条件数或空链 owner 说明；新增 `.pending-item-explain` 样式。
- `web/src/api/project.ts` 的版本详情 normalizer 与本地镜像保持不变，pending 以后端解释为准。

### 2.4 测试

- 更新 `PENDING_KEYS` 为含 `approval_explain` 的 12-key 契约（不是删除断言）。
- `tests/test_approvals_pending.py`：空链 payload shape 断言 owner 说明；新增 `test_legacy_condition_chain_pending_explains_min_amount`。
- `tests/test_commercial_approval.py`：新增 `test_commercial_legacy_chain_pending_has_explain`，验证商务标普通条件链。
- `web/src/api/pendingApproval.test.ts`：对象形态 explain 保留 owner_only/note。
- `web/src/components/PendingApprovals.tsx` 对应组件测试断言渲染 `当前级命中 1/1 项条件`。

测试先行结果：`5 failed, 31 passed`；修复后定向 `36 passed, 1 warning`。

## 3. P1.2：`vector_ready` 只统计可发布块

### 3.1 实测缺口

- `bidcraft/knowledge.py:122` 的 `vector_total = kb.chunk_count` 计入 draft/review/rejected 块。
- `build_vectors()` 通过 `releasable_review_filter()` 跳过这些块，导致全部可发布块都已嵌入时 UI 仍显示“向量待更新”。

### 3.2 生产改动

- `bidcraft/knowledge.py::_vector_fields`：
  - `vector_total` 改为按 `KbChunk JOIN KbDocument` + `releasable_review_filter()` 统计；
  - `vector_embedded` 同口径且限制当前 `embed_key`；
  - 新增 `vector_excluded = 全部块 - 可发布块`（下限 0）；
  - `vector_dim` 也只取可发布块；
  - `kb.chunk_count` 保持原始入库统计，不被改写；
  - `vector_ready` / `vector_stale` 仍只在 `total > 0` 时给出，不改变旧字段语义。
- `web/src/lib/format.ts::kbVectorMeta`：展示未发布排除数，例如 `语义就绪（未发布 2 不计入）`；旧后端无该字段时不显示后缀。

### 3.3 测试

- 新增 `tests/test_v0331_vector_readiness.py`：构造 published / legacy(NULL) / draft / rejected 四类文档；断言 total=2、embedded=2、excluded=2、ready=true、stale=false；再把 legacy 块改成旧 embed_key，断言 stale=true；断言 `kb.chunk_count` 未被改写。
- 新增 `web/src/lib/format.test.ts` 用例，断言排除后缀。

测试先行结果包含该用例失败；修复后定向 `36 passed, 1 warning`。

## 4. 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m ruff check bidcraft tests scripts` | All checks passed |
| `git diff --check` | clean |
| `.venv/bin/python -m pytest -o addopts= -q tests/test_v0331_vector_readiness.py tests/test_approvals_pending.py tests/test_quote_approval_condition.py tests/test_commercial_approval.py` | 36 passed, 1 warning |
| 11 文件相邻回归（含 v0330 explain、knowledge vector、alembic 无关契约、api_package） | 97 passed, 1 warning |
| `cd web && npm run typecheck && npm run lint && npm test -- --run` | typecheck/lint pass；58 files / 454 tests passed |
| 全量后端回归 | `.venv/bin/python -m pytest -o addopts= -q`：**2367 passed / 1008 skipped / 1 warning in 977.08s**，零失败；输出 `/tmp/opencode/v0331_phase1_full.out` |

前端 `ErrorBoundary.test.tsx` 输出预期异常堆栈，不是失败。API 路由仍为 275，Pydantic 请求模型仍为 94；Alembic 单 head 仍为 `0015_fact_confirmation`。

## 5. 失败与修复记录

1. 新增测试首轮出现 5 个失败，证明两个缺陷真实存在；没有删除或降低断言。
2. 解释类测试最初把 `approval_explain.index` 写成 UI 序号 1，实测后端返回原链索引 0；按“与审批记录 stage 同源”的既有契约修正测试为 0，语义未削弱。
3. 向量测试误用 `knowledge.update`，改为显式 `from sqlalchemy import update`。
4. 商业标新测试首次插入位置错误导致缩进异常，已恢复原文件后追加到文件末尾。
5. 前端组件新增渲染后，全部 Web 测试通过；没有用 mock 代替真实后端契约测试。

## 6. 残余风险

- `approval_explain` 是 additive optional 字段；旧前端不会因为缺字段崩溃，但旧后端仍只能本地镜像解释，需后续部署同步。
- `vector_excluded` 依赖 `kb.chunk_count` 之外的真实 KbChunk/KbDocument 行；历史脏数据（chunk 无 document_id）按 NULL review_status 视为可发布，与 `build_vectors` 保持一致。
- 未改变导出门禁、权限、审批状态机或退款归属；无生产数据修改。

## 7. 回滚

按需回退本阶段文件：`bidcraft/boq.py`、`bidcraft/commercial.py`、`bidcraft/knowledge.py`、上述测试与前端 5 个文件；无迁移回滚，无数据回填。
