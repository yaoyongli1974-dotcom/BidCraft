# RC-12 PostgreSQL 迁移与并发验证报告

## 1. 范围与基线

- 分支：`fix/v0.33.0-rc-reliability`，本轮起点 `99c0e3b`（RC-06/07 已提交）。
- Alembic head：`0015_fact_confirmation`（动态推导，单 head）；链上 v0.32 revision 为
  `0013_chapter_planning` → `0014_kb_asset_metadata` → `0015_fact_confirmation`，
  父版本 `0012_generation_lease` 为历史边界，只读、不编辑、不降级。
- 数据库：本地真实 PostgreSQL 16.2（pgserver 集群
  `/home/yao/.cache/bidcraft-v032-pgdata`），psycopg 3.3.9（项目 venv）。
  仅使用新建的临时库 `rc12_migration_concurrency`；`postgres` 库只用于 CREATE/DROP
  该临时库，未连接、未改动任何既有库或生产数据。
- 严格所有权：本轮只新增
  `tests/postgres/test_v0330_migration_concurrency.py` 与本报告；未编辑
  `tests/postgres/conftest.py`（无需扩权），未触碰 `bidcraft/*`、`web/*`、
  其他测试或其他报告。未提交、未推送、未打 tag、未部署。

## 2. 测试先行与覆盖矩阵

新增 `tests/postgres/test_v0330_migration_concurrency.py`（119 个用例，全部 opt-in）。
未设置 `BIDCRAFT_TEST_PG_URL` 时由 conftest 的 autouse fixture 干净跳过；模块自身也有
`pytest.importorskip("psycopg")` + dialect 断言，保证结论只来自真实 PG。测试使用独立
线程 + 独立 `session_scope()` + `threading.Barrier` 起跑；条件 UPDATE 全部由生产代码
执行，不用 Python 业务锁串行化，不用 mock 冒充真实执行。

### 2.1 v0.33 RC 产物在 PG 上的迁移往返（2 个用例）

- `test_v0330_head_artifacts_on_pg`：在 head 再次执行 `init_db()`（幂等），校验
  revision 父子链、DB 版本与动态 head 一致；用 SQLAlchemy `inspect` 校验
  `chapter_plans` 的全部列、唯一约束 `uq_chapter_plan_project_node_version` 与 5 个索引；
  `kb_documents` 的 7 个结构化列；`kb_asset_duplicate_groups` /
  `kb_asset_duplicate_members` 两张表及其列、索引、唯一约束；`facts` 的
  `confirmed_by`/`confirmed_at`。再通过 `information_schema.columns` 断言
  `kb_documents.review_status`、`facts.confirmed_by`、`facts.confirmed_at`
  的 `column_default` 全为 NULL（历史行不得被 DDL 盖章成已审核/已确认）。
- `test_v0330_migration_roundtrip_on_pg`：写入只含基线列的历史行，逐步降级
  `0015→0014→0013→0012`，逐层断言产物消失而历史数据（项目名、文档来源/状态、
  事实值/键）原样保留；重新 `upgrade head` 后断言产物完整重建、历史行新列回到 NULL；
  `finally` 强制恢复到 head，避免污染后续用例。

### 2.2 RC-05 并发修复在 PG 上的真实并发验证（117 个用例）

- `claim_next` 的 `FOR UPDATE SKIP LOCKED`：
  - 6 worker / 6 排队 Job 用 Barrier 同时 `claim_next()`：6 个互不重复的 owner，
    `attempts==1`、claim_token 两两不同，证明任一重复认领都会被抓出（3 轮）。
  - 一个事务用真实 `SELECT … FOR UPDATE` 锁住队首行，另一个线程的 `claim_next()`
    必须在 20s 内跳过它、认领下一行；这是 PG 行锁而非 Python 锁（1 个用例）。
  - 补充编译断言：同一语句在 PG 方言含 `FOR UPDATE`/`SKIP LOCKED`/`RETURNING`，
    SQLite 方言不含 `SKIP LOCKED`，并确认当前引擎确实是 PG。
- 两个 reaper 不能双失败/双退款：
  - 过期租约双回收：`["lost", "requeued"]`，仅 1 条 `queued` 事件（20 轮）。
  - 到达重试上限的计费 Job 双回收：`["failed", "lost"]`，仅 1 条 `failed` 事件，
    `refund_claimed=True`，余额 `100-6+4=98`，退款流水恰好 `[4]`（20 轮）。
  - 两个线程直接并发 `refund_charged_job`：返回 `[0, 4]`，余额 98，流水 `[4]`（20 轮）。
- marker settle 与 Job 退款的单一归属：
  - `settle_pending_charge` 与 TTL `reconcile_pending_charges` 并发：结果为
    `(6,0)` 或 `(0,1)`，余额回到 100、流水恰好 `[6]`、marker `refunded/6`、Job
    `refund_claimed=True` 且 `refund_source="marker"`（20 轮）。
  - marker 先退、迟到的 Job 失败路径退款返回 0（5 轮）；Job 先退、TTL 对账返回 0
    且 marker `cleared`（5 轮）——两种顺序都只有一个账本付款。
  - `mark_job_refund_settled` 并发：结果 `[False, True]`，归属标志与来源唯一（20 轮）。
- heartbeat 三振语义：
  - 真实条件 UPDATE 返回 0 行（token 被 reaper 清走、Job 回队列）时立即 abort，
    旧 owner 无法续租复活；全程不 mock（1 个用例）。
  - 前两次瞬态异常不 abort，第三次调用走真实 PG 条件 UPDATE 成功续租、租约时间
    前移、计数清零；随后连续三次异常才 abort（1 个用例）。

## 3. 验证命令与结果（实际执行）

```bash
# 静态检查（仅本轮新增 Python 文件）
.venv/bin/python -m ruff check tests/postgres/test_v0330_migration_concurrency.py
# All checks passed!
```

真实 PG（每次运行前新建/清空临时库 `rc12_migration_concurrency`）：

```bash
BIDCRAFT_TEST_PG_URL='postgresql+psycopg://postgres:@/rc12_migration_concurrency?host=/home/yao/.cache/bidcraft-v032-pgdata' \
  .venv/bin/python -m pytest -o addopts="" -q tests/postgres/test_v0330_migration_concurrency.py
# 第一次：119 passed in 63.65s (0:01:03)
# 第二次（重建临时库后重跑，确认并发用例稳定）：119 passed in 67.05s (0:01:07)
```

无 env 的干净跳过：

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/postgres/test_v0330_migration_concurrency.py
# 119 skipped in 0.52s
```

未执行：ruff format、全量 pytest、其他 agent 文件的检查；未提交/推送/发版/部署。

## 4. 失败复现与修复（测试先行过程中的真实失败）

1. **首次运行 67 failed / 52 passed**：计费 Job 的 seed 只写了 `done_steps`，漏写
   `charged`/`chapters`，`refund_charged_job` 按缺账本返回 0，导致所有“退款恰好一次”
   的余额/流水断言失败。已在 `_seed_running_job` 中补齐 `charged`/`chapters`/
   `user_id`/`billing_account_id`（与既有 PG reaper 测试同构），断言强度不变。
2. **`mark_job_refund_settled` 双 False**：两处 seed 依赖模型 `default=new_id` 却在
   flush 前读取 `job.id`，实际传入 `None`。已改为显式 `id=str(uuid.uuid4())`，不再依赖
   flush 时序。
3. **单次隔离运行的 fixture setup 报错（`DROP TABLE quality_findings` 不存在）**：
   临时库在前一次压测后处于不一致 schema 状态。改为每轮验证前对 scratch DB 执行
   `DROP DATABASE IF EXISTS` + `CREATE DATABASE`，不复用旧库；此后两轮均零失败。
4. `time -p` 命令在容器 shell 不可用（无 `/usr/bin/time`），改为直接采用 pytest 自报耗时，
   不影响测试。

以上均为测试脚手架缺陷，未发现生产代码缺陷；未降低或删除任何断言，未通过放宽
条件“修绿”用例。

## 5. 残留风险

- **pgserver 不是生产环境**：本报告结论仅对本地 PostgreSQL 16.2 / pgserver 成立；
  生产 PG 的扩展、权限、连接池与 PgBouncer 事务模式未覆盖，需上线前另行取证。
- **并发模型**：验证用同机多线程 + 独立 Session；多进程/多机真实争用未在本轮压测，
  但所有结论来自数据库条件 UPDATE 与行锁，而非进程内状态。
- **heartbeat 瞬态异常注入点**：为触发三振计数，异常在 `_renew_lease_once` 调用边界
  注入；成功路径调用真实函数执行 PG 条件 UPDATE，零行路径由完整真实的
  “租约被夺”用例覆盖。若未来把重试/计数逻辑下沉进 SQL，注入点需同步迁移。
- `SKIP LOCKED` 的编译断言是补充证据，核心证据是“锁住队首仍能立即认领下一行”和
  “N worker 认领 N 个不同 Job”的真实 PG 用例；不是合成/源码检查冒充执行。
- 迁移往返只走到历史边界 0012，0001-0012 的历史 revision 未改、未测；生产库升级
  路径仍需在正式变更窗口单独演练。

## 6. 回滚

- 本轮无任何生产代码、迁移或配置改动；回滚 = 删除
  `tests/postgres/test_v0330_migration_concurrency.py` 与本报告即可，无需数据库回滚。
- 临时库 `rc12_migration_concurrency` 可安全 `DROP DATABASE`；`postgres` 与其他既有库
  未被改动。
- 工作区中出现的其他 agent 的改动（`bidcraft/api/*`、`bidcraft/evaluation/learning.py`、
  RC-08/RC-09 测试与任务台账等）不属于本轮，未编辑、未纳入本轮回滚。
