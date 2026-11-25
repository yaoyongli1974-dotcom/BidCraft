# BidCraft v0.31.2 阶段 4：Worker 动态可靠性矩阵（真实 DB 行为）报告

## 1. 结论

阶段 4 将 Worker 可靠性验证从源码检查升级为真实 Session/事务行为测试，并发现修复了一个真实缺陷：所有 Worker 在 `fenced_job` 只校验 token 后无条件把 Job 状态设回 `running`，导致已取消任务被迟到的 Worker 复活。现在 8 个 Worker 均在入口先检查 `job.status == "running"`。

## 2. 修改文件与函数

| 文件 | 修改 |
|---|---|
| `bidcraft/workers/{agent,compliance,evaluation,export,generation,images,knowledge}.py` | 入口处新增 `if job.status != "running": return`（similarity 已有），阻止终态任务复活 |
| `tests/test_v0312_worker_runtime_matrix.py` | 动态发现全部 8 个 runner；真实 DB 行为矩阵 |

## 3. 测试矩阵

动态从 `jobqueue._API_RUNNER_NAMES` / `_RUNNERS` 发现 8 个 runner：agent / batch_check / batch_export / evaluation / generate / images / kb_reindex / similarity。

| 场景 | 验证方式 |
|---|---|
| 注册完整性 | 8 个 kind 均有字符串 runner 名与可调用实现 |
| 旧 token | `execution_context` 注入旧 token；runner 优雅返回或 JobAborted；Job 仍 running、无事件 |
| 已取消任务 | 所有 Worker 不再复活 cancelled；状态保持 cancelled，无事件 |
| 预置 abort | runner 在入口中止；无业务写入、无事件 |
| 正常终态 | `finalize_job_result` 对 8 个 kind 均产生恰好 1 个 done 事件 |
| 过期 lease | 终态条件 UPDATE 拒绝（JobAborted），状态保持 running，无事件 |
| 终态写入异常 | patch `bidcraft.jobs.publish_event` 抛错；事务回滚，状态 running，无事件 |
| 终态事件幂等 | 重复 `finalize_job_result` 只产生 1 个 done 事件 |

说明：Export / Images / Knowledge 的文件系统与外部索引副作用不属于单体 DB 事务可回滚范围；本阶段验证的是它们不会在 fencing 失败后发布终态事件或错误更新 Job 状态。文件级临时对象/补偿机制仍为残余风险。

## 4. 验证

```
.venv/bin/python -m ruff check bidcraft tests
.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0312_worker_runtime_matrix.py \
  tests/test_v0311_stage4_dynamic_matrix.py \
  tests/test_worker_reliability_matrix.py
```

结果：ruff All checks passed；**70 passed in 6.71s**。

SQLite：真实 Session/事务、Barrier 控制的竞争；未把 SQLite 结果描述为 PostgreSQL。PostgreSQL 真实行锁与 8 worker 并发仍为 opt-in CI 项。

## 5. 失败记录

- 初版测试把 `_API_RUNNER_NAMES[kind]`（字符串）当 callable，已修正从 `_RUNNERS` 取函数。
- 终态回滚测试 patch 错模块属性 `jobqueue.jobs`；改为 `bidcraft.jobs.publish_event`。
- 8 个取消任务用例暴露了 Worker 复活缺陷，已修复生产代码，非降低断言。

## 6. 未完成与残余风险

- 每个 Worker 的完整业务写入/外部副作用（导出文件、图片、知识索引）未逐一做文件级回滚演练。
- PostgreSQL 真实并发未在本地执行（无实例）。
- 未新增数据库迁移，Alembic head 仍 `0012_generation_lease`。

## 7. 回滚

回退 8 个 Worker 入口状态检查与本阶段新增测试/报告；无 schema 变化。
