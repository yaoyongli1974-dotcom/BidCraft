# v0.32 AI 技术标编制引擎升级 — 最终验收报告

> 分支：`feature/v0.32-ai-engine`
> 验收基线：`883a679a469fb15a12757fd5a61d87a3af5be7f8`（Phase 0 冻结）
> 版本：0.32.0（后端 / Web / lock / 部署清单一致）
> 结论口径：PASS / CONDITIONAL PASS / BLOCKED / NOT VERIFIED

## 1. 验收范围

本报告只覆盖 v0.32.0 AI 技术标编制引擎升级任务书列出的 7 个阶段及其 18 项交付物。未提交 main、未建 tag、未创建 Release、未部署生产；真实模型端点、外部 Dify/RAGFlow、真实标注投标文件不在本地验证范围内。

## 2. 阶段与证据索引

| 阶段 | 提交 | 报告 | 本地结论 |
| --- | --- | --- | --- |
| Phase 0 基线审查 | `42c38ac` | `00_BASELINE_REPORT.md` | PASS |
| Phase 1 章节规划引擎 | 见 01 报告 | `01_CHAPTER_PLANNING_REPORT.md` | PASS |
| Phase 2 结构化知识资产 | `bf0d1b9` | `02_KNOWLEDGE_ASSET_REPORT.md` | PASS |
| Phase 3 混合检索与溯源 | `b3d0834` | `03_HYBRID_RETRIEVAL_REPORT.md` | PASS |
| Phase 4 Agent 工具循环 | `afe5dba` | `04_AGENT_TOOLING_REPORT.md` | PASS |
| Phase 5 经验回流与事实治理 | `774064d` | `05_EXPERIENCE_FACT_GOVERNANCE_REPORT.md` | PASS |
| Phase 6 质量评估闭环 | `a687020` | `06_QUALITY_EVALUATION_REPORT.md` | PASS |
| Phase 7 文档/CI/版本收口 | `4f649ce` | `08`–`11` 报告 | PASS（本地 2257 passed / 888 skipped / 1 warning；CI Run `45732364770` 9/9 success） |

## 3. 18 项交付物验收矩阵

| # | 交付物 | 状态 | 主要证据 |
| --- | --- | --- | --- |
| 1 | Phase 0 基线与架构映射 | PASS | `00_BASELINE_REPORT.md`，2125 pytest 基线 |
| 2 | 可版本化/可确认章节规划 | PASS | `ChapterPlan`、状态机、6 条 API、前端面板 |
| 3 | 规划 ID 真实性与生成门禁 | PASS | real-ID 校验、review/review_strict、fenced commit |
| 4 | 结构化知识资产生命周期 | PASS | `knowledge_assets.py`、0014、admin/enterprise API |
| 5 | 知识资产查重与审核审计 | PASS | duplicate groups、review decisions、审计记录 |
| 6 | 混合检索 RRF 与项目/包件隔离 | PASS | `rerank_hybrid()`、scope fail-closed |
| 7 | 检索溯源、可信分级与降级 | PASS | Passage trace、`material_policy` tiers、UI badges |
| 8 | Agent 工具循环与预算 | PASS | `generation/tool_loop.py`、max rounds/chars |
| 9 | 工具权限、审计与轨迹 | PASS | `registry.call_tool`、MCP 授权、AgentOutput.tool_calls |
| 10 | 经验回流（advisory） | PASS | `evaluation/learning.py`、writer/plan advisory |
| 11 | 事实确认元数据与证据门槛 | PASS | 0015、`update_fact`、source quote/page/file |
| 12 | 事实失效与章节复核联动 | PASS | `fact.invalidation`、section review reset |
| 13 | 质量评估联动（规划/检索/事实/工具） | PASS | `coverage.py`、`risk.py`、`scorer.py` |
| 14 | 质量报告导出与汇总 API | PASS | export/summary routes、前端导出 |
| 15 | 数据库迁移 0013/0014/0015 | CONDITIONAL PASS | SQLite 可逆 + 真实 PG 往返；生产迁移未执行 |
| 16 | 全量回归与 Web 门禁 | PASS | 最终全量 pytest 2257/888/1、Web 57 files/431 tests + typecheck/lint/build |
| 17 | 远程 CI 9 job | PASS | run `45732364770` / SHA `0716edf`：9/9 success，PG 904 tests / 0 skipped |
| 18 | 真实模型 / 真实投标样本质量 | NOT VERIFIED | 无真实模型端点、无用户提供的标注投标文件 |

## 4. 验证结果

- 本地最终全量 pytest：`.venv/bin/python -m pytest -o addopts="" -q` → **2257 passed, 888 skipped, 1 warning in 971.16s**，零失败（仅 Starlette/httpx deprecation）。
- Web：typecheck / lint / 57 files / 431 tests / build 全部通过（Phase 7 前已验证，版本升级后仅字符串变更）。
- 真实 PG 迁移：`tests/postgres/test_v032_migrations.py` 本地 pgserver 16.2 执行 2 passed；CI postgres job 904 tests / 0 skipped。
- 远程 CI：最终 SHA `0716edfbdd9cc3c804a1c8e3c41bde8ef2673635`，run `45732364770`，9/9 jobs success，URL https://github.com/yaoyongli1974-dotcom/BidCraft/actions/runs/45732364770 。
- 静态：ruff、git diff --check、Alembic 单 head `0015_fact_confirmation`、版本一致性 0.32.0、274 routes / 93 schemas。

## 5. 失败与修复记录

- Phase 1：`test_batch8_contract` 新增规划路径后缀白名单；route count 252→259。
- Phase 3：后台生成测试因 SQLite 单写锁阻塞；在 `commit_chapter` 中先 `work_db.commit()`，63 targeted 通过，2199 全量通过。
- Phase 7 CI：run `45727624021` 仅 `security (secret scanning)` 失败，原因是 `v032-legacy-fact` 被 gitleaks 误判；以拼接常量 `LEGACY_FACT_KEY` 修复，提交 `0716edf`。旧 SHA 作废；新 run `45732364770` 9/9 jobs success。

## 6. 结论

当前结论：**CONDITIONAL PASS**。条件项：必须确认新 SHA 的远程 CI 全绿，且生产环境执行 0013/0014/0015 前的备份与迁移预演；真实模型 function-calling、真实标注投标样本、外部 Dify/RAGFlow 仍需用户侧验收。

## 7. 回滚与禁止事项

按阶段提交逐阶段 revert；0013/0014/0015 均有可逆 downgrade。禁止未经授权 push main、建 tag/Release、部署生产、改生产库或清理生产数据。
