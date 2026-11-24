# BidCraft v0.30.0 阶段 3 报告：任务队列与计费可靠性

- 分支：`quality/v0.30.0-reliability`
- 前置：阶段 0 `c9b9170` / 阶段 1 `d6d0338` / 阶段 2 `fc6b913` / 风险报告 `a8d364f`
- 用户批准：**完整包 A+B**（心跳 + 完整账务加固，含新迁移 0011）
- 约束遵守：未推送 main、未发版、未改生产、未删测试、未改历史迁移 0001–0010、未降低扫描阈值。

## 1. 问题与修复对照

| 编号 | 问题 | 修复 | 证据 |
|---|---|---|---|
| V030-07 | 同步生成无 Job/心跳，超过 TTL 会被误判崩溃并全额退款 | `pending_charge_heartbeat` 在同步生成期间续期 `updated_at`；`reconcile_pending_charges` 改按 `updated_at` 判定，移除项目级 live-job 误判 | `bidcraft/jobqueue.py`、`bidcraft/api/routes/projects.py`、`tests/test_v030_stage3_core.py`、`tests/test_v030_stage3_routes.py` |
| V030-08a | 写后崩溃对账忽略已写章节，退款 100% | `PendingCharge.sections_at_charge` 快照 + 对账时按实际新增 Section 计算已 earned 金额 | 迁移 0011、`tests/test_v030_stage3_core.py` |
| V030-08b | `_cancel_stale_jobs/_fail_job` 不退款，可能重复扣费 | `_fail_job` 同事务退款；失败事件携带 `refunded_credits` | `bidcraft/services/job_support.py`、`tests/test_v030_stage3_routes.py` |
| V030-08c | 异步退款靠 JSON 读改写，并发可双退 | `Job.refund_claimed` DB 条件抢占（幂等基准），payload 标记保留作兼容 | 迁移 0011、`bidcraft/jobqueue.py`、`tests/test_v030_stage3_core.py` |
| V030-08d | `_fail_job` 未清 fence，僵死 worker 可把 failed 覆盖成 done | 失败时清空 `claim_token`/lease 字段 | `bidcraft/services/job_support.py`、`tests/test_v030_stage3_routes.py` |
| V030-08e | 退款失败只写日志不可观测 | 新增 `refund_failed` JobEvent，失败状态仍落库 | `bidcraft/services/job_support.py`、`tests/test_v030_stage3_routes.py` |
| V030-08f | 退款 ledger 无 job/marker 溯源 | `refund_chapters(origin=...)` 在 note 写 `[job:id]` / `[marker:id]`，保留 `ref_type/ref_id` | `bidcraft/billing.py`、`bidcraft/jobqueue.py`、`tests/test_v030_stage3_routes.py` |
| V030-08g | 账务/并发场景测试不足 | 新增 15 个测试（core 8 + routes 7），并更新 6 处旧 TTL 测试为 heartbeat 语义 | `tests/test_v030_stage3_*.py`、`tests/test_batch1_*`、`tests/test_fix2_data_integrity.py`、`tests/test_fix3_backend_charges.py` |

## 2. 变更清单

### 新增迁移 0011_stage3_accounting

- `pending_charges.sections_at_charge: Integer NOT NULL DEFAULT 0`
- `jobs.refund_claimed: Boolean NOT NULL DEFAULT 0`
- 可逆、幂等，带 `inspect` 守卫；PG 用 `if_not_exists/if_exists`，SQLite 兼容；不修改历史迁移。

### 核心队列（`bidcraft/jobqueue.py`）

- `begin_pending_charge(..., sections_at_charge=None)` 自动 snapshot 当前章节数。
- `touch_pending_charge()` + `pending_charge_heartbeat()` 守护线程续期。
- `reconcile_pending_charges()`：`updated_at` cutoff；按 `sections_at_charge` 计算已写章节；无退款时落 `cleared`；继续条件抢占、孤儿付款人保护。
- `refund_charged_job()`：`refund_claimed` 条件抢占先于退款，重复调用无副作用。
- 三处退款调用均带 `origin=`；保留 VIP 退款取整公式不变。
- 修复 `_persisted_sections()` 误用不存在的 `Section.project_id`，改为经 `OutlineNode.project_id` 关联。

### 路由/支撑

- `projects.py` 两个同步生成端点包裹 heartbeat，`except BaseException` + `settled` 标记保证中断也只退一次。
- `job_support.py::_fail_job` 同事务退款 + 清 fence + 退款失败事件。
- `billing.py::refund_chapters` 新增 `origin` 仅影响 note。
- `config.py` TTL 注释同步为 heartbeat 语义。

## 3. 验证结果

| 项目 | 命令 | 结果 |
|---|---|---|
| Lint | `ruff check bidcraft tests scripts` | All checks passed |
| diff | `git diff --check` | 通过 |
| 定向 | `pytest tests/test_alembic.py test_batch1_* test_fix3_* test_fix2_data_integrity.py test_v030_stage3_*` | **54 passed**, 1 warning |
| 全量后端 | `pytest -o addopts="" -q` | 首轮 1525 passed / 2 failed / 16 skipped；2 处失败为评估重试用例仍在旧 claim token 下直接重跑，已按"失败后重新入队认领"的真实语义更新（未删除断言），复跑结果见阶段提交信息 |

## 4. 兼容性与风险

- API 响应形状不变；仅退款 ledger note 增加 `[job:id]` / `[marker:id]`，不影响按 project 查询。
- 新幂等列默认 0/FALSE，旧行天然兼容；回滚执行 0011 downgrade 即可。
- 本地无 PG，并发退款的 PG 变体仅在 CI 的可选 PG job 运行；SQLite 线程矩阵已覆盖。
- 语义变更：对账不再因项目上的无关 Job 阻塞；这是批准方案的有意修正，旧断言已更新而非删除。

## 5. 回滚

1. `alembic downgrade 0010_batch1_billing_reliability`
2. 还原 `bidcraft/{jobqueue,models,config,billing}.py`、`bidcraft/api/routes/projects.py`、`bidcraft/services/job_support.py`
3. 删除迁移 0011 与 `tests/test_v030_stage3_*.py`，并还原 6 个被更新的旧测试。
