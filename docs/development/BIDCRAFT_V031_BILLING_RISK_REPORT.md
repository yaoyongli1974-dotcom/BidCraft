# BidCraft v0.31.0 阶段 3：账务一致性与业务规则风险报告

## 1. 已完成的安全修复（不涉及业务规则变更）

| 项 | 修复 | 验证 |
|---|---|---|
| 异步 `generate_all` 无扣费 marker | 扣费同事务创建 `PendingCharge`，marker.note 绑定 `job:<id>`，Job.payload 增加 `pending_charge_id` / `billing_account_id`。 | `test_generate_all_records_async_charge_marker` |
| 崩溃后按项目章节差值错误归因 | 对账与结算优先读取 Job 的 `done_steps` 台账，legacy marker 才回退到项目差值。 | `test_reconcile_uses_job_ledger_not_project_wide_delta` |
| 子账号删除后异步退款丢失 | `refund_charged_job` 优先用 Job 记录的付款账号；marker 路径继续用 `billing_account_id`。 | `test_refund_charged_job_uses_billing_account` |
| 退款失败无兜底 | 异步生成全程刷新 marker 心跳；失败/成功都走 marker 结算，DB 条件领取保证幂等；未结 marker 由 reaper/CLI/启动对账重试。 | 既有 + 新增账务测试 |
| 负数对账 TTL | `reconcile_pending_charges(older_than_seconds<0)` 抛 `ValueError`。 | `test_negative_reconcile_ttl_is_rejected` |

以上改动**没有**修改历史迁移、没有删除测试、没有降低门禁、没有改生产配置，也没有改 API 请求/响应结构。

## 2. 需要用户决策的业务规则变更

### 现状

- 同步生成：`generate_chapter` / `write` 没有在途检查。
- 异步生成：`generate_all` 只通过 `_cancel_stale_jobs()` 做 check-then-insert 软检查；并发请求可同时通过，且该检查无法看到同步生成。
- `Job` 没有 `(project_id, kind, status)` 唯一约束；`SectionEditLock` 只用于人工编辑锁。
- 后果：同一项目可能同时跑两次生成，导致重复扣费、章节互覆盖、退款归因混乱。

### 任务书要求

“同一项目并发生成是否禁止”属于业务规则变化，按其定义必须暂停、出具风险报告并等待确认；不能自行实施。

### 可选方案

| 方案 | 内容 | 影响 |
|---|---|---|
| A（推荐） | **禁止同项目并发生成**：同步与异步统一走 DB 条件占用（新增轻量“生成占用”记录或唯一部分索引），第二请求返回 409/等待；占用随任务终态释放，崩溃可恢复。 | 行为变化：用户同时点两次生成时第二次被拒绝；需要新增一张小表或新增唯一索引（需新 Alembic migration，仅新增，不改历史）。 |
| B | **排队串行**：第二请求不拒绝，而是排队等待同一项目的在途任务完成后再执行。 | 用户体验更好，但需跨请求等待/唤醒机制，复杂度与测试成本更高；仍改变当前“可同时提交”的行为。 |
| C | **保持现状**，仅记录文档与监控，不禁止。 | 无行为变化，但重复扣费/覆盖风险继续存在；不符合本阶段“账务一致性”目标。 |

### 建议

选择 **A**：用最小数据库约束把并发生成挡在第一道门，冲突时明确返回 409，避免静默重复扣费；崩溃恢复通过 lease/占用超时处理，不引入跨请求等待。该方案需要新增迁移（不修改历史 migration），属于可控风险，但仍是业务规则变更，需用户确认。

## 3. 明确不修改的内容

本风险报告不涉及、也不建议修改 `_cancel_stale_jobs()` 的周期性/心跳语义；该文件属于 `services/job_support.py`，其超时策略调整需单独确认。阶段 3 的安全修复完全没有触碰该函数。
