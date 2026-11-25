# BidCraft v0.31.2 最终验收报告

## 1. 范围与基线

- 分支：`fix/v0.31.2-concurrency`（仅本地分支，未推送 main、未创建 tag/Release、未部署生产）
- 基线：`74c4156`（v0.31.1）
- 任务书：8 阶段（0 基线 → 7 最终验收），主题为并发可靠性验证与残余风险专项改造
- 约束遵守：无 `reset --hard` / `clean -fd`；无测试删除、断言放宽或用 skip 掩盖问题；历史迁移 0001～0012 零改动；无 Python 全局锁替代 DB fencing；无 mock 替代核心条件 UPDATE 的并发证据；本轮不 bump 版本（保持 0.31.1）
- 变更规模：阶段 0～6 为 29 个文件、+2180 / -108 行；阶段 7 另加本报告与一处旧测试前置条件修正，无新增迁移。

## 2. 阶段提交与结论

| 阶段 | 提交 | 主题 | 结论 |
|---|---|---|---|
| 0 | `aadfe49` | 并发可靠性基线与 V0312-01～07 问题清单 | 已完成并通过验证（1706 pytest 基线） |
| 1 | `3ff8a4d` | Job NULL / 过期 lease 禁止终态提交 | 已完成并通过验证（72 targeted / 1 PG opt-in skip） |
| 2 | `204e02e` | Reaper 真实多线程事务竞争（100 轮 ×4 + 50 轮 ×1） | 已完成并通过验证（68 targeted / 1 PG opt-in skip） |
| 3 | `1452568` | GenerationLease + Job claim 双重 fencing 章节原子提交 | 已完成并通过验证（21 targeted，3 组 100 轮竞争） |
| 4 | `3421a31` | Worker 真实运行矩阵与取消任务复活防护 | 已完成并通过验证（70 targeted） |
| 5 | `886c760` | 账务竞争 50 轮真实线程回归与失败重试 | 已完成并通过验证（311 targeted / 1 warning） |
| 6 | `9c1e909` | 质量指标 coverage / samples / mismatch 明细 | 已完成并通过验证（51 targeted） |
| 7 | 本提交 | 全量验收与最终报告 | 见下方验证结果 |

## 3. 验证结果

| 检查 | 结果 |
|---|---|
| 全量后端 pytest | **2125 passed / 18 skipped / 1 warning in 789.83s**，零失败（第二次运行） |
| Web typecheck / lint / test / build | 通过：39 files / 320 tests；Vite 8 build 成功 |
| `ruff check bidcraft tests scripts` | 通过（All checks passed） |
| `git diff --check` | 通过 |
| 历史迁移 0001～0012 改动 | 0 行 |
| 测试删除检查（`--diff-filter=D`） | 0 处 |
| 版本一致性 | 后端 / Web / lock 均为 0.31.1（本任务不 bump） |
| Alembic 单 head | `0012_generation_lease` |
| DEBUG 临时代码残留 | 0 处 |

首次全量回归（`/tmp/opencode/v0312_stage7_full.out`）结果为 1 failed / 2124 passed / 18 skipped：唯一失败是 `tests/test_batch6_gaps.py::test_similarity_check_enqueues_job_without_blocking`，其测试 helper 仍按旧语义把 Job 建为 `queued` 且只设置 token。阶段 1 要求终态写入有有效 lease，阶段 4 要求 Worker 入口状态为 `running`，因此 runner 正确返回、报告保持 `queued`。已将测试前置条件更新为真实认领状态（`status='running'` + 未来 `lease_expires_at`），断言不变、未删除测试；单独复跑 1 passed。第二次全量回归结果见上方表格。

## 4. 交付物五类结论（任务书要求）

### 4.1 已完成并通过验证

1. **阶段 0 基线与问题清单报告**：锁定 HEAD 74c4156、单 head 0012、版本 0.31.1；基线全量 1706 passed / 18 skipped。
2. **Job NULL / 过期 lease 终态 fence**：`commit_fenced_result` 要求 lease 非 NULL 且未过期；10 项真实 Session 测试 + 2 个既有 helper 强化。
3. **Reaper 真实并发证据**：2 线程 / 2 Session / Barrier；A～D 各 100 轮、E 50 轮真实退款，无全局锁、核心 UPDATE 未 mock。
4. **`_renew_lease_once` 抽取**：从生产 `_heartbeat` 原逻辑逐句抽取，测试与生产共用一条条件 UPDATE 实现，避免漂移。
5. **GenerationLease + Job claim 双重 fencing**：`commit_generation_chapter` 在单一短事务内校验两层 fence，写 Section / OutlineNode / done_steps / `chapter_committed`。
6. **章节重复提交幂等**：重复章节不覆盖正文、不重复 done_steps、不重复发成功事件。
7. **双重 fencing 竞争证据**：100 轮 Job claim 竞争、100 轮 GenerationLease 失效竞争、100 轮章节写入 vs Reaper。
8. **全 Worker 终态复活防护**：8 个 runner 在入口检查 `job.status != 'running'` 即返回；取消任务不再被迟到 Worker 复活。
9. **Worker 真实 DB 运行矩阵**：旧 token / 取消 / 预置 abort / 正常终态 / 过期 lease / 终态写入异常回滚 / 终态事件幂等，8 kind 全覆盖。
10. **`_fail_job` 并发迁移修复**：改用 `transition_job` 条件 UPDATE，两线程只能一个写 failed 并发事件，修复了 50 轮中稳定出现的重复事件。
11. **`transition_job` ORM 过期范围修复**：只 expire 目标 Job，不再 detach 调用方持有的 User 等对象。
12. **退款领取失败释放**：`refund_charged_job` / `settle_pending_charge` 在账本写失败时释放领取标记，使退款可重试。
13. **账务 6 场景 × 50 轮真实线程测试**：两线程退款、Reaper vs Worker、取消 vs 完成、settle vs reconcile、子账号删除、退款失败后重试；金额与账本笔数守恒。
14. **项目级指标明细**：`coverage` / `samples` / `mismatches` 加入 `summary['metrics']`，旧比率口径（0.5、5/209×1000、0.75）完全不变。
15. **真实件指标明细**：单文件与汇总新增条款/取值/判定覆盖、样本和不一致明细；数据集未就绪时返回空容器，不伪造数值。
16. **最终验收静态检查**：历史迁移零改动、无测试删除、ruff / diff-check / 版本一致性全绿。

### 4.2 已完成但验证不足

17. **PostgreSQL 真实行锁并发**：本地无 psql / 真实 PG 实例，SQLite 文件库 + WAL + busy_timeout 证明了条件 UPDATE 原子性与最终一致性；PG opt-in 用例保留，需在 CI/用户提供实例上执行。Reaper、双重 fencing、Worker 矩阵均按此口径陈述。

### 4.3 部分完成

18. **真实件质量指标**：coverage / samples / mismatch 计算与明细已完成并通过合成/契约数据验证；真实脱敏招标文件、人工标注和阈值校准仍未提供，因此真实召回率/一致率保持“未测量”，报告中明确记录原因。导出 / 图片 / 知识 Worker 的外部文件副作用不在单体 DB 事务可回滚范围，本轮只验证其终态事件与 Job 状态正确，文件级补偿仍属残余风险。

### 4.4 未完成

- 真实 PostgreSQL 实例上的 8 Worker 并发与行锁压力验证（本地环境不具备，opt-in CI 项）。
- 真实脱敏招标文件样本与人工标注的收集、版本化和阈值校准（依赖用户提供）。
- 远端 CI 在本分支的执行、main 合并与 Release 创建（按本任务约束不在阶段 7 执行）。

### 4.5 因风险暂停

- 无新增暂停项。本轮未触碰生产配置、权限规则、账务历史数据修复或不可逆迁移；未修改历史迁移；未推送 main、未创建 Release、未部署生产。

## 5. 结论与回滚

本专项在本地验收条件下完成：阶段 0～6 全部提交，全量后端与前端检查通过，并发证据均来自真实线程 / 独立 Session / 生产条件 UPDATE，未使用 Python 全局锁替代数据库 fencing，也未删除或弱化任何测试。结论为：具备合并评审条件，但 PG 真实并发与真实件指标仍需在具备环境/样本时补验。

回滚按阶段 revert：阶段 1～6 的改动均为代码与测试，无新增迁移、无 schema 变化，无需处理历史数据；回退时按报告列出文件选择性还原，避免整文件 checkout 影响并行阶段共享文件。
