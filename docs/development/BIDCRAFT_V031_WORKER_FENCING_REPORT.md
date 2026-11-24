# BidCraft v0.31.0 阶段 1：Worker fencing 事务级原子提交报告

## 1. 阶段目标与结论

消除“独立 Session 检查 token → 另一 Session 提交业务结果”的 TOCTOU 窗口，使查重 worker 的最终报告、命中、审计与 Job 终态在**同一事务**内受当前 claim token 保护。阶段 1 **完成并通过本地 SQLite 验证**；真实 PostgreSQL 校验以 opt-in 测试形式提供，需 CI 的 PG job 运行。

## 2. 修改文件与函数

| 文件 | 修改 |
|---|---|
| `bidcraft/jobqueue.py` | 新增 `commit_fenced_result(job_id)` 上下文管理器；导入 `or_`。 |
| `bidcraft/similarity/service.py` | 新增 `prepare_similarity_result(...)`（纯计算，支持 `abort_check` 回调）与 `persist_similarity_result(...)`（事务内落库）；`run_similarity()` 改为两者薄封装，保持同步路径行为。 |
| `bidcraft/workers/similarity.py` | `_run_similarity_job()` 改为“计算会话 + `commit_fenced_result` 最终会话”；计算前后及每个维度加入 `raise_if_aborted()` 检查点；移除旧的 `assert_claim_current` + fresh-session 两段式写法。 |
| `tests/test_v030_stage4_similarity.py` | 两个旧 fence 测试按新协议更新为更强的断言：租约丢失时报告/命中/审计**全部不落库**（旧实现只保证报告提交后 Job 终态不写）。未删除测试、未降低断言。 |
| `tests/test_worker_fencing_atomicity.py` | 新增 18 条测试（含 100 轮固定种子并发提交、opt-in PG 场景）。 |
| 本报告 | 11 项验收证据。 |

## 3. `commit_fenced_result` 协议

```python
with jobqueue.commit_fenced_result(job_id) as (db, job):
    # 所有最终业务写入必须发生在 with 内
    ...
```

- 从 `_claim_token` ContextVar 取当前 token；缺失直接 `JobAborted`。
- 进入时调用 `raise_if_aborted()`（取消/关闭信号）。
- 在 `session_scope()` 的**同一事务**内执行条件 UPDATE：
  `WHERE id=? AND status='running' AND claim_token=? AND (lease_expires_at IS NULL OR lease_expires_at >= now)`，
  要求 `rowcount==1`。PG 下该 UPDATE 持有行锁至提交；SQLite 下由单写事务串行化，无 PG 专有语法。
- 校验成功后 `yield (db, job)`；`session_scope()` 统一提交或整体回滚。
- 零行命中（旧 token、租约过期、被 reaper 回收、cancelled/failed/done）抛 `JobAborted`，不产生任何业务写入。

## 4. 11 项验收证据

| # | 验收项 | 证据 |
|---|---|---|
| 1 | 正常 Worker 提交成功 | `test_normal_owner_commit_succeeds`：合法 token 将 Job 写为 `done` 并保存 `result`。 |
| 2 | 旧 token 提交被拒绝 | `test_old_token_commit_rejected`：DB 中 token 已更新，旧 token 提交抛 `JobAborted`，状态/结果不变。 |
| 3 | 租约过期后同 token 被拒绝 | `test_expired_lease_same_token_commit_rejected`：条件 UPDATE 的 lease 条件生效。 |
| 4 | 新 Worker 领取后旧 Worker 被拒绝 | `test_reclaim_then_old_worker_cannot_commit`：reaper 回收 → 新 token 认领 → 旧 token 提交失败、新 token 提交成功。 |
| 5 | 旧/新 Worker 并发提交只有合法者成功 | `test_old_and_new_worker_concurrent_only_owner_wins`：线程 Barrier 同步，断言 `{'old': False, 'new': True}`。 |
| 6 | 终态任务拒绝提交 | `test_terminal_states_reject_commit[failed/cancelled/done]`：三种终态均 `JobAborted`。 |
| 7 | 业务写入中途异常整体回滚 | `test_business_exception_rolls_back_whole_unit`：写入 SimilarityReport 后抛错，库中无残留行、Job 仍 `running`。 |
| 8 | 数据库 commit 失败无部分结果 | `test_commit_failure_leaves_no_partial_result`：monkeypatch `session.commit` 抛错，`session_scope` 回滚，无报告残留。 |
| 9 | Worker 崩溃后 Reaper 安全恢复 | `test_crashed_worker_job_is_safely_reclaimed_and_committed`：过期租约 → requeue → 新认领 → 成功提交。 |
| 10 | 100 轮并发提交（固定种子） | `test_100_rounds_old_vs_new_concurrent_commit`：`random.seed(20261124)`，100 轮均只有新 token 成功，无线程挂起/异常。 |
| 11 | PostgreSQL 与 SQLite 兼容 | `prepare/persist` 与 `commit_fenced_result` 仅用条件 UPDATE、`or_` 和 `session_scope`；`test_postgresql_atomic_fencing_optin` 在设置 `BIDCRAFT_TEST_PG_URL` 时验证旧 token/合法 token 场景并在结束时复位引擎。本地无 PG，CI 覆盖。 |

## 5. 业务与兼容性影响

- **数据库**：无新增字段、无新增索引、无新增 Alembic migration；未改动历史 migration。
- **API**：请求/响应结构与业务语义不变；查重报告状态、统计字段、审计 detail 均保持原样。
- **前端**：无改动；`SimilarityTab` 继续按既有轮询终态展示。
- **重试语义增强**：旧实现可能在提交前已写入报告，重试靠 `report_id` 幂等；新实现让报告/命中/审计与 Job 终态同事务提交，旧 worker 不再留下任何部分业务结果。done 报告复用仍保持原行为。

## 6. 验证结果

- `ruff check bidcraft tests` → All checks passed（未运行 ruff format）。
- `pytest tests/test_worker_fencing_atomicity.py tests/test_v030_stage4_similarity.py` → **18 passed, 1 skipped**（PG opt-in 本地跳过）。
- 扩展回归（fencing / similarity / jobqueue / reliability）：`tests/test_worker_fencing_atomicity.py tests/test_v030_stage4_fencing.py tests/test_v030_stage4_similarity.py tests/test_jobqueue.py tests/test_reliability.py tests/test_reliability_writer.py tests/test_similarity_api.py tests/test_similarity_text.py tests/test_similarity_structure.py tests/test_similarity_semantic.py tests/test_similarity_image.py` → **89 passed, 1 skipped**（仅 Starlette/httpx 弃用警告）。
- 未运行全量后端套件（按任务书留待阶段 9）。

## 7. 残留风险与限制

1. 本地无真实 PostgreSQL 服务，PG 并发路径依赖 CI `postgres` job 执行 opt-in 测试。
2. 本阶段只改造 similarity worker 的最终提交；generation/export/agent 等 worker 的通用检查点属于阶段 4 范围。
3. `commit_fenced_result` 要求调用方在 `claim_context` / `execution_context` 内运行；直接调用 runner 的旧测试若未绑定 token 会收到 `JobAborted`（这是预期的安全拒绝）。
4. 条件 UPDATE 会顺带刷新 `jobs.updated_at`，对 `_cancel_stale_jobs` 的 staleness 判定是有利影响（更不容易误判在途任务为陈旧）。

## 8. 回滚说明

逐文件回滚即可：删除 `tests/test_worker_fencing_atomicity.py`；恢复 `bidcraft/jobqueue.py` 中 `commit_fenced_result` 与 `or_` 导入；恢复 `bidcraft/similarity/service.py` 的原 `run_similarity`（或保留新接口并让 worker 回退到 `run_similarity`）；恢复 `bidcraft/workers/similarity.py` 的原两段式实现与 `tests/test_v030_stage4_similarity.py` 的两个断言。无数据库状态需要回滚，无生产配置变更。
