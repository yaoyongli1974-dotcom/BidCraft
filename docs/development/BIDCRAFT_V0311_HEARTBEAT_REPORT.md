# BidCraft v0.31.1 阶段 1：GenerationLease 心跳修复报告

## 1. 结论

修复了生成租约心跳线程借用长事务 Session / 调用参数错误的问题；租约续期现在走独立数据库会话，失效或连续失败会传播 `abort`，生成侧在下一个检查点停止。

## 2. 交付项（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 独立会话续期 | 完成 | `jobqueue.renew_generation_lease(project_id, token)` 内 `session_scope()` |
| 2 | 参数错误修复 | 完成 | 不再在心跳线程直接调用 `touch_generation_lease(lease_project_id, lease_token)` |
| 3 | 状态语义 | 完成 | 返回 `active` / `lost` / `retry` / `stopped`，不再布尔含糊 |
| 4 | 失效即中止 | 完成 | `lost` 时设置传入的 `abort` Event 并记录 ERROR |
| 5 | 异常传播 | 完成 | 独立会话捕获 `SQLAlchemyError`，连续 3 次失败置 abort |
| 6 | 心跳频率 | 完成 | `BIDCRAFT_GENERATION_LEASE_TTL` / `BIDCRAFT_GENERATION_HEARTBEAT_INTERVAL`，间隔强制 ≤ TTL/3 |
| 7 | Worker 接入 | 完成 | `workers/generation.py` 传 `abort=job_abort_event()` |
| 8 | 同步路径接入 | 完成 | `generate_chapter` / `write` 用本地 Event，租约失效则中止并按已写章节结算 |
| 9 | 章节结算口径 | 完成 | 中止/异常路径按 `report.written` 结算，避免全额退款白送已完成章节 |
| 10 | 测试覆盖 | 完成 | `tests/test_v0311_stage1_heartbeat.py` 6 项（续期/续命/失效/异常/配置/静态接线） |
| 11 | 兼容/回滚 | 完成 | 无迁移、无 API 语义变更；恢复配置与新函数删除即可回滚 |

## 3. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | 通过 |
| `pytest tests/test_v0311_stage1_heartbeat.py tests/test_billing_concurrency_v031.py tests/test_jobqueue.py tests/test_v030_stage4_fencing.py` | **44 passed**（1 Starlette 弃用警告） |
| 全量后端 | 阶段 8 统一执行 |

## 4. 边界

- 心跳只保证「租约失效尽快被 worker 感知」；章节级原子写入由阶段 3 处理。
- PG 并发场景仍为 opt-in；本地 SQLite 验证使用了真实独立会话与短间隔心跳。
- 无生产配置变更，无历史迁移改动。
