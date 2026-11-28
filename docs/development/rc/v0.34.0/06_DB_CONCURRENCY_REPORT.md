# BidCraft v0.34.0 阶段 4：数据库/任务并发加固报告（P1）

## 1. 范围与文件所有权

本阶段只处理任务书的 P1 五项：退款锁顺序、审批链 JSON 并发写、同步生成章节门禁、失败退款有界重试、启动恢复不误杀。严格按分配所有权实施：

| 类型 | 文件 |
|---|---|
| 生产修改 | `bidcraft/jobqueue.py`、`bidcraft/boq.py`、`bidcraft/commercial.py`、`bidcraft/api/routes/projects.py` |
| 新增测试 | `tests/test_v0340_db_concurrency.py`、`tests/test_v0340_approval_concurrency.py`、`tests/postgres/test_v0340_db_concurrency.py` |
| 新增报告 | `docs/development/rc/v0.34.0/06_DB_CONCURRENCY_REPORT.md` |

未编辑 `models.py`、迁移、`billing.py`、`config.py`、`admin.py`、web 文件、CI、`test_alembic.py` 或其他任务拥有的文件。无新迁移、无 API 语义变更、无 Python 全局锁、无 mock-only 证据、无 commit/push、未执行 `ruff format`、未删除或降低任何断言。

## 2. 测试先行证据（生产修复前）

```bash
.venv/bin/python -m pytest -o addopts= -q \
  tests/test_v0340_db_concurrency.py \
  tests/test_v0340_approval_concurrency.py \
  tests/postgres/test_v0340_db_concurrency.py
# 13 failed, 15 passed, 41 skipped in 8.18s
```

典型真实失败（均来自新测试，未用 skip/xfail 掩盖）：

- `test_failed_refund_is_retried_once_by_reconcile`：`AssertionError: assert 0 == 1`（失败退款没有任何自动重试，`payload.refund_retries` 不存在）。
- `test_sync_generation_stops_when_lease_is_aborted_mid_run`：`Failed: DID NOT RAISE JobAborted`（租约在第一章后已释放，同步路由仍继续写章节）。
- `test_boq_concurrent_countersign_both_persist` / `test_commercial_concurrent_countersign_both_persist`：两个审批人同时签名后只剩一人的记录，终态停在 `submitted`。
- `test_approval_claim_helper_guards_stale_json`：`ImportError: cannot import name 'claim_json_revision'`（条件抢占助手尚不存在）。
- PG 模块 41 个用例全部干净跳过（`BIDCRAFT_TEST_PG_URL` 未设置），不计为通过结论。

## 3. 生产改动（按函数）

### 3.1 `bidcraft/jobqueue.py` — 退款单一所有权与锁顺序

- 新增 `MAX_REFUND_RETRIES = 3`。
- 新增便携式条件语句/助手：`_claim_job_refund_statement` / `_claim_job_refund`、`_release_job_refund_statement` / `_release_job_refund`、`_lock_marker_statement` / `_lock_marker`、`_record_job_refund_claimed`。`Job.refund_claimed` 用 `.is_(False)` / `.is_(True)` 比较，SQLite 与 PostgreSQL（BOOLEAN）共用同一条 SQL；`_lock_marker` 用无状态变化的 `UPDATE ... SET status=status WHERE status='pending'` 取行锁，PG 持锁至提交，SQLite 由单写事务串行化。
- `refund_charged_job`：顺序改为 **job claim → marker lock → user 账本写入**；退款写入失败时释放 job claim 并保留 marker 可重试；成功才清 marker 并写 payload。
- 新增 `_settle_marker_refund` 统一 marker 结算与 TTL 对账：`job claim（若 marker.note 关联 Job）→ marker claim → user 账本写入 → 关闭双账本`。两条 marker 入口（`settle_pending_charge` / `reconcile_pending_charges`）都走它；抢不到 job claim 时不再碰 marker、不入账，从根上消除跨账本双退与环形等待。
- `mark_job_refund_settled` 保持原语义（条件抢占 + payload 记录来源），内部复用上述助手；直接调用它的既有测试与 `_fail_job` 路径行为不变。
- 新增 `retry_failed_refunds(db, max_retries=MAX_REFUND_RETRIES)`：扫描 `status='failed' AND refund_claimed=false AND kind='generate'` 且 payload 有 charged/chapters 的任务；每次尝试在 SAVEPOINT 内调用真实 `refund_charged_job`，失败则把 `refund_retries` 写回 `Job.payload` 并继续；达到预算后不再尝试。`reconcile_pending_charges` 在 marker 循环后调用它，因此 reaper 周期与启动恢复（都调用 reconcile）自动获得重试。成功入账仍由唯一 job claim 保证，不会重复贷记。
- 新增 `generation_lease_is_live(project_id, token)`：用独立短 Session 校验 GenerationLease，避免长生成会话的旧快照看不到租约被释放。

### 3.2 `bidcraft/boq.py` / `bidcraft/commercial.py` — 审批链条件抢占

- `boq.json_column_stamp(db, column, row_id)` 读取 JSON 列的**存储原文**作为乐观锁 stamp；SQLite TEXT 与 PG JSON 都能用，且不受序列化空白/键序差异影响。
- `boq.claim_json_revision(..., expected_stamp, from_status, **values)`：单条 `UPDATE ... WHERE id=? AND status=from_status AND CAST(json_col AS TEXT)=expected_stamp`。PG 下输家等待赢家提交后 EvalPlanQual 重检匹配 0 行；SQLite 下由单写事务串行化。无 Python 锁、无新增列。
- `review_version` 与 `review_commercial` 改为有界重读循环（`APPROVAL_CLAIM_ATTEMPTS = 8`）：
  - 每轮先 `db.expire()` 丢弃身份映射旧快照；
  - **审批记录从同一 stamp 的 `json.loads()` 解码**（曾出现过“ORM 读到旧 approvals、stamp 读到新行”的 5ms TOCTOU，导致覆盖同事签名，已通过同源快照修复）；
  - 重新计算阶段、授权与终态判定后，才用条件 UPDATE 写入；抢占失败则重新读取并重算，8 次仍冲突才抛 `BoqConflictError`。
- 业务规则不变：会签 all / 或签 any、金额/包件/币种门槛、重复签名 403、非当前级审批人 403、终态只审一次、审批通过收回旧版本，均沿用原有函数与判定。
- `commercial._review_context` 增加可选 `validation_override`，只服务于同源快照；旧调用方行为不变。

### 3.3 `bidcraft/api/routes/projects.py` — 同步生成逐章门禁

- 新增 `_sync_generation_guard(project_id, token, abort)`：先查心跳 abort / 全局 shutdown，再用 `generation_lease_is_live` 在**独立 Session** 校验租约；失效即置 abort 并抛 `JobAborted`。
- `generate_chapter` 与 `write` 两个同步路由的 `generate_document(...)` 都传入 `before_chapter=_sync_generation_guard(...)`，与异步 worker 的 `before_chapter` 一样在每个章节模型调用/写库之前检查； abort 后生成会话抛错回滚，路由按已落库章节结算（本次异常发生在 `report` 赋值前，因此全额退款），不留半章、不重复退款。

## 4. 新增测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_v0340_db_concurrency.py` | 真实 SQLite 线程 + Barrier：job 退款 vs marker settle（5 轮）、job 退款 vs TTL reconcile（5 轮），每轮恰退 4 点、账本只有 1 笔正向流水；瞬态失败后重试成功（1 次失败→第 2 轮成功）、永久失败有界（恰好 3 次尝试、计费余额不变）、预算耗尽后不再重试；启动恢复只回退过期租约、不 mass-fail/不退款；同步 `write` 中途释放租约后抛 `JobAborted`、DB 无任何章节正文、marker 全额退款。 |
| `tests/test_v0340_approval_concurrency.py` | BOQ 与商务标各 6 轮真实双线程会签：两条审批记录都落库、终态 `approved`、审计各 2 条；重复签名仍 403；条件抢占助手对过期 stamp 返回 False 且不覆盖在先记录。 |
| `tests/postgres/test_v0340_db_concurrency.py` | 真实 PG（opt-in）：每类 20 轮双线程 job 退款 vs marker settle / reconcile，`SET LOCAL statement_timeout='10000'` 捕获死锁，线程 45s 硬超时；断言无线程异常、恰退 4 点、仅 1 条正向流水、双账本关闭；并校验三条生产条件语句在 PG/SQLite 双方言可编译。无 `BIDCRAFT_TEST_PG_URL` 时整模块干净跳过。 |

账户、账本、租约、审批记录、事件等断言全部直接查询真实数据库；测试里仅注入账本写入的瞬态异常和模型客户端（无模型服务环境），没有替换任何 DB 操作，也没有 Python 业务锁。

## 5. 验证结果（实际执行的命令）

```bash
.venv/bin/ruff check bidcraft tests
# All checks passed!

.venv/bin/ruff check bidcraft/jobqueue.py bidcraft/boq.py bidcraft/commercial.py \
  bidcraft/api/routes/projects.py \
  tests/test_v0340_db_concurrency.py tests/test_v0340_approval_concurrency.py \
  tests/postgres/test_v0340_db_concurrency.py
# All checks passed!

.venv/bin/python -m pytest -o addopts= -q \
  tests/test_v0340_db_concurrency.py tests/test_v0340_approval_concurrency.py \
  tests/postgres/test_v0340_db_concurrency.py
# 28 passed, 41 skipped in 8.28s

.venv/bin/python -m pytest -o addopts= -q tests/postgres/test_v0340_db_concurrency.py
# 41 skipped in 0.51s   （BIDCRAFT_TEST_PG_URL 未设置，干净跳过）

.venv/bin/python -m pytest -o addopts= -q \
  tests/test_jobqueue.py tests/test_jobqueue_race_conditions.py \
  tests/test_v0311_stage5_billing_races.py tests/test_v0330_generation_concurrency.py \
  tests/test_worker_reliability_matrix.py tests/test_v0340_job_accounting.py \
  tests/test_batch1_charge_refund.py tests/test_batch1_pending_charge.py
# 79 passed, 1 skipped in 16.63s

.venv/bin/python -m pytest -o addopts= -q \
  tests/test_quote_approval.py tests/test_quote_approval_chain.py \
  tests/test_quote_approval_condition.py tests/test_quote_approval_conditions.py \
  tests/test_quote_approval_parallel.py tests/test_commercial_approval.py \
  tests/test_boq_approval_rules_v032.py tests/test_boq_approval_rules_api_v032.py \
  tests/test_boq_approval_rules_integration_v032.py tests/test_approvals_pending.py \
  tests/test_batch7_approvals.py
# 97 passed, 1 warning in 144.36s (0:02:24)

.venv/bin/python -m pytest -o addopts= -q \
  [上述新测试 + 上述 jobqueue/审批回归 + test_batch3_generation_fixes.py \
   test_v030_stage3_routes.py test_commercial_gate.py]
# 229 passed, 42 skipped, 1 warning in 236.47s (0:03:56)
```

中途有一次 `ruff check bidcraft tests` 报出并行任务正在编辑的 `pipeline.py` / `user_docs.py` 共 5 个错误；这些文件不属于本阶段，未代修，最终重跑为 `All checks passed!`。

定向回归中另有 17 个失败集中在 `tests/test_reliability_writer.py`、`tests/test_v0311_stage3_chapter_fence.py`、`tests/test_v0312_generation_dual_fencing.py`、`test_api_boq.py` 的两个用例，根因是并行文件完整性任务正在改动 `bidcraft/pipeline.py`：夹具上传文本被内容嗅探拒绝（`ValueError: 招标文件未提取到文本…`、上传返回 400）。失败发生在夹具 `_prepare` / 上传阶段，未触及本阶段四个生产文件；未修改这些文件或放宽断言。未运行全量 pytest。

## 6. 结论与残余风险

结论口径：SQLite 真实并发与会话路径 **PASS**；PostgreSQL 真实并发项 **NOT VERIFIED**（本机未注入 `BIDCRAFT_TEST_PG_URL`，测试以 clean skip 记录，不得算作通过）。整体 **CONDITIONAL**：PG 环境注入后按第 5 节命令重跑 41 个 opt-in 用例即可转正。建议 **CONDITIONAL GO**。

残余风险：

1. PG 行锁/死锁消除只由代码锁顺序、语句编译检查和 opt-in 测试覆盖；未在本机真实 PG 上执行，需在交付环境注入 `BIDCRAFT_TEST_PG_URL` 验证。测试内的 `statement_timeout=10s` 只为快速暴露死锁，不代表生产超时策略。
2. 审批链条件抢占比较 JSON **存储原文**。所有写入均经 SQLAlchemy JSON 序列化，且 stamp 直接取自数据库；若外部工具绕开应用直接改写 JSON 格式，stamp 可能失配并触发最多 8 次重读后 409，不会静默覆盖。
3. 退款重试预算为每条 Job 3 次，计数存 `Job.payload['refund_retries']`；预算耗尽后只能人工对账。重试成功不发新的 SSE 事件（首次失败时的 `refund_failed` 仍在），前端需靠轮询/账本确认最终状态。
4. 同步生成每章都用独立 Session 查租约，增加一次短查询；在超长章节数的同步生成中开销线性增长，但换来的是 abort 后不再提交任何章节。
5. 并行任务对 `pipeline.py` 的改动使部分既有夹具失败（见第 5 节），本阶段未触碰；需在合并前由对应任务修复并重跑这些回归。
6. 未运行全量测试套件（按任务要求仅定向验证）。

## 7. 回滚

无需迁移/API 回滚。若需整体撤销本阶段（仅本任务拥有的文件）：

```bash
git restore bidcraft/jobqueue.py bidcraft/boq.py bidcraft/commercial.py \
  bidcraft/api/routes/projects.py
rm tests/test_v0340_db_concurrency.py tests/test_v0340_approval_concurrency.py \
  tests/postgres/test_v0340_db_concurrency.py \
  docs/development/rc/v0.34.0/06_DB_CONCURRENCY_REPORT.md
```

仅回滚生产代码而保留测试，可直接复现第 2 节的测试先行失败，便于复核。注意 `git restore` 时不要带入其他并行任务在同一工作区的改动（严格按上面的文件清单操作）。
