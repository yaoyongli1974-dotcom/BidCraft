# BidCraft v0.31.1 阶段 2：Reaper 租约竞争修复报告

## 1. 结论

Reaper 在重试上限处现在使用与扫描时点一致的租约判定：若心跳在扫描与条件 UPDATE 之间续租成功，竞争失败并返回 `lost`，活跃任务不会被误杀；若租约确实过期，则只产生一次 `failed`。

## 2. 交付项（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | `_set_failed` 增加租约阈值 | 完成 | 新增 `lease_expired_before` / `token` 关键字参数 |
| 2 | 上限路径透传 | 完成 | `_requeue` 在 attempts >= cap 时把阈值与 token 传给 `_set_failed` |
| 3 | 心跳续租胜利 | 完成 | 租约已续至未来时 `_requeue` 返回 `lost`，状态保持 running |
| 4 | 竞争失败不伪造失败事件 | 完成 | `lost` 不写 failed event / failed 计数 |
| 5 | 100 轮竞争 | 完成 | `test_100_rounds_concurrent_reclaim_stays_consistent` |
| 6 | 正常过期回收 | 完成 | 双 stale 快照只有一个 `requeued` |
| 7 | 上限失败唯一性 | 完成 | 第二个 reaper 返回 `lost`，只有一条 failed event |
| 8 | 启动恢复 | 完成 | 未过期租约保持 running，不被 startup recovery 抢走 |
| 9 | 过期生成租约清理 | 完成 | `reap_expired` / `recover_on_startup` 调用 `release_expired_generation_leases()` |
| 10 | 无 Python 全局锁 | 完成 | 全部判断仍由 SQL 条件 UPDATE / 租约阈值完成 |
| 11 | 兼容/回滚 | 完成 | 无迁移、无 API 变更；新参数均有默认值 |

## 3. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | 通过 |
| `pytest tests/test_v0311_stage2_reaper_race.py tests/test_jobqueue_race_conditions.py tests/test_jobqueue.py` | **33 passed / 1 skipped** |
| 全量后端 | 阶段 8 统一执行 |

## 4. 边界

- 本地 SQLite 覆盖确定性竞争；真实 PostgreSQL 并发仍由 opt-in CI 用例覆盖。
- 不改重试次数、告警文案与状态机既有语义。
