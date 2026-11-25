# v0.31.3 阶段 1：真实 PostgreSQL 并发验证与缺陷收口报告

- 分支：`fix/v0.31.3-production-readiness`（基线 f06feb7 / v0.31.2）
- 数据库：**真实 PostgreSQL 16.2**（`pgserver` 0.1.4，unix socket `/tmp/opencode/v0313-pgdata`，连接 URI 写入 `/tmp/opencode/v0313_pg_uri.txt`），通过项目 venv 的 psycopg 3.3.9 连接
- 约束遵守：先复现缺口、再改代码；所有场景真实线程 + 独立 Session + `Barrier` 同步；核心条件 UPDATE 由生产代码执行，不 mock、不用 Python 业务锁、不用固定 sleep

## 1. 先复现（用户明确要求）

新增 `tests/postgres/conftest.py`：

- 模块级 `pg_engine`：把 `settings.db_url` 指向 `BIDCRAFT_TEST_PG_URL`，重置引擎，drop 所有应用表与 `alembic_version`，再 `init_db()`；断言方言为 `postgresql`。
- 自动隔离：每个用例前 `TRUNCATE` 所有应用表（保留 `alembic_version`）`RESTART IDENTITY CASCADE`，并 `jobqueue._reset()`。
- 未设置 `BIDCRAFT_TEST_PG_URL` 时整目录 skip，不把 SQLite 结果冒充 PostgreSQL。

新增 `tests/postgres/test_v0313_postgres_reaper.py`（501 参数化用例）：

| 场景 | 轮次 | 并发双方 | 断言 |
|---|---:|---|---|
| 双 Reaper 回收 | 100 | `_requeue` ×2 | 唯一 `requeued` / `lost`，status=queued，1 条 queued 事件 |
| Reaper vs Worker 终态提交 | 100 | `commit_fenced_result` vs `_requeue` | 不能双终态；done 或 queued 二选一 |
| 上限失败 + 真实退款 | 100 | `_requeue` ×2 | 唯一 `failed`，1 条 failed 事件，`refund_claimed=True` |
| NULL / 过期 lease 拒绝终态 | 各 100 | 终态条件 UPDATE | `JobAborted`，状态不变、无事件 |
| 回收后旧 token 不能提交 | 100 | reaper → 旧 worker | `JobAborted`，无写入 |

首次真实运行 `/tmp/opencode/v0313_pg_reaper_repro4.out`：**100 failed / 401 passed**。全部失败集中在「上限失败 + 真实退款」，余额停在 90、无退款入账。

## 2. 根因与生产修复

**真实 PostgreSQL 缺陷**：`bidcraft/jobqueue.py::refund_charged_job` 的领取条件写作

```python
.where(Job.id == job.id, Job.refund_claimed == 0)
```

PostgreSQL 中 `refund_claimed` 是 `BOOLEAN`，SQLAlchemy 渲染为 `refund_claimed = 0`，抛 `psycopg.errors.UndefinedFunction: operator does not exist: boolean = integer`，事务中止；上层 `_set_failed` 捕获并记录后继续，因此用户被扣费却不退款、也没有认领标记。SQLite 动态类型掩盖了该问题。

修复为可移植写法：

```python
# PostgreSQL stores this as BOOLEAN: comparing with the Python int 0
# would render ``refund_claimed = 0`` and fail with "boolean = integer".
# .is_(False) is portable on both SQLite and PostgreSQL.
.where(Job.id == job.id, Job.refund_claimed.is_(False))
```

同时修正测试中的一个错误预期：种子为 `charged=6, chapters=3, done=1`，已 earned 2 点，应退 `(3-1)*6//3 = 4`，余额 `100-6+4=98`，而不是全额 100。断言同时改为精确校验账本正数记录 `== [4]`（比原断言更严格，未放宽）。

修复后 `/tmp/opencode/v0313_pg_reaper_fix.out`：**501 passed**（36s），零失败。

## 3. 新增 PostgreSQL 并发矩阵

新增 `tests/postgres/test_v0313_postgres_fencing_billing.py`（300 参数化用例）：

| 场景 | 轮次 | 并发双方 | 最终一致性断言 |
|---|---:|---|---|
| GenerationLease 释放 vs 章节提交 | 100 | `commit_generation_chapter` vs `release_generation_lease` | 提交成功则正文/done_steps/`chapter_committed` 完整；中止则全清 |
| 两个 Worker 终态提交 | 100 | `finalize_job_result` ×2 | 唯一 committed/aborted；status=done；恰好 1 条 done 事件 |
| 两个 Reaper 退款 | 50 | `refund_charged_job` ×2 | 退款 `[0,4]`；余额 98；账本 `[4]`；认领标记置位 |
| settle vs reconcile | 50 | `settle_pending_charge` vs `reconcile_pending_charges(TTL=0)` | `(4,0)` 或 `(0,1)`；余额 98；账本 `[4]`；标记 refunded=4 |

结果 `/tmp/opencode/v0313_pg_fencing.out`：**300 passed**（22s），零失败。

## 4. 汇总验证

```bash
BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/v0313_pg_uri.txt)" \
  .venv/bin/python -m pytest -o addopts="" -q \
  tests/postgres tests/test_postgres_integration.py tests/test_v030_pg_isolation.py \
  tests/test_worker_fencing_atomicity.py tests/test_jobqueue_race_conditions.py
```

`/tmp/opencode/v0313_pg_stage1_all.out`：**834 passed / 1 skipped**（66s），零失败。skip 为既有的非 PG opt-in 项，未放宽。

静态检查：`.venv/bin/python -m ruff check bidcraft tests` → All checks passed；`git diff --check` → clean。

## 5. 结论与残余风险

- 结论：阶段 1 为 **A PASS**。真实 PostgreSQL 16.2 暴露并修复了一个 SQLite 无法发现的账务退款缺陷；全部核心并发场景在真实数据库、真实线程、独立 Session 下通过。
- 残余风险：本轮 PG 实例是临时本地 `pgserver`，不等同于生产参数/网络/连接池；CI 真实 PG 作业（阶段 6）仍需把 `tests/postgres/` 纳入并验证。阶段 8 之前不得据此宣称生产环境已验证。
- 回滚：恢复 `refund_charged_job` 的单行条件（不推荐，会重新引入 PG 退款失败），或删除 `tests/postgres/`；无迁移、无 schema 变化。
