# BidCraft v0.31.1 阶段 3：章节级 fencing 写入报告

## 1. 结论

后台生成现在把**每章正文写入 + done_steps 记账**放进一个独立短事务，并在该事务内重新校验 Job fencing token / 租约 / 生成租约。旧 worker 被 Reaper 回收后无法再落盘章节或伪造幂等账本。

## 2. 交付项（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | `jobqueue.fenced_chapter_write` | 完成 | 复用 `commit_fenced_result` 的条件 UPDATE，同一事务内可选校验 GenerationLease |
| 2 | writer 新钩子 | 完成 | `generate_document(..., commit_chapter=...)` 接管正文写入 |
| 3 | 长会话不再写章节 | 完成 | 提供 `commit_chapter` 时不修改 Section/OutlineNode |
| 4 | done_steps 同事务 | 完成 | worker 回调内在 fenced session 中 `mark_step_done` |
| 5 | abort 检查点 | 完成 | `before_chapter` 仍先 `raise_if_aborted()` + `assert_generation_lease` |
| 6 | 模型调用不在 DB 长事务写盘 | 完成 | 章节落盘发生在模型返回后的新短事务 |
| 7 | 无 Python 全局锁 | 完成 | 并发安全由 SQL 条件 UPDATE + 租约完成 |
| 8 | 失效 token 拒绝 | 完成 | `JobAborted`，无正文、无 done_steps |
| 9 | 同步路径兼容 | 完成 | 未提供 `commit_chapter` 时沿用原写入与 `after_chapter` |
| 10 | 测试矩阵 | 完成 | fenced 成功/失效 + writer 回调路径，共 3 项新测试 |
| 11 | API/迁移 | 不变 | 无路由变化、无迁移 |

## 3. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | 通过 |
| `pytest tests/test_v0311_stage3_chapter_fence.py tests/test_reliability_writer.py tests/test_batch3_generation_fixes.py` | **19 passed** |
| 全量后端 | 阶段 8 统一执行 |

## 4. 边界

- `commit_chapter` 是新可选参数；旧调用方（同步 API、CLI）行为不变。
- 章节写入仍由 writer 决定正文/来源；fence 只控制是否允许持久化。
