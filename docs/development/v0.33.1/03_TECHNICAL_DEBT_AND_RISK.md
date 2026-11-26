# v0.33.1 Phase 0：技术债与风险登记

## 1. 判定口径

- 以当前 `main` `3734997` 与 `docs/development/rc/v0.33.0/00–14` 为事实来源。
- RC 文档是发版前快照；当前主线已完成 v0.33.0 版本 bump、tag、Release。不得把 RC 文档中“未推送/未打 tag/未 bump”当作当前状态。
- 用户任务把“普通审批链缺解释”和“vector_ready 统计偏大”列为 Phase 1 必须收口的两项；技术债级别记为 P2，但验收级别按 P0 处理。

## 2. 未关闭事项

| ID | 级别 | 事项 | 证据 | 分类 | 是否需暂停/审批 |
|---|---|---|---|---|---|
| TD-01 | 验收 P0 / 技术 P2 | `approval_explain` 仅覆盖含 `rules` 的审批链；普通 `min_amount`/`packages`/`currencies` 链与空链 pending item 仍无解释 | `bidcraft/boq.py:1586-1599,1624`；`docs/development/rc/v0.33.0/10_APPROVAL_RULE_EXPLAINABILITY_REPORT.md:19`；`13_FINAL_RC_ACCEPTANCE_REPORT.md:36` | 代码可修 | 需同步更新 `tests/test_approvals_pending.py:465`、`tests/test_quote_approval_condition.py:576` 的字段期望，不得删/放宽断言 |
| TD-02 | 验收 P0 / 技术 P2 | `vector_ready` 按 KB 全部块数计算，草稿/不可发布块可能造成统计偏大 | `bidcraft/knowledge.py:121-165`；`05_KNOWLEDGE_ASSET_LIFECYCLE_REPORT.md:136-137` | 代码可修 | 需同步诊断口径与前端消费方 |
| TD-03 | P1 | 真实/脱敏招标件 E2E 未验收，清单缺失，测试显式 DATA BLOCKED skip | `tests/fixtures/tender_real/` 仅有示例；`bidcraft/evaluation/tender_dataset.py`；`tests/test_v0330_dataset_gate.py:128-132` | DATA BLOCKED | 暂停；需用户提供真实/脱敏样本和标注，禁止合成冒充 |
| TD-04 | P1 | 检索 qrels 缺失，Recall/Precision/MRR/NDCG 与系统率恒为未测量 | `bidcraft/evaluation/retrieval_metrics.py`；`docs/development/rc/v0.33.0/07_RETRIEVAL_BENCHMARK_REPORT.md:117-123` | DATA BLOCKED + 采集链路可补 | 数据暂停；采集链路接线可先做 |
| TD-05 | P1 | 真实专家评分缺失，Pearson/Spearman/Brier/分桶恒为未测量 | `bidcraft/evaluation/quality_calibration.py`；`08_AI_QUALITY_CALIBRATION_REPORT.md:86-92` | DATA BLOCKED | 暂停；跨量表需人工归一化 |
| TD-06 | P1 | 真实模型端点 / function-calling 未验证 | `bidcraft/generation/agent.py:176,280`；CI 仅假端点启动冒烟 | INFRA BLOCKED | 暂停；需真实端点、密钥与调用授权 |
| TD-07 | P1 | PostgreSQL 生产等价性不足：本地 pgserver 16.2 + 同机多线程，未覆盖 PgBouncer/权限/多机；迁移往返只到 0012 | `12_POSTGRES_MIGRATION_CONCURRENCY_REPORT.md:9-16,112-124`；`.github/workflows/ci.yml:493-518` | INFRA BLOCKED | 暂停；生产迁移窗口与备份+`backup-verify` 审批 |
| TD-08 | P1 | RC 报告中的“最终 SHA CI 未取证”是发版前快照；发版后 run `45807554195` 9/9 success，Phase 0 又确认目标 SHA 多次运行成功 | `docs/development/rc/v0.33.0/13_FINAL_RC_ACCEPTANCE_REPORT.md:53`；GitHub Actions API | 文档/取证可修 | v0.33.1 最终 SHA 仍需重新取证 |
| TD-09 | P2 | 文档漂移：RC 报告写 0.32.2/未 tag/未发版，但当前主线为 0.33.0 且已发版 | `13_FINAL_RC_ACCEPTANCE_REPORT.md:8,11,48`；`bidcraft/__init__.py:3`；`CHANGELOG.md:5` | 文档可修 | 无；不回改历史快照，在 v0.33.1 文档声明当前基线 |
| TD-10 | P1 | 回滚非数据中性：代码 revert 不还原已写入的 review_status、lesson_policy、validation.quote 等状态 | `bidcraft/knowledge.py:458-461`；`projects.py:1395-1407`；`bidcraft/commercial.py:320-323` | 预防可修 + 生产决策 | 若已产生生产数据，回滚方案需单独审批 |
| TD-11 | P2 | RC-03 依赖判废只比对规划 JSON 已保存字段，模型省略 score/criteria 时变化不触发 | `docs/development/rc/v0.33.0/04_CHAPTER_PLAN_CONSISTENCY_REPORT.md:94-99` | 代码可修 | 无 |
| TD-12 | P2 | RC-02 残留：max_seconds 为轮次间软截止、dedupe 仅单次循环、脱敏只覆盖已知键/模式 | `03_AGENT_SECURITY_AUDIT_REPORT.md:62-66`；`02_TOOL_LOOP_RELIABILITY_REPORT.md:62` | 可接受残留/局部可修 | 无 |

## 3. 一致性正面核验

- API 路由 275：`tests/test_api_package.py:54` 与 `13_FINAL_RC_ACCEPTANCE_REPORT.md:27` 一致。
- Pydantic 请求模型 94：`tests/test_api_package.py:128` 与 `14_CHANGELOG.md:36` 一致。
- Alembic 单 head `0015_fact_confirmation`：实测 ScriptDirectory/get_heads 一致。
- Web 58 files / 451 tests：Phase 0 实测一致。
- 后端 2364 passed / 1008 skipped / 1 warning：v0.33.0 文档一致；Phase 0 全量回归结果另见 00 报告回填。

## 4. Phase 1 可立即开工范围

无需等待数据/生产授权即可开工：

1. 普通审批链（含空链）pending item 的 `approval_explain`，并同步测试/frontend 类型与归一化。
2. `vector_ready` 的可发布块口径，分离总数、可发布数、已向量化数、草稿数。
3. RC 文档当前版本状态说明（只新增 v0.33.1 文档，不回改历史报告）。
4. RC-03 依赖判废遗漏字段的保守补充（如以不降低安全为原则）。

DATA/INFRA BLOCKED 项只能准备契约、夹具、门禁与报告；不得把合成数据、假模型、pgserver 临时库冒充真实验收。
