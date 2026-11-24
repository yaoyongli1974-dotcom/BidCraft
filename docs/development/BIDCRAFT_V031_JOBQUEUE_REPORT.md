# BidCraft v0.31.0 阶段 2：JobQueue / Reaper 并发治理报告

## 1. 阶段目标与结论

把 Job 状态迁移收口到**条件 UPDATE + 显式状态机**：多个 reaper、心跳、worker 安全网同时竞争时，只有一个写入者能成功；终态不可变；requeue 清理陈旧结果；心跳不再复活过期租约，且续租失败会触发 abort。阶段 2 **完成并通过本地 SQLite 验证**；PostgreSQL 变体以 opt-in 测试提供，需 CI 执行。

## 2. 修改文件与函数

| 文件 | 修改 |
|---|---|
| `bidcraft/jobqueue.py` | 新增 `VALID_TRANSITIONS`、`transition_job()`；`_set_failed()` / `_requeue()` 改为条件 UPDATE；`reap_expired()` / `recover_on_startup()` 传入 `lease_expired_before=now`；`_finalize_after_run()` 传入当前 token；`_heartbeat()` 增加未过期租约条件，异常时置 abort。 |
| `tests/test_jobqueue_race_conditions.py` | 新增 11 条测试：状态机、双 reaper、50 轮并发回收、并发 fail、事件去重、心跳失败/过期租约、PG opt-in。 |
| 本报告 | 11 项验收证据。 |

## 3. 核心协议

```python
job = transition_job(
    db, job_id,
    from_statuses=("running",), to_status="queued",
    token=...,                      # worker 路径：当前 fencing token
    lease_expired_before=now,       # reaper 路径：仅回收已过期租约
    require_attempts_below=cap,     # 重试上限在同一条 UPDATE 中判定
    clear_claim=True,               # 清 token/lease/worker/result/progress
)
```

- 所有状态迁移走单条 `UPDATE ... WHERE id/status/token/lease/attempts`，`rowcount==0` 即代表“别人赢了”，调用方不得继续写事件或退款。
- `VALID_TRANSITIONS`：`queued → running/failed/cancelled`；`running → queued/done/failed/cancelled`；`done/failed/cancelled` 为终态。
- Reaper 不再先 SELECT 再 ORM 写；两个 reaper 同时扫到同一行时只有一个条件 UPDATE 成功。
- 心跳 UPDATE 增加 `lease_expires_at IS NOT NULL AND lease_expires_at >= now`，过期租约无法被续命；任何心跳异常都会 `abort.set()`，让 runner 在检查点停止。

## 4. 11 项验收证据

| # | 验收项 | 证据 |
|---|---|---|
| 1 | 显式状态机拒绝非法转换 | `test_transition_job_enforces_state_machine`：`running→done` 成功，`done→running` 与 `running→bogus` 抛 `ValueError`。 |
| 2 | requeue 清理陈旧结果 | `test_requeue_clears_stale_result_progress_and_claim`：过期任务 requeue 后 `status=queued`、`claim_token=None`、`result=None`、`progress=0`。 |
| 3 | 双 Reaper 不重复 requeue | `test_two_reapers_cannot_requeue_same_job_twice`：两线程同时回收，断言 `(requeued,failed)` 总和为 1，`queued` 事件只有 1 条。 |
| 4 | 50 轮固定种子并发回收 | `test_50_rounds_two_reapers_only_one_wins`：`random.seed(20261124)`，50 轮均只有一个 reaper 成功，无线程挂起。 |
| 5 | 并发 fail 只记一次事件 | `test_concurrent_set_failed_publishes_exactly_one_event`：两线程 `_set_failed`，返回值 `[False, True]`，`failed` 事件仅 1 条。 |
| 6 | 终态重复回收不重复审计 | `test_audit_events_are_not_duplicated_on_terminal_race`：重复 reaper 调用不产生第二条 `queued` 事件。 |
| 7 | 心跳不能复活过期租约 | `test_heartbeat_cannot_revive_expired_lease`：过期租约下 abort 被置位，DB 中 `lease_expires_at` 不变。 |
| 8 | 心跳异常触发 abort | `test_heartbeat_failure_sets_abort`：monkeypatch `session_scope` 抛错，abort 被置位。 |
| 9 | finalize 安全网受 token 保护 | `test_finalize_after_run_is_fenced_after_reclaim`（既有）+ 阶段 1 旧 token 测试：token 变化时安全网不覆盖新 owner。 |
| 10 | 退款只在成功抢占失败态后发生 | `_set_failed()` 先 `transition_job` 成功再 `refund_charged_job()`；失败方直接返回 `False`，不重复退款。 |
| 11 | PostgreSQL opt-in | `test_postgresql_multi_reaper_optin`：在 `BIDCRAFT_TEST_PG_URL` 下重复双 reaper 竞争并断言单胜者；结束时复位引擎。 |

## 5. 业务与兼容性影响

- **数据库**：无新增字段/索引/迁移；未改动历史 migration；SQLite 与 PG 均使用标准条件 UPDATE + `or_`，无方言专有语法。
- **API**：无请求/响应结构变化；无业务语义变化。
- **前端**：无改动。
- **事件**：重排队会清理 `result/progress`，SSE 客户端看到的新 attempt 从 0 开始，符合“重新执行”语义；`done_steps` 仍保存在 `payload`，恢复后续跑不重复章节。

## 6. 验证结果

- `ruff check bidcraft tests` → All checks passed（未运行 ruff format）。
- `pytest tests/test_jobqueue_race_conditions.py tests/test_jobqueue.py tests/test_worker_fencing_atomicity.py` → **39 passed, 2 skipped**（PG opt-in 与无本地依赖跳过）。
- 扩展回归（队列/ fencing / 账务 / 可靠性 / API jobs）：`tests/test_jobqueue.py tests/test_jobqueue_race_conditions.py tests/test_worker_fencing_atomicity.py tests/test_v030_stage4_fencing.py tests/test_v030_stage4_similarity.py tests/test_reliability.py tests/test_reliability_writer.py tests/test_batch1_charge_refund.py tests/test_batch1_pending_charge.py tests/test_fix3_backend_charges.py tests/test_fix3_refund.py tests/test_v030_stage3_core.py tests/test_v030_stage3_routes.py tests/test_api_jobs.py tests/test_jobs_unit.py` → **114 passed, 2 skipped**（仅 Starlette/httpx 弃用警告）。

## 7. 残留风险与限制

1. 本地无真实 PostgreSQL，PG 并发路径依赖 CI `postgres` job。
2. `_cancel_stale_jobs()`（`services/job_support.py`，分钟级 updated_at 判定）仍与 reaper（秒级 lease 判定）存在两套超时口径；该文件属阶段 3 账务/生成范围，本阶段未改，避免跨阶段冲突。
3. `cancelled` 已在状态机中声明，但当前系统没有独立的取消 API，实际取消仍通过 `_fail_job()` 落为 `failed`；本阶段不引入新 API 语义。
4. `transition_job` 的 `db.expire_all()` 会过期当前 Session 的所有 ORM 对象；调用方应使用返回的 fresh row，不要继续持有调用前的快照。

## 8. 回滚说明

逐文件回滚：删除 `tests/test_jobqueue_race_conditions.py`；在 `bidcraft/jobqueue.py` 删除 `VALID_TRANSITIONS`/`transition_job`，恢复原 `_set_failed`/`_requeue`/`reap_expired`/`recover_on_startup`/`_finalize_after_run`/`_heartbeat` 实现。无数据库状态或生产配置需要回滚。
