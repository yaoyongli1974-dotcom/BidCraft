# v0.31.3 阶段 2：八类 Worker 真实 PostgreSQL 运行时矩阵

- 分支：`fix/v0.31.3-production-readiness`（阶段 1 提交 fcf66dd 之后）
- 数据库：真实 PostgreSQL 16.2（`BIDCRAFT_TEST_PG_URL` 指向本地 `pgserver` 实例）
- 证据类型：真实 Job / JobEvent 行、真实 claim token 与租约、真实事务；不使用源码检查作为运行时证据，不 mock 核心 fencing SQL

## 1. 覆盖范围

从 `jobqueue._API_RUNNER_NAMES` / `jobqueue._RUNNERS` 动态发现全部 8 类 runner：

`agent`、`batch_check`、`batch_export`、`evaluation`、`generate`、`images`、`kb_reindex`、`similarity`。

新增 `tests/postgres/test_v0313_worker_runtime.py`（57 项）：

| 场景 | 每类 runner | 验证点 |
|---|---|---|
| 注册完整性 | 1 | 字符串 runner 名 + 可调用实现，方言断言为 postgresql |
| 旧 token | 1 | runner 优雅返回或 `JobAborted`；Job 保持 running，无事件 |
| 已取消任务 | 1 | 8 类 Worker 均不复活 cancelled，状态保持 cancelled，无事件 |
| 预置 abort | 1 | 入口/检查点中止；状态 running，无写入、无事件 |
| 过期 lease | 1 | `finalize_job_result` 抛 `JobAborted`；无终态、无事件 |
| 正常终态 | 1 | 共享终态路径恰好 1 条 done 事件 |
| 重复终态 | 1 | 第二次被 fencing 拒绝，事件仍仅 1 条 |
| 终态写入异常回滚 | 全局 1 | patch `jobs.publish_event` 抛错；状态 running、无事件 |

## 2. 验证命令与结果

```bash
BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/v0313_pg_uri.txt)" \
  .venv/bin/python -m pytest -o addopts="" -q tests/postgres/test_v0313_worker_runtime.py
```

`/tmp/opencode/v0313_pg_worker_runtime.out`：**57 passed**（4.81s），零失败。

`.venv/bin/python -m ruff check bidcraft tests`：All checks passed。

## 3. 结论与残余风险

- 结论：阶段 2 为 **A PASS**。八类 Worker 在真实 PostgreSQL 上的入口 fencing、状态守卫、abort、终态幂等与异常回滚均通过。
- 残余风险：本矩阵验证的是 DB 状态与 JobEvent；导出 / 图片 / 知识索引等外部文件副作用由阶段 3 单独验证，不能声称 DB 回滚会自动回滚文件系统。
- 回滚：删除本阶段新增测试文件即可；无生产代码、无迁移改动。
