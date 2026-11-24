# BidCraft v0.31.1 阶段 4：动态 Worker 矩阵报告

## 1. 结论

8 个注册 runner 现在通过动态发现统一验证：入口 abort 检查点、终态 fencing 写入、中止时不落盘状态/事件。发现并修复了 generation 与 similarity 两个此前在入口 abort 检查前先写 Job 状态的缺口。

## 2. 交付项（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 动态发现 runner | 完成 | `jobqueue._API_RUNNER_NAMES` → `_RUNNERS` |
| 2 | 8 个 runner 全覆盖 | 完成 | agent/batch_check/batch_export/evaluation/generate/images/kb_reindex/similarity |
| 3 | 入口 abort 检查 | 完成 | 每个 runner 函数源码含 `raise_if_aborted()` |
| 4 | 终态 fencing | 完成 | runner 所在模块源码含 `commit_fenced_result`/`finalize_job_result`/`fenced_chapter_write` |
| 5 | generation 入口修复 | 完成 | 进入首个 session 前 `raise_if_aborted()` |
| 6 | similarity 入口修复 | 完成 | 进入首个 session 前 `raise_if_aborted()` |
| 7 | 预置 abort 不写终态 | 完成 | 8 个用例均保持 status=running、events=[] |
| 8 | 中止传播兼容 | 完成 | 既允许向外抛 `JobAborted`，也允许 runner 内部捕获返回 |
| 9 | 无 Python 全局锁 | 完成 | 仅复用现有 DB fence |
| 10 | 测试 | 完成 | `tests/test_v0311_stage4_dynamic_matrix.py` 16 用例 |
| 11 | API/迁移 | 不变 | 无路由、无 schema 变更 |

## 3. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | 通过 |
| `pytest tests/test_v0311_stage4_dynamic_matrix.py tests/test_worker_reliability_matrix.py tests/test_v030_stage4_fencing.py` | **43 passed** |
| 全量后端 | 阶段 8 统一执行 |

## 4. 边界

- 动态矩阵检查生产 runner 注册项；测试内 monkeypatch 的临时 runner 不在范围内。
- 行为测试验证入口中止，不重复执行各 runner 的完整业务路径。
