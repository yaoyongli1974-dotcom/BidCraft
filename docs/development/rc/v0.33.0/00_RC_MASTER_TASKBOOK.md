# v0.33.0 RC 主任务台账

## 1. 目标与约束

目标：按《BidCraft v0.32.x AI引擎可靠性与实战效果专项技术改造任务书》完成 RC-01..RC-13，逐模块代码审查、测试先行、最小修复、回归/静态检查/报告闭环。

硬约束：仅 `fix/v0.33.0-rc-reliability` 本地开发；未授权不推送 main、不打 tag、不建 Release、不部署生产、不改生产数据；不删测试、不放宽断言、不用 mock 冒充真实执行；不重写整模块、不重复造轮子；所有新增状态变更考虑事务/并发/权限/审计/回滚。

## 2. 基线与分支

- 基线：`main` @ `7fe741dcc7c066e9b77e1269fae3260f7894e389`（v0.32.2）。
- RC 分支：`fix/v0.33.0-rc-reliability`。
- Alembic head：`0015_fact_confirmation`（单 head）。
- 基线全量：2270 passed / 888 skipped / 1 warning。
- 基线 Web：57 files / 434 tests，typecheck/lint/build 通过。

## 3. 模块台账

| RC | 主题 | 状态 | 报告 | 提交 |
|---|---|---|---|---|
| RC-01 | 工具结果验证严格化 | 已完成并通过验证 | `02_TOOL_LOOP_RELIABILITY_REPORT.md` | `77e1f56` |
| RC-02 | Agent 工具安全/预算/审计 | 已完成并通过验证 | `03_AGENT_SECURITY_AUDIT_REPORT.md` | `8dd27f5` |
| RC-03 | 章节规划依赖一致性 | 已完成并通过验证 | `04_CHAPTER_PLAN_CONSISTENCY_REPORT.md` | `241a9bf` |
| RC-04 | 知识资产生命周期 | 已完成并通过验证 | `05_KNOWLEDGE_ASSET_LIFECYCLE_REPORT.md` | `f5c9159` |
| RC-05 | 生成并发/退款单一归属 | 已完成并通过验证 | `06_GENERATION_CONCURRENCY_REPORT.md` | `591891c` |
| RC-06 | 检索基准与未测量口径 | 实施中 | `07_RETRIEVAL_BENCHMARK_REPORT.md` | 待回填 |
| RC-07 | 专家评分校准 | 实施中 | `08_AI_QUALITY_CALIBRATION_REPORT.md` | 待回填 |
| RC-08 | 经验治理（策略/过期/撤销） | 实施中 | `09_LEARNING_GOVERNANCE_REPORT.md` | 待回填 |
| RC-09 | 审批规则可解释性 | 实施中 | `10_APPROVAL_RULE_EXPLAINABILITY_REPORT.md` | 待回填 |
| RC-10 | 真实/脱敏投标 E2E | 待实施 | `11_REAL_BID_E2E_ACCEPTANCE_REPORT.md` | 待回填 |
| RC-11 | 过程指标与 summary 契约 | 待实施 | `12_POSTGRES_MIGRATION_CONCURRENCY_REPORT.md` 前的指标报告待补 | 待回填 |
| RC-12 | PostgreSQL 迁移/并发 | 待实施 | `12_POSTGRES_MIGRATION_CONCURRENCY_REPORT.md` | 待回填 |
| RC-13 | 最终验收/文档 | 待实施 | `13_FINAL_RC_ACCEPTANCE_REPORT.md`、`14_CHANGELOG.md` | 待回填 |

注：用户指定文件名 `02_TOOL_LOOP_RELIABILITY_REPORT.md` 对应 RC-01；主台账保留指定文件名，不做重命名。

## 4. 当前验证基线

- P0 完成后全量后端：`2324 passed, 888 skipped, 1 warning in 999.36s`，零失败。
- Web 基线：57 files / 434 tests，typecheck/lint/build 通过。
- 每批模块均执行 ruff、目标测试、相邻回归；模块报告记录真实命令和结果。

## 5. 数据/环境阻塞（最终按 PARTIAL 或 NOT VERIFIED 口径）

- 真实招标文件、标注 qrels、专家评分样本未提供：不得用合成数据冒充真实评估。
- 真实模型端点/function-calling 未验证；生产 PostgreSQL、生产部署、生产备份恢复不在本地 RC 范围。
- CI 仍需对最终分支全绿取证；未获授权前不推送 main、不发版。

## 6. 回滚

每 RC 独立提交；按提交逐批 revert 即可。当前无新增/改写历史迁移，无生产状态变更。
