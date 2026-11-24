# BidCraft v0.31.0 阶段 3：账务一致性与并发生成治理报告

## 1. 阶段目标与批准边界

阶段 3 处理两件事：①不改变业务规则的账务/对账一致性修复；②用户批准的业务规则 **A：禁止同项目并发生成**。风险报告 `BIDCRAFT_V031_BILLING_RISK_REPORT.md` 已列出选项，用户明确选择 A。

严格边界：

- 不修改 `_cancel_stale_jobs()` 的周期扫描/心跳语义（原样保留）。
- 不修改历史迁移 0001～0011；只新增 `0012_generation_lease`。
- 不删除测试、不跳过测试、不降低任何门禁。
- 不改既有 API 成功响应语义；仅在并发冲突时按批准规则返回 HTTP 409。
- SQLite/PG 兼容；不在生产库测试。

## 2. 已完成改动（11 项证据）

| # | 项 | 证据 / 实现 |
|---|---|---|
| 1 | 异步退款付款方修正 | `jobqueue.refund_charged_job()` 优先取 `payload['billing_account_id']`，再回退 `payload['user_id']`；子账号被删除后仍退到父账号。`tests/test_billing_consistency_v031.py::test_refund_charged_job_uses_recorded_billing_account`。 |
| 2 | 对账按 Job 账本而非项目章节差 | 新增 `_marker_written()`：marker.note 以 `job:<id>|` 开头时读 Job.payload 的 `done_steps`；旧 marker 回退章节差。`settle_pending_charge()`/`reconcile_pending_charges()` 均使用。`test_reconcile_uses_job_ledger_not_project_wide_delta`。 |
| 3 | 负 TTL 防护 | `reconcile_pending_charges(older_than_seconds<0)` 抛 `ValueError("对账 TTL 不能为负数")`；0 仍允许以兼容既有测试。`test_negative_reconcile_ttl_is_rejected`。 |
| 4 | 异步扣费 marker 与 Job 双向绑定 | `projects.generate_all` 先生成 `job_id`，`begin_pending_charge(note=f"job:{job_id}|...")`，Job payload 增加 `billing_account_id`、`pending_charge_id`。`test_generate_all_records_async_charge_marker`。 |
| 5 | 异步 worker 心跳/结算对齐 | `workers/generation.py` 从 payload 读 `marker_id`，工作会话用 `pending_charge_heartbeat(marker_id)`，成功/失败均按 `done_steps` 调 `settle_pending_charge()`；旧任务仍走 `_job_refund()`。 |
| 6 | 生成互斥租约模型 | `models.GenerationLease`：`project_id` 主键、`token`、`job_id`、`owner_id`、`acquired_at`、`heartbeat_at`、`expires_at`；无 FK，避免影响账号删除。 |
| 7 | 新增幂等可逆迁移 | `bidcraft/migrations/versions/0012_generation_lease.py`（down=`0011_stage3_accounting`）：inspect 守卫 + 索引守卫建表/删表；`tests/test_alembic.py` HEAD 更新为 `0012_generation_lease`；未触碰历史迁移。 |
| 8 | DB 层原子抢占 | `jobqueue.acquire_generation_lease()` 先删过期租约，再用 `SAVEPOINT` + `IntegrityError` 唯一键冲突返回 `None`；SQLite/PG 通用，不毒化外层事务。`touch_generation_lease()`、`release_generation_lease()`、`assert_generation_lease()`、reaper 过期清理配套。`test_generation_lease_is_exclusive_and_reclaimable`。 |
| 9 | 三条生成入口统一互斥 | `generate_chapter()`、`write()`、`generate_all()` 均在扣费前抢占租约；冲突返回 409「该项目已有生成任务在运行，请等待完成后再试」。同步路径在成功/异常终态释放；异步路径把 token 写入 Job payload。 |
| 10 | 并发真实性验证 | `tests/test_billing_concurrency_v031.py`：直接租约互斥/回收/释放 + 持锁请求 409（不扣费、不建 Job）+ 双线程同时请求只有一个 202、一个 409、只有一个 Job 且保留租约。`test_two_concurrent_generate_requests_only_one_charges`。 |
| 11 | Worker 租约心跳与中止 | `pending_charge_heartbeat(..., lease_project_id=, lease_token=)` 同周期刷新租约；`before_chapter()` 调 `assert_generation_lease()`，租约丢失抛 `JobAborted`（不退款、留给 reaper）；done/失败终态释放租约；释放失败由 reaper 过期清理兜底。 |
| 12 | Reaper 计数语义修正（全量回归暴露） | `_requeue()` 现返回 `requeued/failed/lost`，竞争失败的 reaper 不再被计为新失败；`reap_expired()`/`recover_on_startup()` 只对真实 failed 转换计数。两份并发测试断言补强为「恰好一次 requeue、0 次 failed、恰好一个 queued 事件、0 个 failed 事件」，不是放宽门禁。 |

## 3. 兼容性 / 业务语义

- 成功路径响应体不变；并发冲突新增 409（用户批准的方案 A 核心语义）。
- 旧 Job（payload 无 `generation_lease_token`）仍走原有退款/对账路径，不受影响。
- 旧 marker（note 不以 `job:` 开头）仍按章节差对账。
- `_cancel_stale_jobs()` 未修改；它与新租约并行存在：软检查负责 30 分钟 stale 任务，租约负责 DB 原子互斥。
- 租约 TTL 默认 1800s，可通过 `settings.generation_lease_ttl_seconds` 调整（下限 60s）；与扣费 marker TTL 独立。

## 4. 验证结果

- `ruff check bidcraft tests`：**All checks passed**。
- `git diff --check`：clean。
- 定向：`tests/test_billing_concurrency_v031.py tests/test_billing_consistency_v031.py tests/test_alembic.py` → **15 passed**。
- 队列/ fencing 回归：`tests/test_v030_stage4_fencing.py tests/test_v030_stage4_similarity.py tests/test_v030_stage3_core.py tests/test_v030_stage3_routes.py` → **35 passed**（与上面合并运行共 50 passed）。
- 账务大回归（阶段 3 安全修复时）：billing / jobqueue / race / batch1 / fix3 / stage3 → **84 passed, 2 skipped**。
- Worker 回归（租约终态释放移出 DB 写事务后）：`test_worker_extraction / stage3 / billing_concurrency / batch1_charge_refund` → **36 passed**。
- Reaper 竞争回归（计数语义修正后）：`test_jobqueue_race_conditions / test_jobqueue / test_batch1_charge_refund / test_worker_fencing_atomicity` → **49 passed, 2 skipped**。
- 全量后端：`.venv/bin/python -m pytest -o addopts="" -q` → **1647 passed, 18 skipped, 1 warning in 748.96s**，零失败（输出 `/tmp/opencode/v031_stage3_full2.out`；首次运行暴露 reaper 竞争计数问题，已按第 12 项修正后重跑通过）。

## 5. 残留风险 / 未做

1. 同步生成路径的租约释放发生在请求进程；进程在扣费后、释放前被 SIGKILL 时，租约只能等 TTL 过期后被下一个请求/reaper 回收，期间项目被锁住最多 TTL（默认 30 分钟）。这是方案 A 的可接受代价。
2. PG 真实并发（双进程同时抢租约）未在 CI 之外复现；唯一键 + SAVEPOINT 是标准 SQL 行为，仓库已有 opt-in PG 测试框架。
3. 租约 TTL 配置项通过 `getattr(settings, ...)` 读取，未新增 config 字段；如需显式文档化可在后续阶段补 env 说明。
4. 未改 `GenerationLease` 的唯一索引名称之外的历史对象；`0012` 是完全新增的可逆迁移。

## 6. 回滚

逐文件撤销阶段 3 差异；删除新增迁移 `0012_generation_lease.py` 与两个新测试文件；`tests/test_alembic.py` HEAD 回退到 `0011_stage3_accounting`。已部署环境需先 `alembic downgrade 0011_stage3_accounting`（迁移可逆），不会丢业务数据。
