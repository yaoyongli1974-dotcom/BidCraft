# BidCraft v0.31.2 阶段 3：GenerationLease + Job claim 双重 fencing 报告

## 1. 结论

章节写入现在由生产路径 `bidcraft/workers/generation.py::commit_generation_chapter` 在**同一个短事务**内同时校验 Job claim（running + token + 有效 lease）与项目 GenerationLease（token + 未过期），再写入 Section、OutlineNode、done_steps 与 `chapter_committed` 事件；任一项 fencing 失败则整笔回滚，不产生部分提交。

## 2. 修改文件与函数

| 文件 | 函数/位置 | 说明 |
|---|---|---|
| `bidcraft/workers/generation.py` | `commit_generation_chapter` | 使用 `fenced_chapter_write(job_id, node_id, project_id=..., lease_token=...)`；事务内检查 `step_status`，重复章节直接返回；写入 Section.body/words/status、OutlineNode.status、`mark_step_done`、发布 `chapter_committed` |
| `bidcraft/jobqueue.py` | `fenced_chapter_write` | 复用 `commit_fenced_result` 的条件 UPDATE（NULL lease 已禁止），并在同一事务内 `assert_generation_lease` |
| `tests/test_v0312_generation_dual_fencing.py` | 新增 21 项用例 | 真实 pipeline 数据 + 真实 Session/事务 + 生产 Worker 提交函数 |

## 3. 测试证据

| 场景 | 结果 |
|---|---|
| 正常双重 fencing | Section/OutlineNode/done_steps/chapter_committed 全部提交 |
| Job token 失效 | JobAborted，全部保持初始状态，无事件 |
| GenerationLease 失效/释放 | JobAborted，无部分写入 |
| 双重 fencing 单项失效（两种组合） | 均拒绝 |
| 事务中途 `mark_step_done` 异常 | RuntimeError，整笔回滚，无成功事件 |
| SQLAlchemy Identity Map 陈旧 | 旧 Session 不能绕过数据库条件 UPDATE |
| 重复章节提交 | 幂等：正文保持第一次内容，step True，仅 1 个事件 |
| **100 轮 Job claim 竞争** | 2 线程 / 2 独立 Session / Barrier；成功或 JobAborted 二选一；最终状态一致 |
| **100 轮 GenerationLease 失效竞争** | 2 线程 / 2 Session；提交成功必须与 done_steps/事件一致，否则全清 |
| **100 轮章节写入 vs Reaper** | 2 线程 / 2 Session；`chapter_committed` 与正文/step 严格一致，queued 事件最多 1 次 |

测试环境：本地 SQLite（文件库，WAL + busy_timeout），真实 SQLAlchemy Session 与事务，无 mock 替代核心 UPDATE；未声称 PostgreSQL 验证。PostgreSQL 真实行锁竞争仍为 opt-in CI 项，本地标记未完成。

## 4. 验证命令与结果

```
.venv/bin/python -m ruff check bidcraft tests
.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0312_generation_dual_fencing.py \
  tests/test_v0311_stage3_chapter_fence.py \
  tests/test_v0312_job_lease_null.py
```

结果：ruff All checks passed；**21 passed in 71.06s**，零失败。

## 5. 失败记录与修复

- 初版测试把常量 TOKEN 当作 GenerationLease token，`assert_generation_lease` 正确拒绝；新增 `_lease_token()` 读取真实 token。
- 重复章节测试曾期望不存在的 `StepAlreadyDone`；改为生产幂等断言（两次写入、正文不变、事件 1 次）。
- 中途异常测试 patch 错了模块符号；改为 `bidcraft.workers.generation.mark_step_done`。
- 竞争测试中 JobAborted 是合法竞争失败，作为结果捕获并验证最终一致性，不作为测试异常。
- `step_status` 对有效 claim 的空 done_steps 返回 False，测试断言从 `is None` 调整为 `in (None, False)`，不降低 fencing 强度。

## 6. 未完成与残余风险

- PostgreSQL 真实并发行锁验证：本地无实例，标记为未完成；CI opt-in 用例覆盖。
- 文件系统/外部副作用不在本阶段范围；章节写入只涉及 DB 行。
- 未新增数据库迁移，Alembic head 仍为 `0012_generation_lease`。

## 7. 回滚

仅回退 `bidcraft/workers/generation.py` 的 `commit_generation_chapter` 及相关导入，删除本阶段新增测试与报告；无 schema 变化。
