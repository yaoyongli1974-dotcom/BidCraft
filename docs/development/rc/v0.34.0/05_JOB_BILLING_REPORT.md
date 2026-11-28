# BidCraft v0.34.0 阶段 2：任务失败/退款 fencing 与租约恢复报告

## 1. 范围与边界

本阶段只处理 P0-05（失败任务退款路径与状态抢占顺序）和 P0-06（恢复后 GenerationLease token 永久失效），并补齐排队任务扣费标记的 TTL 保护。严格按任务书的文件所有权实施：

| 类型 | 文件 |
|---|---|
| 生产修改 | `bidcraft/jobqueue.py`、`bidcraft/services/job_support.py`、`bidcraft/workers/generation.py` |
| 新增测试 | `tests/test_v0340_job_accounting.py` |
| 新增报告 | `docs/development/rc/v0.34.0/05_JOB_BILLING_REPORT.md` |

未编辑 `models.py`、迁移、`routes/projects.py`、`billing.py`、`wechatpay.py`；无新迁移、无 API 变更、无 Python 全局锁、无 mock-only 证据、无 commit/push、未运行 `ruff format`、未删除或降低任何断言。工作区里其他并行任务的修改（`config.py`、`billing.py`、`wechatpay.py`、支付测试等）不属于本阶段，未触碰。

## 2. 生产改动（按函数）

### 2.1 `bidcraft/services/job_support.py::_fail_job`

- 顺序改为 **先抢状态、后碰账务**：先用 `jobqueue.transition_job(from_statuses=("queued", "running"), to_status="failed", token=job.claim_token, clear_claim=True)` 抢占失败终态；只有赢家进入退款 savepoint 并发布 `failed` 事件。输家立即 `return False`，不再调用 `refund_charged_job`，也不发任何事件。
- 退款仍在 `db.begin_nested()` savepoint 内调用 `jobqueue.refund_charged_job`，退款失败时回滚 savepoint、`failed` 已落库，并发 `refund_failed` 事件供对账；返回值语义保持不变（抢到转换/终态补退为 `True`，抢输或已领取退款为 `False`）。
- 终态（`done`/`failed`/`cancelled`）但 `refund_claimed=False` 的补退窗口保留：只结算账本、不重发 `failed` / `refund_failed`，避免旧 worker 死在转换与退款之间时永久漏退。
- 取消路径（`_cancel_stale_jobs` → `_fail_job`）沿用同一单点退款：失败转换赢家唯一退款，并按 payload 中 token 释放 `GenerationLease`（token 作用域 DELETE，天然幂等），不会二次入账。

### 2.2 `bidcraft/jobqueue.py`

- 新增 `clear_job_generation_lease_token(db, job_id, *, token=None)`：
  - `db.expire()` 丢弃身份映射旧快照后重读，复制 payload 字典（`dict(row.payload or {})`）并 `pop("generation_lease_token")`；**不做 JSON 原地变更**。
  - 单条条件 `UPDATE ... WHERE id=? AND status='queued'`（可选 claim token 守卫）写回新 payload；`rowcount=0` 直接返回 `False`，不覆盖并发写。
  - 同一事务内按旧 token 释放 `GenerationLease`（`release_generation_lease` 本身带 token 条件），避免旧租约在 TTL 内挡住新 worker。
- `_requeue` 在赢得 `running → queued` 条件转换后、发布 `queued` 事件前调用该 helper。reaper 与 `recover_on_startup` 都走 `_requeue`，两条恢复路径统一覆盖；`queued` 事件 payload 也是清理后的版本。
- `pending_charge_heartbeat` 进入上下文时同步刷新一次 `PendingCharge.updated_at`（`touch_pending_charge`），堵住“第一个心跳周期之前”的 TTL 对账窗口；失败仅记 debug，由后台线程重试。
- 新增 `_queued_job_reserves_charge`：`marker.note` 关联 Job 且 Job 仍为 `queued` 时，`reconcile_pending_charges` 跳过该标记，避免排队任务被提前退款。保护有界：`grace = max(2 * ttl, 2 * generation_lease_ttl, 60)` 秒，以 Job `created_at` 为锚点；僵尸排队任务超过上界仍会被结算。

### 2.3 `bidcraft/workers/generation.py::_run_generation_job`

- 在 fenced 会话内（已通过 `fenced_job` 校验 claim token）读取 payload；若 `generation_lease_token` 为空，调用 `acquire_generation_lease(db, project_id, job_id=..., owner_id=...)` 获取新 token，复制到 payload 写回 `Job.payload` 并 `flush()`，**在启动心跳与提交章节之前完成**。
- 拿不到租约时抛 `JobAborted`（交回 reaper 重排队），不绕过任何 fencing；`assert_generation_lease` / `fenced_chapter_write` 仍使用同一个新 token。

## 3. 测试（先写测试，后写生产修复）

新增 `tests/test_v0340_job_accounting.py`：13 个用例，全部使用真实 SQLite Session / 真实线程 / 真实条件 UPDATE，仅在模型调用边界替换 `generate_document`；账务、事件、租约、状态均从数据库断言。

| 用例 | 覆盖点 |
|---|---|
| `test_fail_job_vs_worker_finalize_single_owner[0..5]` | 6 轮 Barrier 双线程竞争 `_fail_job` vs worker `commit_fenced_result`+`settle_pending_charge`：spy 记录操作顺序，只有转换赢家调用退款；终态事件恰好 1 条、退款流水恰好 1 笔、余额从 4 变 8，无 `refund_failed` |
| `test_fail_job_transitions_before_refund` | 退款时传入的 Job 状态必须是 `failed`；顺序断言 `["transition", ("refund", "failed")]` |
| `test_requeue_clears_stale_generation_lease_token[reaper/startup]` | 两条恢复路径都清 token、释放旧租约、保留 `done_steps`/`charged`、`queued` 事件不含 token、不产生失败/退款 |
| `test_new_worker_reacquires_lease_and_heartbeat_after_requeue` | 恢复后 `claim_next` 新 token → `_execute` 真实线程跑 worker：新租约 token 与 payload 一致且不同于旧 token，`renew_generation_lease` 持续 `active`，`GenerationLease` 与 Job 租约到期时间都在生成过程中前移，`abort` 未置位，最终 `done` 且无 fail/refund 循环、无重复 `queued` 事件 |
| `test_cancel_releases_generation_lease_and_refunds_once` | `_cancel_stale_jobs` 取消：唯一退款、唯一 `failed` 事件、租约释放；重复 `_fail_job` 返回 `False` 且余额/事件不变 |
| `test_queued_marker_not_early_refunded_while_job_queued` | Job 仍 `queued` 时 TTL 对账 0 笔、余额不变；转终态后同一 marker 结算 1 笔 |
| `test_queued_marker_ttl_skip_is_bounded` | `created_at` 远超 grace 的排队任务仍会被对账退款，证明保护有上界 |

**测试先行证据**（改动生产代码前）：`pytest tests/test_v0340_job_accounting.py` → `11 failed, 2 passed`。典型失败：`['refund', 'transition:lost'] != ['transition:lost']`（输家先退款）、`('refund','running') != 'transition'`（退款早于状态转换）、reaper/startup 后 payload 仍含 token、新 worker 租约失效、排队 marker 被提前退款。

## 4. 验证结果（实际执行命令）

```bash
.venv/bin/ruff check bidcraft/jobqueue.py bidcraft/services/job_support.py \
    bidcraft/workers/generation.py tests/test_v0340_job_accounting.py
# All checks passed!

.venv/bin/python -m pytest -o addopts= -q tests/test_v0340_job_accounting.py
# 13 passed in 3.65s

.venv/bin/python -m pytest -o addopts= -q \
    tests/test_jobqueue.py \
    tests/test_jobqueue_race_conditions.py \
    tests/test_v0311_stage5_billing_races.py \
    tests/test_v0330_generation_concurrency.py \
    tests/test_worker_reliability_matrix.py
# 51 passed, 1 skipped in 10.66s

# 额外邻近回归（非全量）
.venv/bin/python -m pytest -o addopts= -q \
    tests/test_v0312_billing_concurrency.py tests/test_v030_stage3_routes.py \
    tests/test_v0311_stage1_heartbeat.py tests/test_v030_stage3_core.py \
    tests/test_batch1_pending_charge.py tests/test_fix2_data_integrity.py
# 335 passed, 1 warning in 18.96s
```

按要求执行了 `.venv/bin/ruff check bidcraft tests`；命令本报告写入时返回 **3 个 SIM117 错误**，均位于并行任务拥有的文件（`tests/test_alembic.py:657`、`tests/test_v0340_billing_atomicity.py:325,379`），本阶段拥有的 4 个文件 0 错误。未代修这些文件（严格所有权），需由对应任务处理后再跑全局 ruff。未运行全量 pytest。

## 5. 残余风险

1. 并发验证在当前 SQLite（WAL）上完成；任务书指定的 PostgreSQL 真实集成未在本机运行，`FOR UPDATE SKIP LOCKED` / 行锁语义只由既有 `test_claim_statement_skip_locked_only_on_postgres` 覆盖。
2. `clear_job_generation_lease_token` 的条件更新以 `status='queued'` 为准：极端窗口内新 worker 已完成 claim 时，清理会被跳过（不会覆盖新 owner）；新 worker 会沿用仍有效的旧租约 token，但 Job claim fencing 仍保证不会双写。
3. 排队 marker 保护的 grace 基于 `Job.created_at` 与固定倍数（默认至少 3600 秒）；超长排队且 Job 未推进的极端场景会等待该上界后才退款，属于刻意的有界权衡。
4. 终态补退路径刻意不发 `refund_failed`；退款失败只能靠账本 `refund_claimed=False` 与后续对账发现，不会出现在 SSE 事件流中。
5. 全局 ruff 当前被并行任务的 3 处 SIM117 阻塞（见上）；本阶段文件本身干净。

## 6. 回滚

无需迁移/API 回滚。若需整体撤销本阶段：

```bash
git restore bidcraft/jobqueue.py bidcraft/services/job_support.py bidcraft/workers/generation.py
rm tests/test_v0340_job_accounting.py docs/development/rc/v0.34.0/05_JOB_BILLING_REPORT.md
```

仅回滚生产代码时保留新增测试，可直接复现第 3 节的测试先行失败，便于复核。回滚后旧行为恢复：失败回调先退款、恢复任务携带失效租约 token、排队 marker 可被 TTL 提前结算。
