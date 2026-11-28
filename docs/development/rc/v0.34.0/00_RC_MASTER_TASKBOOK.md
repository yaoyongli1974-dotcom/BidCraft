# BidCraft v0.34.0 RC 专项主任务书

## 1. 范围与基线

- 专项分支：`fix/v0.34.0-commercial-reliability`
- 基线 Commit：`f333c27b86ba6e0ce6b72b0b6a957531dfeaf5d1`
- 基线版本：`0.33.2`
- 基线 Alembic Head：`0016_fact_provenance`
- 基线 CI：Run `46092725093`，9/9 success
- 基线全量回归：`2427 passed, 1008 skipped, 1 warning in 1042.38s`

## 2. 强约束

- 使用当前真实代码，不回滚、不 reset --hard / clean -fd。
- 不推送 main、不建 tag、不创建 Release、不部署生产、不操作生产库/数据。
- 不修改历史迁移 `0001`–`0016`；新增迁移必须幂等、可逆、SQLite/PG 兼容。
- 不删除测试、不降低断言、不用 skip/xfail/mock 掩盖真实失败。
- 不改变扣费规则、VIP 权益、订单语义、导出门禁等业务逻辑。
- 每阶段必须有代码、测试、报告、真实命令与结果。

## 3. 阶段与报告

| 阶段 | 内容 | 报告 |
|---|---|---|
| 0 | 基线保护、架构与风险盘点 | `01_BASELINE_REPORT.md`, `02_RISK_AND_CURRENT_TEST_REPORT.md` |
| 1 | 支付回调安全 | `03_PAYMENT_SECURITY_REPORT.md` |
| 2 | 资金一致性与原子抢占 | `04_FINANCIAL_CONSISTENCY_REPORT.md` |
| 3 | 任务失败退款与租约恢复 | `05_JOB_BILLING_REPORT.md` |
| 4 | 数据库并发与迁移 | `06_DB_CONCURRENCY_REPORT.md` |
| 5 | 文件完整性与上传原子性 | `07_FILE_INTEGRITY_REPORT.md` |
| 6 | 权限、IDOR、审计 | `08_PERMISSION_AUDIT_REPORT.md` |
| 7 | 备份恢复与运维 | `09_BACKUP_RESTORE_REPORT.md` |
| 8 | 前端终态与轮询闭环 | `10_TEST_REPORT.md`（前端部分） |
| 9 | CI 门禁与全量验证 | `11_CI_REPORT.md`, `10_TEST_REPORT.md` |
| 10 | 迁移、部署、回滚与最终验收 | `12_MIGRATION_REPORT.md`, `13_DEPLOYMENT_GUIDE.md`, `14_ROLLBACK_GUIDE.md`, `15_RELEASE_NOTES.md` |

## 4. 当前进展

- 阶段 0：已提交 `69eb538`。
- P0 支付安全、资金原子性、任务退款/租约恢复：实现完成，定向验证通过；合并全量回归 `2535 passed, 1008 skipped, 2 warnings in 988.14s`，0 failures。
- 全量回归第 2 个 warning 为 `tests/test_kb_reindex_job.py` 退出后遗留 worker 线程读取已被重置的测试库（no such table: jobs），属于测试环境线程生命周期噪音，已记录为后续 P1 运维/稳定性清理项，未掩盖任何失败。
- P1：尚未开始，严格在 P0 全绿并提交后推进。

## 5. 结论口径

最终验收只允许使用：

- `PASS`
- `FAIL`
- `CONDITIONAL`
- `NOT VERIFIED`

最终建议只允许：

- `GO`
- `CONDITIONAL GO`
- `NO-GO`

任何未运行、无真实环境或数据阻塞的项目必须明确标记，不得写为 PASS。
