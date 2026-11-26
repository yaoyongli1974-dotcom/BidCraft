# v0.33.1 Phase 5 验收报告：性能、恢复与稳定性

## 1. 结论

本阶段无业务代码变更。复用 v0.33.0/v0.33.1 已有的 job queue / fencing / reaper / heartbeat / GenerationLease / PendingCharge 机制，真实 PostgreSQL 并发/恢复套件与隔离环境性能基线均通过。生产负载、soak、告警仍 NOT VERIFIED。

## 2. 环境

- 隔离 scratch PostgreSQL：`v0331_phase5`，PostgreSQL 16.2，`127.0.0.1:5433`，`BIDCRAFT_TEST_PG_URL=postgresql+psycopg://postgres:@127.0.0.1:5433/v0331_phase5`。
- SQLite：Phase 4 全量回归 `2379 passed, 1008 skipped, 1 warning in 982.08s`。
- 所有数据均为测试库，未访问生产库。

## 3. 真实 PostgreSQL 并发/恢复

```bash
PG_URL='postgresql+psycopg://postgres:@127.0.0.1:5433/v0331_phase5'
BIDCRAFT_TEST_PG_URL="$PG_URL" .venv/bin/python -m pytest -o addopts= -q \
  tests/postgres tests/test_postgres_integration.py tests/test_v030_pg_isolation.py \
  tests/test_worker_fencing_atomicity.py tests/test_jobqueue_race_conditions.py
# 1022 passed, 1 skipped, 1 warning in 627.36s (0:10:27)
```

覆盖：Real PG reaper 竞争、GenerationLease vs chapter commit、双 worker finalize、账单退款单一归属、8 worker 运行时矩阵、文件副作用、质量指标/迁移/权限、Alembic 迁移往返、claim 原子性与 fencing。1 skipped 为环境依赖用例，未以 skip 掩盖失败。

## 4. 隔离性能基线

```bash
# TestClient + 隔离 PG + fake model endpoint（仅启动校验），BIDCRAFT_ENV=rc
LOGIN 200
GET /projects codes [200] n 300 P50 15.60 ms, P95 18.07 ms, MAX 214.20 ms
POST /projects codes [201] n 30 P50 78.52 ms, P95 83.96 ms, MAX 165.03 ms
GET /health/live codes [200] n 100 P50 6.48 ms, P95 7.91 ms, MAX 11.54 ms
```

仅为隔离 RC 环境短测，不作生产 SLA。首次请求含连接/初始化开销，MAX 高于中位数。

## 5. 恢复与稳定性证据

- 退款唯一归属：Phase 4 全量回归含 `tests/test_v0330_generation_concurrency.py` 的 marker settle → `_fail_job`、`_fail_job` → reconcile 顺序用例；PG 套件再次覆盖真实 DB 路径。
- 心跳 3-strike：Phase 4 全量回归覆盖 transient 容忍与第 3 次失败 abort。
- PG SKIP LOCKED：`tests/test_v0330_generation_concurrency.py` 编译断言 PG 方言含 `FOR UPDATE SKIP LOCKED`、SQLite 不含；真实 PG suite 覆盖 claim 竞争。
- 文件副作用：PG suite 覆盖导出 zip 丢失 fence 后清理与成功保留、图片幂等重试、KB 仅 DB 写入。

## 6. 数据与环境阻塞

- DATA BLOCKED：生产等价 PostgreSQL/PgBouncer、连接池权限、真实备份恢复演练需独立授权窗口。
- NOT VERIFIED：生产负载、长时间 soak、告警阈值与 On-call 流程、真实模型生成吞吐。
- 本地 pgserver 是真实 PostgreSQL 16.2，但不是生产环境；报告不把它写成生产验证。

## 7. 回滚

本阶段无业务代码变更；如撤销，删除或回退本报告即可。PG scratch DB 不影响业务库。
