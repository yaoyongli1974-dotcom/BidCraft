# BidCraft v0.31.2 阶段 1：Job 租约 NULL 安全修复报告

基线 HEAD：`74c41568bd57edff25ffb6cb53808b6869e3bc6c`

## 1. 原问题

`commit_fenced_result()` 的终态条件 UPDATE 曾把 `lease_expires_at IS NULL` 视为「租约有效」：

```python
or_(Job.lease_expires_at.is_(None), Job.lease_expires_at >= now)
```

而 `heartbeat()` 早已把 NULL lease 视为过期。两者不一致，可能让失去租约的旧 Worker 提交终态。

## 2. 修复

`bidcraft/jobqueue.py::commit_fenced_result()` 现在要求：

- `status == running`
- `claim_token == 当前 claim_context token`
- `lease_expires_at IS NOT NULL`
- `lease_expires_at >= now`

条件 UPDATE 影响 0 行即抛 `JobAborted`，业务写入与事件全部回滚。token 仍来自 `claim_context`，不从数据库重读。

## 3. 测试

新增 `tests/test_v0312_job_lease_null.py`（10 项）：有效/ NULL /过期 /错 token / failed / cancelled / Reaper 抢占 / 失败不写结果 / 失败不发事件。

强化既有测试（非空断言、未删测试）：
- `tests/test_worker_reliability_matrix.py::_seed_job` 补有效 lease。
- `tests/test_v0311_stage3_chapter_fence.py::_seed_job` 补有效 lease。

命令：

```bash
.venv/bin/python -m ruff check bidcraft tests
.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0312_job_lease_null.py \
  tests/test_worker_fencing_atomicity.py \
  tests/test_worker_reliability_matrix.py \
  tests/test_v030_stage4_fencing.py \
  tests/test_v030_stage4_similarity.py \
  tests/test_v0311_stage3_chapter_fence.py \
  tests/test_v0311_stage4_dynamic_matrix.py
```

结果：ruff 通过；**72 passed / 1 skipped**（skip 为 PG opt-in）。测试使用真实 Session 与事务，核心 UPDATE 未被 mock。

## 4. 残余风险

- PostgreSQL 真实并发需 opt-in CI/用户提供实例验证；本地仅 SQLite。
- 无迁移、无 API/模型变化；回滚只需恢复 `commit_fenced_result()` 条件与测试 helper。
