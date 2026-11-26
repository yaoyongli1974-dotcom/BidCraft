# v0.33.1 Phase 0：下一轮实施计划

## 1. 总原则

- 基线固定为 `main` `373499729a651c4d7bb69527379b0201b7ec7d1a`，不得回退到旧 SHA。
- 每阶段在独立分支上小步提交；先测试取证，再最小修复；每阶段有独立文档与真实命令输出。
- 不合并 main、不打 tag、不创建 Release、不部署生产；只有用户明确授权才执行。
- 数据/环境阻塞不得用合成数据、假模型、临时 pgserver 冒充真实验收。
- 新增数据库字段/表必须有迁移、降级、SQLite/PG 兼容与说明；API 变更必须同步前端类型/归一化/测试。

## 2. Phase 1：两个 P0 验收缺口收口

1.1 普通审批链解释完整性
- 文件：`bidcraft/boq.py`（`pending_for_user`、`_chain_explain`/`_stage_explain`）、`bidcraft/commercial.py` 对应 pending 聚合、`bidcraft/api/routes/approvals.py`。
- 目标：所有 `submitted` 审批项，无论链为空、普通条件链还是规则链，均返回与当前可审级对应的 `approval_explain`；空链返回明确“项目 owner 审批”解释，不得编造审批人。
- 测试：更新 `tests/test_approvals_pending.py`、`tests/test_quote_approval_condition.py` 字段期望；新增普通条件链和空链 pending 解释断言；不删除旧断言。
- 前端：`web/src/lib/types.ts`、`web/src/api/boq.ts`/`shared.ts`、`PendingApprovals.tsx`，确保后端解释优先且旧后端仍可本地兜底。

1.2 `vector_ready` 诊断口径
- 文件：`bidcraft/knowledge.py::_vector_fields`、KB API payload、`web/src/lib/format.ts`、`AdminKnowledgeTab.tsx`。
- 目标：`vector_total` 表示可发布块数；新增/明确草稿和不可发布块数量，`vector_ready` 只在可发布数>0且全部可发布块已嵌入时为 true；避免草稿块导致假 stale。
- 测试：覆盖 NULL/published/draft/review/approved/rejected 的块；验证 ready/stale/embedded 计数。

## 3. Phase 2：真实数据与未测量契约

- 保持 `source=user-provided`、全文件合成标记扫描、dataset gate 和 CI 非门禁数据状态输出。
- 真实招标件、qrels、专家评分未提供时，相关测试维持显式 `DATA BLOCKED (skip)`，指标保持 `measured=false`。
- 准备可复现的数据契约模板与用户交接清单；不得将合成 fixture 改标为真实。

## 4. Phase 3：文档质量与跨章节/大文档/DOCX-PDF 验证

- 复用现有 DOCX engine、validator、golden 测试与 export gate；基于真实或脱敏长文档数据验证分页、目录、页眉页脚、表格跨页、图片与引用。
- 无真实大文档时仅做合成工程验证并明确“未做真实版式验收”。

## 5. Phase 4：安全、权限与审计

- 核验工具脱敏、预算、call_id 审计、审批/经验策略 owner 限制、finding IDOR、plugin scope/signature、导出门禁 force 原因。
- 新增测试必须证明拒绝路径（403/404/409）、审计记录和跨项目隔离，不以正向路径替代。

## 6. Phase 5：性能、恢复与稳定性

- 基于现有 job queue/fencing/reaper/heartbeat/GenerationLease/PendingCharge 机制验证并发、恢复、退款唯一归属、SQLite 单写锁和 PG SKIP LOCKED。
- 性能基线仅用隔离 RC 环境；生产负载、soak、告警按 NOT VERIFIED 处理。

## 7. Phase 6：PostgreSQL、迁移与 CI

- 维持 Alembic 单 head；新增迁移须可升级/降级、SQLite/PG 兼容、备份前置说明。
- 真实 PG job 继续包含 `tests/postgres/**` 和数量/跳过/失败门禁；最终 SHA 必须重新取证 9/9。
- 生产等价 PG、PgBouncer、权限/连接池需独立授权与窗口。

## 8. Phase 7：API/前端产品闭环

- 所有 API 变更同步 `web/src/lib/types.ts`、normalizer、apiSurface/outboundContract、组件测试。
- 不改变既有导出硬门禁；不新增仅展示“成功”的 mock-only UI。

## 9. Phase 8：最终验收与发版准备

- 汇总所有阶段：代码基线、变更文件/函数、迁移、API/前端、后端/Web/PG/CI、skip 分类、真实数据结果、大文档结果、安全/性能/恢复、DATA BLOCKED、GO/CONDITIONAL GO/NO-GO。
- 只有在所有 P0 通过、无 Critical/High 未关闭、报告完整时才可提出 release 建议；任何 main push/tag/Release/deploy 仍需用户明确授权。

## 10. 暂停条件

出现以下情况立即暂停并输出风险报告：新增或改写历史迁移、生产数据修复/删除、真实模型凭据调用、生产 PostgreSQL 或部署、改变核心权限/导出门禁语义、数据契约需要用户确认、远程 CI 在最终 SHA 变红。
