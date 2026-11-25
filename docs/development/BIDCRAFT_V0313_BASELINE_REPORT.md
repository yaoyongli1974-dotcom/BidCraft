# BidCraft v0.31.3 阶段 0：基线保护与项目状态确认报告

## 1. Git 基线

| 项目 | 值 |
|---|---|
| 目标基线 SHA | `f06feb7cdb6375c5df74fd2ae5a4ecd601a2662f` |
| 实际 HEAD（开始时） | `f06feb7cdb6375c5df74fd2ae5a4ecd601a2662f`（一致） |
| 原分支 | `main`（与 `origin/main` 同步） |
| 新建工作分支 | `fix/v0.31.3-production-readiness` |
| 工作区状态 | 干净，无未提交修改；未执行 `reset --hard` / `clean -fd` |
| 现有标签 | `v0.31.0`、`v0.31.1`、`v0.31.2`（`v0.31.2` 指向 f06feb7） |
| 远端 | `git@github.com:yaoyongli1974-dotcom/BidCraft.git`（SSH over 443 配置已存在） |

## 2. 版本与环境

| 项目 | 值 |
|---|---|
| 后端版本 | 0.31.2（`bidcraft/__init__.py`、`pyproject.toml`） |
| Web 版本 | 0.31.2（`web/package.json`、`web/package-lock.json`） |
| Python | 3.13.5（`.venv`） |
| SQLAlchemy | 2.1.8 |
| SQLite | 3.46.1（Python 内置） |
| Node.js / npm | v26.13.1 / 11.20.0 |
| Docker | 未安装（本地无法构建/启动容器） |
| PostgreSQL | 本地无系统 psql/docker；已通过 `pgserver` 临时包启动独立 **PostgreSQL 16.2** 测试实例（仅用于测试，未连接任何生产/开发库） |
| Alembic | 单 head `0012_generation_lease` |

## 3. 起始测试基线

| 检查 | 结果 |
|---|---|
| 后端全量 pytest（v0.31.2 发版结果） | 2125 passed / 18 skipped / 1 warning |
| 前端 typecheck / lint | 通过 |
| 前端 test | 39 files / 320 tests 通过 |
| 前端 build | Vite 8 构建通过 |
| 现有 PostgreSQL opt-in 套件（真实 16.2 实例） | 33 passed / 1 skipped（无新用例，仅确认环境可用） |
| 本轮基线全量 pytest | **2125 passed / 18 skipped / 1 warning in 800.25s**，零失败（`/tmp/opencode/v0313_baseline_full.out`） |

## 4. CI 与 Release 状态（REST 核验）

| 项目 | 结果 |
|---|---|
| 最新 Release | `v0.31.2`，https://github.com/yaoyongli1974-dotcom/BidCraft/releases/tag/v0.31.2 |
| main 最近 3 次 Actions | run 45537151489 / 45537147533 / 45536985786 均 `completed success`，HEAD 均为 f06feb7 |
| 本分支 | 本地新建，尚未推送远端；按任务约束不自动合并 main、不创建 Release |

## 5. 约束与执行原则

- 保留 v0.31.2 已有 Job fencing、GenerationLease、Reaper、退款幂等机制。
- 不删除测试、不放宽断言、不通过 skip/xfail/mock 关键数据库操作规避问题。
- SQLite 通过不等同于 PostgreSQL 通过；本轮使用独立 PostgreSQL 16.2 实例执行真实并发验证。
- 文件系统副作用不得描述为随数据库事务自动回滚；按临时文件 + 原子发布 + 可恢复状态处理。
- 无真实脱敏标书样本时，真实质量指标保持 `NOT_MEASURED`。
- 阶段化提交与报告；未经明确批准不推送 main、不创建正式 Release、不部署生产。
