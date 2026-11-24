# BidCraft v0.31.0 专项技术改造——最终验收报告

## 1. 范围与基线

- 任务书：并发可靠性、账务一致性与真实投标质量治理，10 个顺序阶段。
- 基线：v0.30.0 / HEAD `510f566`，新建独立分支 `quality/v0.31.0-reliability`；未推送 main、未建 Release、未改生产配置、未连生产库。
- 约束执行：无 `reset --hard`/`clean -fd`、无测试删除/跳过掩盖、无历史迁移修改、无 `npm audit fix --force`、无既有 API 业务语义变更（唯一新增业务规则「同项目禁止并发生成」已先出风险报告并获用户批准）。

## 2. 阶段与提交

| 阶段 | 提交 | 主题 | 验证 |
|---|---|---|---|
| 0 | `6370fb1` | 基线审查与调用链报告 | 57 targeted passed |
| 1 | `3f02207` | Worker fencing 事务级原子提交 | 18 passed/1 skipped（PG opt-in）、89 broader |
| 2 | `8c43fa7` | Job 状态机 + Reaper 条件更新 | 39 passed/2 skipped、114 broader |
| 3 | `33b5592` | 账务一致性 + 生成互斥租约 + 迁移 0012 | **1647 passed / 18 skipped** |
| 4 | `549a908` | 全 Worker 终态 fencing 矩阵 | **1658 passed / 18 skipped** |
| 5 | `b65eaa6` | 真实件数据集契约框架 | 48 targeted passed |
| 6 | `13b2099` | 真实件质量指标与未测量口径 | **1669 passed / 18 skipped** |
| 7 | `2470340` | 前端质量闭环 | 39 files / 320 web tests |
| 8 | `29013e7` | 备份恢复手册与运维检查 | 10 passed/1 skipped，演练 1 passed |
| 9 | 本提交 | 最终验收 + v0.31.0 准备 | 全量 + 静态合规检查 |

## 3. 最终验证

| 项目 | 结果 |
|---|---|
| 后端全量 | `.venv/bin/python -m pytest -o addopts="" -q` → **1669 passed, 18 skipped, 1 warning in 750.77s**，零失败（`/tmp/opencode/v031_stage9_full.out`） |
| 前端 | typecheck / lint 通过；`npm test -- --run` → **39 files / 320 tests**；Vite 8 build 通过 |
| 静态检查 | `ruff check bidcraft tests scripts` 通过；`git diff --check` 干净 |
| 迁移约束 | `git diff 4d7b4dc..HEAD -- migrations/versions/0001..0011` 为空；新增 `0012_generation_lease` 可逆；Alembic 单 head |
| 测试约束 | `git diff --diff-filter=D 4d7b4dc..HEAD -- tests web/src` 为空 |
| 版本一致性 | `0.31.0` 在 `__init__.py` / `pyproject.toml` / `uv.lock` / compose / deploy 文档 / web package+lock / CHANGELOG / release-notes 索引一致 |
| 运维演练 | `BIDCRAFT_RESTORE_DRILL=1 pytest tests/test_fix3_backup_drill.py` 通过 |

## 4. 工作分类（任务书要求）

### 4.1 已完成并通过验证

- 阶段 0～4：基线报告、fencing 原子提交、队列状态机、账务一致性、同项目生成互斥（迁移 0012）、全 Worker 可靠性矩阵。均有自动化回归与全量 pytest 支撑。
- 阶段 6 的**框架与未测量语义**：数据集未就绪时 `status=未测量`、比率 `null`、原因入 `notes`；`build_quality_report` 摘要已集成并有测试。
- 阶段 7：6 项指标前端展示与格式，320 web tests 通过。
- 阶段 8：备份/恢复手册、`backup-verify` 与 restore drill 通过。

### 4.2 已完成但验证不足

- 迁移 `0012_generation_lease` 的真实 PostgreSQL 升级/回滚演练：本地 SQLite 与 opt-in PG 测试覆盖，但未连真实 PG 实例执行升级。
- 交互场景端到端（双进程并发 + 数据库级租约 + worker 心跳）在本地通过多线程与 DB 状态验证；未做生产形态压测。
- 本分支未推送远端，GitHub Actions 未对 v0.31.0 分支运行（按约束不推 main/不发版）。

### 4.3 部分完成

- 真实招标文件数据集：只交付契约框架、示例 manifest 与脱敏说明；真实样本和 `dataset.json` 需用户提供。
- 真实件质量指标：条款召回率与关键值解析率框架就绪；缺标注应答时 `verdict_agreement` 只能为 `null`；逐文件明细已归一化但界面只展示汇总。
- 生成互斥租约：同步路径进程被 SIGKILL 时项目锁持续到 TTL 自动过期（设计取舍，非缺陷）。

### 4.4 未完成

- 真实脱敏招标文件的收集、标注、版本管理与基线校准（依赖用户提供）。
- 基于真实标注的召回率/一致性阈值校准与正式发版/合并 main（按任务约束不在本阶段执行）。
- 远端市场自动安装、容器/系统级隔离、质量报告导出与批量看板（既有决策暂缓）。

### 4.5 因风险暂停

- 无新增暂停项。阶段 3 涉及业务规则变化，已按任务书要求出风险报告并获得用户对方案 A（禁止同项目并发生成）的批准后实施。

## 5. 结论与回滚

本分支具备合并评审条件：核心并发与账务路径已加固，所有自动化门禁通过，唯一需要用户决策的是是否合并 main / 发布 v0.31.0，以及何时提供真实脱敏样本启用真实件指标。

回滚按阶段提交逐一 revert；迁移 0012 可逆（`downgrade` 删除 `generation_leases` 表与索引），无需修复历史数据。

## 6. 发布决定（验收后补记）

用户在本报告验收通过后明确要求「合并到 main 并发 v0.31.0」。第 4.2/4.4 节中「未推送远端 / 未合并 main / 未发版」是**验收时点**的约束陈述；实际后续动作由用户授权执行：分支快进合并至 main，版本 0.31.0 已推送并打 tag，GitHub Release 从发版说明创建。真实脱敏样本仍待用户提供，真实件指标继续保持「未测量」口径。
