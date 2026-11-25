# BidCraft v0.31.2 阶段 2：Reaper 真实并发竞争报告

基线 HEAD：`74c41568bd57edff25ffb6cb53808b6869e3bc6c`；阶段 1 提交 `3ff8a4d`。

## 1. 结论

Reaper 竞争测试已升级为真实多线程、多 Session、多事务测试。生产代码仅抽取 `_renew_lease_once()` 作为可复用的一次性心跳条件 UPDATE（与 `_heartbeat` 原逻辑逐句一致），不引入 Python 全局锁、不 mock 核心 UPDATE。

## 2. 生产代码改动

| 函数 | 改动 | 原因 |
|---|---|---|
| `jobqueue._renew_lease_once(job_id, token)` | 从 `_heartbeat` 循环体抽取，返回是否成功续租 | 让测试能在独立 Session 中执行真实心跳 SQL |
| `jobqueue._heartbeat` | 循环内改调 `_renew_lease_once` | 保持单一实现，避免测试与生产漂移 |

状态迁移仍由 `transition_job()` 的**一条条件 UPDATE**负责，带有 `lease_expired_before`、token 与 attempts 条件。

## 3. 并发测试证据

测试文件：`tests/test_v0312_reaper_true_concurrency.py`。同步方式：`threading.Barrier` + `threading.Event`，**不依赖固定 sleep**。每个线程创建自己的 SQLAlchemy `Session`，不共享 ORM 实例。SQLite 使用文件数据库 + WAL + busy_timeout，未使用 Python 全局锁。

| 场景 | 线程/Session | 轮次 | 成功 | 拒绝/失败 | 异常 | 最终一致性断言 |
|---|---:|---:|---:|---:|---:|---|
| A 双 Reaper 回收 | 2 / 2 | 100 | 1 requeued | 1 lost | 0 | status=queued；attempts=0；仅 1 条 queued 事件；无 failed 事件 |
| B Heartbeat 先成功 | 2 / 2 | 100 | hb=True | reaper=lost | 0 | status=running；lease 续期；无事件 |
| C Reaper 先成功 | 2 / 2 | 100 | requeued | hb=False | 0 | status=queued；token 清除；仅 1 条 queued 事件 |
| D 重试上限 + Heartbeat | 2 / 2 | 100 | hb=True | reaper=lost | 0 | status=running；attempts=3；无 failed 事件 |
| E 上限失败 + 真实退款 | 2 / 2 | 50 | 1 failed | 1 lost | 0 | 1 条 failed 事件；refund_claimed=1；余额从 90→100；无超额退款 |

场景 E 使用真实 `User` / `billing.add_credits(100)` / `charge_chapters(10)`，核对原始扣费、退款金额、账本余额与 `refund_claimed`，未 mock 退款逻辑。

## 4. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | 通过 |
| `pytest tests/test_v0312_reaper_true_concurrency.py tests/test_v0311_stage2_reaper_race.py tests/test_jobqueue_race_conditions.py` | **68 passed / 1 skipped**（PG opt-in） |
| 全量后端 | 阶段 7 统一执行 |

## 5. 失败记录与修正

- 初次运行 3 失败：A 期望 attempts==1 错误（requeue 不增加 attempts，只有 claim 才增加），已按生产语义修正断言；B/D 种子租约已过期导致 heartbeat 失败，改为 `lease_expired=False`。均非降低断言，而是修正测试预期/前置条件。

## 6. 未完成与残余风险

- 本地无真实 PostgreSQL/psql，场景 A～E 的 PG 行锁与双连接并发尚未本地执行；PG opt-in 用例保留，需在 CI/有 PG 的环境验收。
- SQLite 单写者特性使部分竞争序列化；测试证明的是条件 UPDATE 的原子性与最终状态一致性，不等同于 PG 行锁压力结果。
