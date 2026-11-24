# BidCraft v0.31.0 阶段 4：全 Worker 可靠性审计报告

基线：分支 `quality/v0.31.0-reliability`，阶段 3 提交 `33b5592`。阶段 0～3 详见 `BIDCRAFT_V031_BASELINE_REPORT.md`、`BIDCRAFT_V031_WORKER_FENCING_REPORT.md`、`BIDCRAFT_V031_JOBQUEUE_REPORT.md`、`BIDCRAFT_V031_BILLING_CONSISTENCY_REPORT.md`。

## 1. 审计范围与已验证事实

| Worker | 入口 | 中止检查点 | 终态写入（本阶段改造后） |
|---|---|---|---|
| agent | `workers/agent.py::_run_agent_job` | `raise_if_aborted()` + `assert_claim_current()` | `finalize_job_result()`（原 `fenced_job()+session_scope()`） |
| compliance | `workers/compliance.py::_run_batch_check_job` | 入口/循环/终态 | `finalize_job_result()` |
| evaluation | `workers/evaluation.py::_run_evaluation_job` | 入口 + `_done()` | `finalize_job_result()` |
| export | `workers/export.py::_run_batch_export_job` | 入口/循环/终态 | `finalize_job_result()` |
| generation | `workers/generation.py::_run_generation_job` | 入口/章节前 `assert_generation_lease()`/终态 | `commit_fenced_result()`（与 marker settle、done 事件同事务） |
| images | `workers/images.py::_run_image_batch_job` | 入口/循环/终态 | `finalize_job_result()` |
| knowledge | `workers/knowledge.py::_run_kb_reindex_job` | 入口/循环/终态 | `finalize_job_result()` |
| similarity | `workers/similarity.py::_run_similarity_job` | 入口/计算后/最终写前（新 Session） | `commit_fenced_result()`（阶段 1 已完成） |

所有 worker 均 `except JobAborted` 在前、通用异常在后；中止不写 failed、不发 failed 事件、不退款，由 reaper 重新排队。

## 2. 本阶段修复

1. `bidcraft/jobqueue.py` 新增 `finalize_job_result(job_id, *, status, result, message, progress, event_kind)`：复用阶段 1 的 `commit_fenced_result()`，把**条件 UPDATE（token + running + lease 未过期）、status/result 写入、终态事件**放进同一个 `session_scope()`；任一步失败整笔回滚。
2. 7 个 worker（agent/compliance/evaluation/export/images/knowledge/generation）的终态写入全部改为上述事务级 fencing；generation 直接在 `commit_fenced_result()` 块内完成 marker settle + done 事件，租约释放在事务提交后执行，避免嵌套 SQLite writer。
3. `tests/test_worker_reliability_matrix.py`（11 项）静态校验 8 个 worker 均具备 fencing 终态写入、`raise_if_aborted()` 与 `except JobAborted`，并动态验证终态写入成功落盘 status/result/event，而 token 失效或缺失时整笔不落盘、不发事件。

## 3. 不变量与兼容性

- 不做 API 语义变更：事件种类、payload 形状、消息文本保持原样。
- 不做 schema 变更：无新迁移；仅复用已有 `claim_token`/`lease_expires_at` 字段。
- SQLite 与 PG 使用同一套条件 UPDATE，无 PG 方言；PG 上条件 UPDATE 持有行锁至提交。
- 未删除或跳过任何测试；阶段 1～3 的既有回归全部保留。

## 4. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | All checks passed |
| `git diff --check` | 无空白错误 |
| `pytest tests/test_worker_reliability_matrix.py tests/test_v030_stage4_fencing.py` | **27 passed** |
| `pytest tests/test_v030_stage4_fencing.py tests/test_v030_stage4_similarity.py tests/test_worker_fencing_atomicity.py tests/test_batch7_orchestration.py tests/test_batch7_evaluation.py tests/test_batch7_quality.py tests/test_billing_concurrency_v031.py` | **63 passed / 1 skipped**（PG opt-in） |
| 完整后端套件 | **1658 passed, 18 skipped, 1 warning in 749.38s**（`/tmp/opencode/v031_stage4_full.out`，零失败） |

## 5. 残留风险与回滚

- 通用 worker 的非终态进度写入仍用 `fenced_job()`（读检查），接受短暂的进度事件重复；终态与账务已做到事务级原子。
- PG 真实并发终态抢占仍依赖 CI 的 opt-in PG job；本地仅 SQLite 验证。
- 回滚：恢复 7 个 worker 文件与 `bidcraft/jobqueue.py` 的终态写入，删除 `tests/test_worker_reliability_matrix.py` 与本报告；无需迁移回滚。
