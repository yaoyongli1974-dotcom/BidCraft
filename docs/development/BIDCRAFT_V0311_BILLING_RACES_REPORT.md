# BidCraft v0.31.1 阶段 5：账务竞争场景报告

## 1. 结论

针对取消、陈旧 worker 回调、子账号删除、重复退款、退款失败重试五类场景补齐回归测试，并修复 `_fail_job` 对终态重试重复发 `failed` 事件的问题。退款幂等仍由 DB 条件领取（`refund_claimed` / marker `status`）负责，未引入 Python 全局锁。

## 2. 交付项（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 取消只退一次 | 完成 | `test_cancel_refunds_once_even_if_fail_job_repeated` |
| 2 | 重复回调不重复事件 | 完成 | `_fail_job` 终态守卫；事件列表仅 1 条 |
| 3 | 已写满章节不退款 | 完成 | `test_stale_worker_failure_does_not_refund_earned_chapters` |
| 4 | 子账号删除 | 完成 | `test_deleted_subaccount_refunds_to_billing_account` |
| 5 | 付款账号优先 | 完成 | payload `billing_account_id` 优先于 `user_id` |
| 6 | 退款失败事件 | 完成 | `refund_failed` 事件存在，claim 未领取 |
| 7 | 退款失败后重试 | 完成 | `refund_charged_job` 二次调用成功退还 |
| 8 | marker 条件领取 | 完成 | 既有 `settle_pending_charge` 测试覆盖 |
| 9 | 不改账务公式 | 完成 | VIP 取整/部分章节公式保持原样 |
| 10 | 无 Python 锁 | 完成 | 全部幂等由 SQL 条件 UPDATE 保障 |
| 11 | 兼容/回滚 | 完成 | 仅改 `_fail_job` 终态分支；新增测试文件 |

## 3. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | 通过 |
| `pytest tests/test_v0311_stage5_billing_races.py tests/test_billing_consistency_v031.py tests/test_billing_concurrency_v031.py tests/test_fix2_data_integrity.py` | **20 passed / 1 warning** |

## 4. 边界

- 本地 SQLite 验证确定性竞争；PostgreSQL 真实并发由 opt-in CI 覆盖。
- 退款失败事件只作为人工对账信号；未自动无限重试（避免重复扣/退）。
