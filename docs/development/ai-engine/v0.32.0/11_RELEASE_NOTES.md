# v0.32.0 发版说明索引（Phase 7）

对外口径以 [docs/release-notes/v0.32.0.md](../../../release-notes/v0.32.0.md) 为准；本文件仅作阶段报告索引，避免多副本措辞漂移。

| 主题 | 证据 |
| --- | --- |
| 总览与对外说明 | [docs/release-notes/v0.32.0.md](../../../release-notes/v0.32.0.md) |
| 基线与架构边界 | `00_BASELINE_REPORT.md` |
| 章节规划 / 知识资产 / 混合检索 / Agent 工具 / 经验事实治理 / 质量评估 | `01_` … `06_*.md` |
| API 契约（22 条新路由、274/93、响应变化） | `08_API_CHANGELOG.md` |
| 数据库迁移（0013-0015、单 head、NULL 语义、回滚） | `09_DATABASE_MIGRATION_REPORT.md` |
| 测试与 CI（SQLite 2257/886/1、Web 57/431） | `10_TEST_REPORT.md` |

版本 0.32.0 已同步后端、`pyproject.toml`、`uv.lock`、Web `package.json`/lock 根版本与 `deploy/` 清单；CI 临时镜像标签为 `v0320-check`，PG job 显式纳入 `tests/postgres/test_v032_migrations.py` 并保持 `>=900 / skipped=0 / failures=0` 门禁。

状态占位（随最终验收回填）：

- 【待回填：最终全量 pytest 重跑结果】
- 【待回填：远端 CI postgres job 结果（tests>=900 / skipped=0 / failures=0）】
- 【待回填：真实 PG 0013/0014/0015 迁移往返测试结果】
