# v0.33.0 RC 变更记录（本地开发分支）

分支：`fix/v0.33.0-rc-reliability`；基线：`7fe741dcc7c066e9b77e1269fae3260f7894e389`（v0.32.2）。未推送 main、未打 tag、未创建 Release、未部署。

## 本地提交

| 提交 | 模块 | 摘要 |
|---|---|---|
| `77e1f56` | RC-01 | 工具结果验证严格化与必需工具失败轨迹检查 |
| `8dd27f5` | RC-02 | Agent 工具轨迹脱敏、预算门禁与审计关联强化 |
| `241a9bf` | RC-03 | 章节规划依赖失效判废与生成身份冻结 |
| `f5c9159` | RC-04 | 知识资产内容变更撤销审核与检索/查重生命周期收紧 |
| `591891c` | RC-05 | 生成退款单一归属、失败 fencing 与队列竞态加固 |
| `99c0e3b` | RC-06/07 | 检索基准与专家校准未测量契约 |
| `e7fab57` | RC-12 | 真实 PostgreSQL v0330 迁移往返与并发验证 |
| `be4f8b9` | RC-08/09 | 经验治理与审批规则可解释性 |
| `61ed523` | RC-10/11 | 真实件数据门禁与未测量指标契约；451 web tests |
| `2efee1e` | RC-13 | 对齐新增解释字段与真实件来源契约；5 targeted passed |

## 后端主要改动

- `bidcraft/generation/tool_loop.py`、`bidcraft/security.py`、`bidcraft/orchestration/*`、`bidcraft/mcp.py`、`bidcraft/plugins.py`：工具验证、脱敏、预算、审计、审批门禁。
- `bidcraft/generation/chapter_plan.py`、`bidcraft/api/routes/projects.py`、`bidcraft/workers/generation.py`、`bidcraft/generation/prompts.py`：规划依赖判废、状态机防回退、冻结身份、regenerate/package/lessons。
- `bidcraft/knowledge.py`、`bidcraft/knowledge_assets.py`、`bidcraft/enterprise/matching.py`、`bidcraft/api/routes/admin.py`：知识资产内容变更生命周期、可发布过滤、查重清理、企业匹配门禁。
- `bidcraft/jobqueue.py`、`bidcraft/services/job_support.py`、`bidcraft/generation/writer.py`、`bidcraft/workers/generation.py`、`bidcraft/api/shared.py`：退款单一归属、失败 fencing、心跳三振、SKIP LOCKED、lease 释放、确认章节保护。
- `bidcraft/evaluation/{learning.py,retrieval_metrics.py,quality_calibration.py,quality_metrics.py,tender_dataset.py,report.py}`、`bidcraft/boq.py`、`bidcraft/commercial.py`、`bidcraft/api/routes/{evaluation.py,commercial.py,approvals.py}`：经验治理、检索/校准未测量、审批解释、商务报价摘要规则、真实件门禁与 summary 契约。

## 前端主要改动

- 经验治理面板、审批规则解释、真实件/未测量指标展示、summary 与 measured 正常化、boq explanation 透传、相关 CSS 和测试。
- Web 全量：58 files / 451 tests，typecheck/lint/build 通过。

## API / 数据契约

- 路由：274 → 275（新增 `PATCH /projects/{ref}/lessons/{lesson_id}`）。
- Pydantic 模型：93 → 94（新增 `LessonPolicyUpdate`）。
- `ProjectUpdate.lesson_policy`、lesson payload 增加 `policy` 与 `grade/trust/expires_at/revoked`；summary 增加 `ref/name/report_version/created_at` 与未测量 stub；version/pending 增加 `approval_explain`。
- 无新增 Alembic revision；head 仍为 `0015_fact_confirmation`；历史迁移 0001-0012 零改动。

## 验证

- 后端全量：2364 passed / 1008 skipped / 1 warning，零失败。
- 真实 PG：119 passed（两轮稳定），无 env 时 119 skipped。
- Web：451 tests + build。
- ruff / diff-check / 版本 / 单 head / 历史迁移 / 无测试删除：全部通过。

## 数据与风险边界

- 真实招标件、检索 qrels、专家评分未提供：真实件 E2E 与检索/校准指标按“未测量 / PARTIAL”处理，未用合成数据替代。
- 真实模型、生产 PostgreSQL、CI 最终 HEAD、生产部署/备份恢复未验证。
- 结论：CONDITIONAL PASS / PARTIAL ACCEPTANCE；需明确授权后才可合并 main、打 tag、创建 Release 或部署。
