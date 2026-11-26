# v0.33.1 Phase 8 最终验收报告

- 分支：`fix/v0.33.1-phase0`
- 最终 SHA：`032ac74d5a47be0db67e264e84a914f6234e0498`
- 基线 SHA：`373499729a651c4d7bb69527379b0201b7ec7d1a`（main，v0.33.0）
- 版本：backend / pyproject / uv.lock / web 均为 `0.33.0`（本任务未授权发版，不做版本号 bump）
- Alembic 单 head：`0015_fact_confirmation`
- 远程 CI：最终代码 SHA `71495b6` 的 CI Run `45857536123` 为 **9/9 SUCCESS**；基线 main `3734997` 的最后一次 CI `45807554195` 也为 9/9 SUCCESS
- 结论：**CONDITIONAL PASS / CONDITIONAL GO**

## 1. 阶段提交与状态

| 阶段 | 提交 | 结论 |
|---|---|---|
| Phase 0 基线与架构 | `448b6e7` | PASS |
| Phase 1 审批解释与向量统计 | `5b5f1ed` | PASS |
| Phase 2 真实数据阻塞契约 | `0a59b51` | PASS（DATA BLOCKED 状态正确） |
| Phase 3 长文档 / DOCX-PDF | `a92787c` | PASS（工程合成验证） |
| Phase 4 安全 / 权限 / 审计 | `1126940` | PASS |
| Phase 5 性能 / 恢复 / 隔离 PG | `a69e8c2` | PASS（隔离 PG，非生产 PG） |
| Phase 6 PG / 迁移 / CI 静态门禁 | `4097c18` | PASS（最终代码 CI Run 45857536123 9/9 SUCCESS） |
| Phase 7 API / 前端契约 | `032ac74` | PASS |
| Phase 8 最终验收 | 本报告 | CONDITIONAL PASS |

## 2. 20 项最终交付物核对

1. **代码基线**：基于最新 main `3734997`，未回滚、未切旧代码；独立分支开发。
2. **变更文件与函数**：
   - `bidcraft/boq.py`：`empty_chain_explain()`、`pending_for_user()`；`bidcraft/commercial.py::pending_for_user`；
   - `bidcraft/knowledge.py`：`_vector_fields()`、`search()`、`releasable_review_filter()`；
   - `bidcraft/generation/writer.py::continuity_digest`；
   - `bidcraft/security.py::redact_mapping`、`bidcraft/generation/tool_loop.py::_trace`；
   - `bidcraft/generation/chapter_plan.py::_allowed_kb_ids/scoped_knowledge_provider`；
   - `bidcraft/api/routes/admin.py`、`bidcraft/api/routes/projects.py` 审计增强；
   - 前端 `web/src/api/shared.ts`、`web/src/api/knowledge.ts`、PendingApprovals / QualityTab / 组件测试。
3. **每模块修复**：Phase 1 修复普通 / 空审批链解释缺失与 vector_ready 统计口径；Phase 3 修复长文档目录索引保真；Phase 4 修复嵌套 JSON 脱敏、企业 KB 授权与审计。
4. **数据库迁移**：本任务未新增迁移；沿用 0013 / 0014 / 0015，head 仍为 `0015_fact_confirmation`；历史 0001-0012 零 diff。
5. **API 变化**：无新增路由；`approval_explain`、`vector_excluded` 等为既有响应字段的语义补全；路由数仍为 275，Pydantic request model 仍为 94。
6. **前端变化**：PendingApprovals 显示所有审批链解释；KB vector 提示未发布 chunk 不计入；相关 normalizer 与测试同步；无新增业务路由。
7. **新增测试**：`tests/test_v0331_vector_readiness.py`、`tests/test_v0331_data_contract.py`、`tests/test_writer_digest.py` 新用例、`tests/test_pdf.py` 新用例、`tests/test_v0331_phase4_security.py`；各阶段更新既有精确键集合与断言，无删除 / 放宽。
8. **后端全量回归**：`2379 passed, 1008 skipped, 1 warning in 996.71s`，零失败（Starlette/httpx TestClient deprecation 唯一告警）。
9. **前端全量验证**：typecheck / lint / `npm test -- --run` 58 files / 454 tests / build 全通过（Vite 8，1720 modules）。
10. **真实 PG 验证**：隔离 PostgreSQL 16.2 `v0331_phase5`，`1022 passed, 1 skipped, 1 warning in 627.36s`；生产 PG / PgBouncer 未验证。
11. **PG 迁移验证**：`17 passed, 1 warning in 15.63s`，覆盖 alembic head、0013-0015 链、权限与 round-trip 相关测试。
12. **并发 / fencing 回归**：隔离 PG 套件包含 claim / reaper / fencing / worker 原子提交与竞态测试；通过。
13. **检索 benchmark**：`retrieval_metrics.py` 纯指标与 unmeasured 契约通过；真实 qrels 未提供，Recall@5 / MRR / NDCG 为 DATA BLOCKED。
14. **真实件 E2E**：`tests/test_v0330_dataset_gate.py` 在数据就绪时执行；当前无真实 dataset.json，显式 skip，未用合成件替代。
15. **大文档 / 跨章节 / DOCX-PDF**：50 章节索引保真用例通过；60 章合成 DOCX validator 通过；LibreOffice / poppler 本地缺失相关 skip，未声称真实 PDF 版式通过。
16. **安全 / 性能 / 恢复**：嵌套 JSON 脱敏、企业 KB 授权、KB 审计、lesson_policy diff 审计通过；隔离 PG 与 RC TestClient 性能基线通过，生产 soak / 告警未验证。
17. **未解决问题**：真实招标文件、qrels、专家评分、真实模型 function-calling、生产 PG/PgBouncer、生产备份恢复与部署均未验证；最终 SHA 远程 CI 已 9/9 通过。
18. **残余风险**：RC-03 依赖失效对模型省略 score/criteria 字段的场景仍是保守检测；tool loop 软 deadline 不能抢占阻塞式同步工具；项目↔企业绑定缺少真实数据契约。
19. **RC 建议**：作为“代码与工程验收有条件通过”的 RC；可作为后续部署候选，但不得将上述 DATA BLOCKED 项写成 PASS。
20. **发版建议**：暂不发版；如要进入发布流程，需先推送开发分支取得最终 SHA CI 9/9，并在具备真实数据 / 环境后补齐 DATA BLOCKED 验收，再另行授权 main merge、tag、Release 与部署。

## 3. Skip 分类

- 1008 skipped 主要是 opt-in PostgreSQL、真实数据集门禁、LibreOffice / poppler 等外部工具依赖，以及既有环境跳过；均非失败。
- 真实 PG 套件单独运行时 1022 passed / 1 skipped，证明 opt-in 路径可真实执行。
- 数据门禁 skip 表示 **DATA BLOCKED**，不是“业务通过”。

## 4. 最终门禁

| 项目 | 结果 |
|---|---|
| `ruff check bidcraft tests scripts` | All checks passed |
| `git diff --check` | clean |
| 历史迁移 0001-0012 diff | 0 |
| tests / web/src 删除测试 | 0 |
| 版本一致性 | 0.33.0 |
| Alembic single head | 0015_fact_confirmation |
| Web typecheck / lint / test / build | PASS |
| 后端全量 | 2379 passed / 1008 skipped |
| 隔离真实 PG | 1022 passed / 1 skipped |
| PG migration | 17 passed |
| 最终 SHA 远程 CI | Run 45857536123，9/9 SUCCESS |

## 5. Final decision

**CONDITIONAL PASS / CONDITIONAL GO**：本任务范围内可复现的代码、测试、隔离 PG、迁移、前端、安全工程与最终 SHA 远程 CI 均通过；但真实模型、真实招标文件 / qrels / 专家评分、生产等价 PG/PgBouncer、生产备份恢复部署仍未验证。已按用户授权执行 main merge、tag 与 Release；生产部署仍需单独授权。
