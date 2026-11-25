# v0.31.2 阶段 5：账务竞争真实 Session 回归报告

- 分支：`fix/v0.31.2-concurrency`（阶段 0 `aadfe49`、1 `3ff8a4d`、2 `204e02e`、3 `1452568`、4 `3421a31` 之后）
- 目标：用真实多线程 + 独立 Session 验证退款/失败/取消/标记对账/子账号删除/退款失败的金额守恒，禁止用全局 Python 锁或只 mock 核心 SQL 的伪并发证据。
- 结论：**已完成并通过验证**（50 轮 × 6 场景，全部通过）。

## 1. 新增测试

`tests/test_v0312_billing_concurrency.py`（300 个参数化用例，6 场景 × 50 轮）：

| 场景 | 并发双方 | 核心断言 |
| --- | --- | --- |
| 两线程退款 | `refund_charged_job` ×2 | 唯一成功退款 4 点；余额 8；账本 `[job:id]` 恰好 1 笔；`refund_claimed=True` |
| Reaper vs Worker 失败 | `_set_failed` vs `_fail_job` | 恰好 1 条 `failed` 事件；退款 4 点；账本 1 笔 |
| 取消 vs 完成 | `transition_job(cancelled)` vs `finalize_job_result(done)` | 最多一个终态；没有双终态事件 |
| settle vs reconcile | `settle_pending_charge` vs `reconcile_pending_charges(TTL=0)` | 两者返回 `(4,0)` 或 `(0,1)`；余额 8；标记 `refunded`；账本 1 笔 |
| 子账号删除 | 先删 child，再并发退款 | 退回父账号 6 点；余额 10；账本 1 笔 |
| 退款 DB 错误后重试 | 第一次 `billing.refund_chapters` 抛错，第二次恢复 | 失败后 `refund_claimed=False`、余额 4；重试后退还 4、余额 8、账本 1 笔 |

每轮使用独立用户/任务/标记（uuid 后缀），线程由 `Barrier` 同步起跑，核心条件 UPDATE 全部走生产代码，SQLite 短锁仅在测试线程内重试（不是 Python 业务锁）。

## 2. 发现并修复的生产问题

### 2.1 `_fail_job` 与 Reaper 并发时重复 `failed` 事件

`bidcraft/services/job_support.py::_fail_job` 原先直接赋值 `job.status='failed'` 再发事件。两个线程都能从陈旧快照读到 `running`，导致 50 轮测试稳定出现两条 `failed` 事件。

修复：改为调用 `jobqueue.transition_job(... from_statuses=('queued','running'), to_status='failed', token=..., clear_claim=True)`。只有抢到条件 UPDATE 的一方写终态并发事件；失败方直接返回。退款仍在 savepoint 中先做，且由 `refund_claimed` 条件领取保证唯一入账。终态任务的行为保持不变（只补做未领取的退款，不重复发事件）。

### 2.2 `transition_job` 过期整个 Session 导致调用方对象脱离

`transition_job` 为丢弃身份映射中的陈旧 Job 快照原先调用 `db.expire_all()`，会把调用方仍持有的 User 等对象一并置于 detached+expired 状态，旧测试在事务外读取 `user.id` 时报 `DetachedInstanceError`。

修复：只 `db.expire(mapped Job)`，不再影响无关 ORM 对象。状态机语义和 fencing 条件不变。

### 2.3 退款写账本失败时释放领取标记

`refund_charged_job` 与 `settle_pending_charge` 原先先做条件领取（`refund_claimed=1` / marker `status='processing'`）再调用 `billing.refund_chapters`。若账本瞬时 DB 错误，领取不释放会导致永远无法重试。

修复：两者都在 `refund_chapters` 抛错时把领取改回可重试状态（`refund_claimed=False` / marker `status='pending'`），再原样抛错；成功路径不变。该修复使 50 轮「退款 DB 错误后重试」场景金额守恒成立。

## 3. 退回金额口径

- `refund_charged_job`：`(chapters - written) * charged // chapters`，`written` 来自 `done_steps` 的 `chapter:` 项。
- `settle_pending_charge`：用 `_marker_written`，`job:` 标记同样取 Job 的 `done_steps`；退款失败时释放标记回 `pending`。
- `reconcile_pending_charges`：同样用 `_marker_written`，保证与 settle 对同一任务算出相同金额；返回“安置的标记数”而非点数（测试按此口径断言）。

## 4. 验证命令与结果

```bash
.venv/bin/python -m ruff check bidcraft tests            # All checks passed
.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0312_billing_concurrency.py \
  tests/test_v0311_stage5_billing_races.py \
  tests/test_v030_stage3_routes.py                       # 311 passed, 1 warning

.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_jobqueue.py tests/test_jobqueue_race_conditions.py \
  tests/test_reliability.py tests/test_worker_reliability_matrix.py \
  tests/test_v030_stage3_core.py tests/test_v030_stage3_routes.py \
  tests/test_billing_consistency_v031.py \
  tests/test_billing_concurrency_v031.py \
  tests/test_batch1_charge_refund.py tests/test_batch1_pending_charge.py \
  tests/test_fix3_backend_charges.py tests/test_fix3_refund.py
# 88 passed, 1 skipped
```

## 5. 失败历史与修正（未删测试、未降低断言）

1. 首轮 250 failed：测试用户名用 `threading.get_ident()`，跨参数化重复导致 UNIQUE 冲突 → 改用 `uuid4().hex`。
2. Reaper vs Worker 出现 2 条 `failed` → 生产 `_fail_job` 改为条件状态转换（见 2.1）。
3. 旧回归出现 `DetachedInstanceError` → `transition_job` 改为只过期 Job（见 2.2）。
4. settle/reconcile 金额一度出现 6（全退），因测试标记缺 `job:` 前缀，reconcile 走项目级 section 差值为 0 → 测试改为带 `job:` 标记并给 Job 写入 1 个 done_step，双方均算 4。
5. cancel vs complete 的 `finalize_job_result` 缺 `claim_context` → 测试按生产 worker 用法补上并捕获合法 `JobAborted`。

## 6. 兼容性

- 无迁移、无模型字段、无 API 响应变化。
- `_fail_job` 仍保留退款失败时发布 `refund_failed`、终态幂等、fence 清理语义。
- SQLite/PostgreSQL 均使用同一条件 UPDATE；本地仅 SQLite，PG 真实并发仍由 CI opt-in 覆盖。

## 7. 残余风险

- 本地未运行 PostgreSQL 真实并发（`BIDCRAFT_TEST_PG_URL` 未提供）；条件 UPDATE 的 SQL 为数据库通用写法。
- 两线程竞争依赖 SQLite 短锁重试；测试中的重试不是业务锁，不改变生产 fencing 语义。

## 8. 回滚

逐项回退：`tests/test_v0312_billing_concurrency.py`（删除）、`bidcraft/services/job_support.py::_fail_job` 的条件转换改回直接赋值、`bidcraft/jobqueue.py::transition_job` 的 `db.expire(mapped)` 改回 `db.expire_all()`。本报告一并删除。无数据迁移可回滚。
