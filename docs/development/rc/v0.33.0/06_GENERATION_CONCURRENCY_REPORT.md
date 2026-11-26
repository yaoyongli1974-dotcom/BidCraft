# RC-05 生成并发与计费单一归属报告

## 1. 范围与基线

- 基线分支：`fix/v0.33.0-rc-reliability`
- 本模块起点：`241a9bf`（RC-03 已提交）；RC-04 由独立代理并行实施。
- 复核文件：`bidcraft/jobqueue.py`、`bidcraft/services/job_support.py`、`bidcraft/generation/writer.py`、`bidcraft/workers/generation.py`、`bidcraft/api/shared.py`。

## 2. 审计确认的问题

1. **P0 双账本重复退款**：worker 成功/失败路径先用 `settle_pending_charge()` 退 PendingCharge marker，随后 `_fail_job()` 又用 `refund_charged_job()` 按 Job 账本退款；两套幂等标志互不感知，同一笔扣费可退两次。Reaper `_set_failed()` + TTL `reconcile_pending_charges()` 有同样缺口。
2. **P1 取消竞态**：`_cancel_stale_jobs()` 调用 `_fail_job()` 后忽略结果；条件状态转换失败（已被其他路径判定失败）仍返回 `None`，调用方会再入队一个重复 Job。
3. **P1 长会话失败写入未 fencing**：`writer.generate_document()` 在 `ModelUnavailable` 分支直接用长生成 Session 写 `Section.status='failed'`，租约被回收后仍可能覆盖新 owner 的状态。
4. **P1 force 覆盖人工确认**：`_pending_chapters(force=True)` 把 confirmed/locked 章节也计入待重生成。
5. **P2 瞬态心跳错误即中止**：`_heartbeat()` 首次 DB 异常就设置 abort。
6. **P2 PG claim 无 SKIP LOCKED**：`_claim_statement()` 在 PG 并发认领时依赖 EvalPlanQual 兜底。
7. **P2 自动失败未释放 GenerationLease**：项目级互斥锁可能一直留到 TTL。

## 3. 测试先行

新增 `tests/test_v0330_generation_concurrency.py`，9 个真实 DB 用例（先于生产修复编写）：

- marker settle → `_fail_job` 只能退一次；
- `_fail_job` 先退 → `reconcile_pending_charges(0)` 不再退；
- `_cancel_stale_jobs` 竞态失败时返回已占用 Job；
- `fail_chapter` 钩子被调用且长 Session 不写 `Section.status`；
- `force=True` 跳过 confirmed/locked；
- heartbeat 允许两次瞬态失败、第三次才 abort；
- `_claim_statement(..., skip_locked=True)` PG 编译含 `FOR UPDATE SKIP LOCKED`，SQLite 不含；
- 自动失败释放持久化 GenerationLease；
- `mark_job_refund_settled()` 条件认领只有一个赢家。

首次运行：9 failed / 0 passed，精确复现上述缺口。

## 4. 生产修复

### 4.1 统一退款归属（单一账本赢家）

`bidcraft/jobqueue.py` 新增：

- `_marker_job_id(marker)`：从 `job:<id>|...` marker note 解析 Job ID；
- `mark_job_refund_settled(db, job_id, *, source)`：条件 `UPDATE jobs SET refund_claimed=1 WHERE refund_claimed=0`，成功后写 `payload['refunded']=True` 与 `payload['refund_source']='marker'|'job'`；
- `clear_job_marker(db, job)`：Job 账本赢时把 `payload['pending_charge_id']` 对应 marker 置 `cleared`。

接线：

- `settle_pending_charge()` 成功后调用 `mark_job_refund_settled(..., source='marker')`；
- `reconcile_pending_charges()` 结算 marker 后同样调用；
- `refund_charged_job()` 成功领取 Job 账本后写 `refund_source='job'` 并 `clear_job_marker()`。

因此无论 marker 先退、Job 先退、reaper 后到或 TTL 对账后到，只有一个账本实际付款。

### 4.2 失败路径与租约

- `_fail_job()` 返回 `bool`：真正赢得 `transition_job` 才返回 True；丢失竞态返回 False；终态已认领退款返回 False。
- `_cancel_stale_jobs()` 在 `_fail_job()` 返回 False 时返回 fresh Job 行，阻止重复入队。
- `_fail_job()` 读取 `payload['generation_lease_token']`，成功/终态处理完成后调用 `release_generation_lease()`，无等待 TTL。

### 4.3 章节失败 fencing

- `generation/writer.py::generate_document()` 新增 `fail_chapter(info)` 钩子；`ModelUnavailable` 分支优先调用它，旧调用方仍保留原本地写入。
- `workers/generation.py` 提供 fenced 实现：先 `work_db.commit()` 释放长事务，再在 `fenced_chapter_write()` 内写 `Section.status='failed'`。

### 4.4 队列与门禁加固

- `api/shared.py::_pending_chapters(force=True)` 跳过 `review_status in {'confirmed','locked'}` 的章节。
- `_heartbeat()` 使用三次连续失败计数，成功 renewal 清零。
- `_claim_statement(..., skip_locked=False)` 新增 PG-only `FOR UPDATE SKIP LOCKED`；`claim_next()` 依据实际 driver dialect 决定开启，SQLite 行为不变。

## 5. 验证

命令与结果（实际执行）：

```bash
.venv/bin/python -m ruff check bidcraft tests scripts
# All checks passed!

.venv/bin/python -m pytest -o addopts="" -q tests/test_v0330_generation_concurrency.py tests/test_v0330_kb_lifecycle.py tests/test_jobqueue_race_conditions.py tests/test_jobqueue.py tests/test_v0311_stage5_billing_races.py tests/test_worker_reliability_matrix.py tests/test_v032_chapter_planning.py tests/test_v030_stage3_routes.py tests/test_api_package.py
# 99 passed, 1 skipped, 1 warning in 36.88s

.venv/bin/python -m pytest -o addopts="" -q
# 2324 passed, 888 skipped, 1 warning in 999.36s
```

全量后端回归零失败；888 skipped 为既定 PG/可选环境跳过，无本模块新增跳过。

中间回归发现 `tests/test_jobqueue_race_conditions.py::test_heartbeat_failure_sets_abort` 仍按“首次异常即 abort”断言；已把 stop 对象改为 `_ThreeStrikeStop`（允许三次 tick）并断言 `stop.calls >= 3`，断言强度不降低，符合新的三次计数语义。

## 6. 兼容性与风险

- 无新增迁移、无 API 路由/响应契约变化、无前端改动。
- `settle_pending_charge()` 的 `written`/退款金额公式未变；仅追加单一归属标志。
- `_fail_job()` 由 `None` 改为 `bool`，现有忽略返回值的调用方不受影响；`_cancel_stale_jobs()` 显式消费结果。
- PG `SKIP LOCKED` 仅通过 SQL 编译断言验证；真实 PG 并发压测留待 RC-12。
- 外部模型调用、LibreOffice/对象存储副作用不在本模块范围。

## 7. 回滚

按需回退：`bidcraft/jobqueue.py` 的单一归属 helper/接线与 claim/heartbeat、`services/job_support.py` 的 bool/租约释放、`generation/writer.py` 与 `workers/generation.py` 的 fail hook、`api/shared.py` 的确认章节过滤，并删除 `tests/test_v0330_generation_concurrency.py` 与本报告。无需数据库回滚。
