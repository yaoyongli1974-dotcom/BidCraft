# v0.33.1 Phase 5 验收报告：性能、恢复与稳定性

## 1. 范围

复用现有 job queue / fencing / reaper / heartbeat / GenerationLease / PendingCharge 机制，不新增并发控制体系。生产负载、soak、告警保持 NOT VERIFIED。

## 2. 环境

- 隔离 scratch PostgreSQL：`v0331_phase5`，PostgreSQL 16.2，`127.0.0.1:5433`，`BIDCRAFT_TEST_PG_URL=postgresql+psycopg://postgres:@127.0.0.1:5433/v0331_phase5`。
- SQLite：仓库测试默认真库。
- 所有数据均为测试库，未访问生产库。

## 3. 待回填

- 完整 SQLite 全量回归结果。
- 真实 PostgreSQL 并发/恢复套件结果。
- 隔离 RC 环境性能基线（GET /projects、POST /projects、/health/live）。
- 退款唯一归属、心跳 3-strike、PG SKIP LOCKED 的现有真实测试取证。

## 4. 结论

待验证后填写。
