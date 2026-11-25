# BidCraft v0.31.2 阶段 0：并发可靠性基线报告

## 1. 基线状态

| 项目 | 值 |
|---|---|
| 目标 HEAD | `74c41568bd57edff25ffb6cb53808b6869e3bc6c` |
| 实际 HEAD | `74c41568bd57edff25ffb6cb53808b6869e3bc6c`（一致） |
| 工作区 | 干净；无用户未提交改动 |
| 原分支 | `main`（与 `origin/main` 同步） |
| 新工作分支 | `fix/v0.31.2-concurrency` |
| 版本 | 0.31.1（后端 / Web / lock 一致） |
| Alembic | 单 head `0012_generation_lease`，12 个 revision |
| 标签 | `v0.31.0`、`v0.31.1` |
| 环境 | Python 3.13.5、SQLite 3.46.1、SQLAlchemy 2.1.8、Node 26.13.1、npm 11.20.0；本地无 psql/Docker/LibreOffice |

未执行 `reset --hard`、`clean -fd`、`push --force`；未覆盖任何用户文件。

## 2. 基线验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts="" -q` | **1706 passed / 18 skipped / 1 warning in 740.12s**，零失败 |
| Web（v0.31.1 验收基线） | typecheck / lint / 39 files 320 tests / build 全绿 |
| `ruff check bidcraft tests scripts` | 通过 |
| 历史迁移 0001～0012 | 未改动 |

## 3. 已知残余问题映射

| 编号 | 问题 | 阶段 |
|---|---|---|
| V0312-01 | `commit_fenced_result` 允许 NULL lease 通过 | 1 |
| V0312-02 | Reaper 顺序竞争测试不是真实并发 | 2 |
| V0312-03 | 章节写入缺少 GenerationLease + Job claim 双重真实事务证据 | 3 |
| V0312-04 | Worker 矩阵以源码检查为主 | 4 |
| V0312-05 | 账务退款并发与守恒证据不足 | 5 |
| V0312-06 | 质量指标缺少 sample/coverage/mismatch 明细 | 6 |
| V0312-07 | 验收报告与残余风险清单需补齐 | 7 |

## 4. 约束

不新增业务功能；不推送 main；不建 Release；不部署生产；不修改历史迁移；不删测试、不降低断言；SQLite 与 PostgreSQL 结果分别陈述；本地 PostgreSQL/Docker 不可用时如实标记未完成。
