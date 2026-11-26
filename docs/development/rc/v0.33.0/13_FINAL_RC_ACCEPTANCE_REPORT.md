# v0.33.0 RC 最终验收报告

## 1. 验收对象

- 分支：`fix/v0.33.0-rc-reliability`
- 基线：`7fe741dcc7c066e9b77e1269fae3260f7894e389`（v0.32.2，main 原 HEAD）
- 最终验证 HEAD：`2efee1e`（文档提交另计）
- 版本：后端 / pyproject / Web 均为 `0.32.2`；本 RC 未做版本 bump
- Alembic：单 head `0015_fact_confirmation`
- 本地提交链：`77e1f56` → `8dd27f5` → `241a9bf` → `f5c9159` → `591891c` → `99c0e3b` → `e7fab57` → `be4f8b9` → `61ed523` → `2efee1e`（契约期望修复）
- 未推送 main、未打 tag、未创建 Release、未部署、未改生产数据

## 2. 验收口径

使用五类状态：已完成并通过验证 / 已完成但验证不足 / 部分完成 / 未完成 / 因风险暂停。真实招标件、专家评分、真实模型与生产环境数据缺失时不得用合成数据冒充。

## 3. 20 项验收清单

1. **代码基线**：基线 SHA/分支/工作树/版本/Alembic 已核对，见 `00_RC_MASTER_TASKBOOK.md`、`01_BASELINE_AND_REGRESSION_REPORT.md`。状态：已完成并通过验证。
2. **变更文件与函数**：RC-01..RC-11、RC-12 每份报告列出文件/函数；合计 9 个本地提交。状态：已完成并通过验证。
3. **RC-01 工具验证**：严格证据/业务失败/文本未验证/必需工具失败轨迹；26 passed。状态：已完成并通过验证。
4. **RC-02 Agent 安全**：脱敏、预算、去重、审批门禁、审计 call_id 关联、usage 真实；46 + 127 passed。状态：已完成并通过验证。
5. **RC-03 规划一致性**：依赖变化判废、版本不回退、身份冻结、regenerate、package、lessons；31 + 102 passed。状态：已完成并通过验证。
6. **RC-04 知识生命周期**：内容变更撤销审核、可发布过滤、查重清理、企业匹配门禁；11 + 95 passed。状态：已完成并通过验证。
7. **RC-05 生成并发/账务**：单一退款归属、失败 fencing、三次心跳、PG SKIP LOCKED、lease 释放；9 + 99 + 全量通过。状态：已完成并通过验证。
8. **数据库迁移**：0013/0014/0015 在真实 PG 上 head→0001→head 的等价链往返、步骤降级、v0.33 产物校验；119 PG passed。状态：已完成并通过验证（pgserver 非生产）。
9. **API 变化**：新增 `PATCH /projects/{ref}/lessons/{lesson_id}`；路由 274→275；Pydantic 模型 93→94；summary 增字段、解释字段透传。状态：已完成并通过验证。
10. **前端变化**：经验治理、审批解释、未测量指标、真实件口径、header/normalizer 同步；Web 58 files / 451 tests + typecheck/lint/build 通过。状态：已完成并通过验证。
11. **新增测试**：RC-01 2 文件、RC-02 1、RC-03 1、RC-04 1、RC-05 1、RC-06/07 2+夹具、RC-08/09 2、RC-10/11 3、RC-12 1；未删除测试，未降低断言。状态：已完成并通过验证。
12. **后端全量**：最终全量 `2364 passed, 1008 skipped, 1 warning in 941.27s`，零失败；1008 skipped 为 PG opt-in 119、真实件 E2E 1 与既定环境跳过。状态：已完成并通过验证。
13. **前端全量**：`npm run typecheck`、`npm run lint`、`npm test -- --run`、`npm run build` 全通过；58 files / 451 tests；ErrorBoundary 测试打印预期堆栈。状态：已完成并通过验证。
14. **PostgreSQL/并发**：真实 PG 16.2 scratch DB；119 passed（两轮稳定），无 BIDCRAFT_TEST_PG_URL 时 119 skipped；无生产库操作。状态：已完成并通过验证（pgserver 非生产）。
15. **检索基准**：Recall@5/10、Precision@5、MRR、NDCG@10 与四类系统率；21 targeted + 42 回归；无真实 qrels → 未测量。状态：已完成但验证不足（真实数据阻塞）。
16. **专家评分校准**：Pearson/Spearman/Brier/binned；21 targeted + 42 回归；无真实专家评分 → 未测量。状态：已完成但验证不足（真实数据阻塞）。
17. **真实件 E2E**：dataset-gated parse→outline→generate→check→export；无数据集时显式 skip；临时清单 READY 路径烟测通过，未冒充真实验收。状态：部分完成（真实/脱敏数据集缺失）。
18. **未解决问题**：RC-09 普通条件链（无 rules）在 pending items 中仍缺解释；RC-04 vector_ready 对草稿计数可能偏大；RC-10 数据阻塞；RC-06/07 真实数据阻塞。状态：已完成并记录于各报告。
19. **残余风险**：pgserver 非生产、多线程非多机；真实模型/function-calling 未验证；生产备份/恢复/部署/权限未验证；远程 CI 最终 SHA 未取证。状态：已识别，未在项目内解决。
20. **RC/发版建议**：RC 结论为 CONDITIONAL PASS / PARTIAL ACCEPTANCE；建议用户提供真实数据集与专家评分并完成生产等价 PG/模型/CI 取证后再决定是否发版。当前不合并 main、不打 tag、不创建 Release、不部署。状态：因风险暂停（需授权/数据）。

## 4. 验证汇总

| 项目 | 结果 |
|---|---|
| ruff | `ruff check bidcraft tests scripts` All checks passed |
| diff-check | clean |
| 历史 migrations 0001-0012 | 与基线零 diff |
| 测试删除 | 与基线对比零删除 |
| 版本一致性 | 0.32.2（未 bump） |
| Alembic heads | 单 head `0015_fact_confirmation` |
| Web | 58 files / 451 tests，typecheck/lint/build 通过 |
| 真实 PG | 119 passed（两轮） |
| 后端全量 | 2364 passed / 1008 skipped / 1 warning，零失败 |
| 远程 CI | 未对最终本地 HEAD 取证（NOT VERIFIED） |

## 4.1 最终全量失败与修复记录

第一次最终全量：`5 failed, 2359 passed, 1008 skipped`。失败均为契约变更后的旧测试期望，不是生产回归：

- `tests/test_quote_versions.py::test_version_save_list_detail_restore_delete`：响应新增 `approval_explain`，测试字段集合更新。
- 三个 `tests/test_tender_quality_v031.py` 用例与一个 `tests/test_v0312_quality_details.py` 用例：真实件清单新增 `source: user-provided`，测试夹具补齐该字段。

修复提交 `2efee1e`，仅新增 5 行测试期望/夹具字段；未删测试、未降低断言。重跑目标 5 passed；第二次最终全量 2364 passed，零失败。

## 5. 结论

P0 全部通过；P1 代码实现与自动化测试完成，但真实业务数据、生产环境和远程 CI 仍有明确缺口。最终结论：**CONDITIONAL PASS / PARTIAL ACCEPTANCE**。

禁止在未经明确授权的情况下合并 main、推送 tag、创建 Release 或部署生产。回滚按每个本地提交逐批 revert；无历史迁移改写、无生产数据回滚需求。
