# v0.32 API 变更记录（Phase 7）

> 分支：`feature/v0.32-ai-engine`；基线 v0.31.3（252 路由 / 91 个 Pydantic 请求模型）
> 计数权威：`tests/test_api_package.py`（运行时路由 274、请求模型 93）、`tests/test_v030_stage8_observability.py`（路由 docstring 同步）
> 所有新增路由沿用既有鉴权/审计：`_project` / `_require_write` / `require_super` / 企业 owner，未新增绕过路径。

## 1. 数量变化

| 项 | v0.31.3 | v0.32.0 | 增量 |
| --- | --- | --- | --- |
| API 路由 | 252 | 274 | +22 |
| Pydantic 请求模型（`bidcraft/api/schemas.py`） | 91 | 93 | +2 |

22 条新路由 = 章节规划 7 + 管理端知识资产 5 + 企业端知识资产 5 + 检索预览 1 + Agent 工具目录 1 + 经验 1 + 质量报告导出 1 + 质量汇总 1。

## 2. 新增路由（22 条）

### 2.1 章节规划（7，Phase 1）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/projects/{ref}/chapters/{node_id}/plan` | 读取当前生效规划 |
| POST | `/projects/{ref}/chapters/{node_id}/plan` | 按提交内容创建规划（含 `package_id`、`updates` 兼容形态） |
| POST | `/projects/{ref}/chapters/{node_id}/plan/generate` | 生成规划（兼容别名），真实 ID 校验后落库 |
| POST | `/projects/{ref}/chapters/{node_id}/plan/regenerate` | 追加新版本并使旧确认失效 |
| PUT | `/projects/{ref}/chapters/{node_id}/plan` | 编辑规划，追加 UPDATED 版本 |
| POST | `/projects/{ref}/chapters/{node_id}/plan/confirm` | 确认规划（状态机合法跃迁） |
| GET | `/projects/{ref}/chapters/{node_id}/plan/history` | 规划版本历史 |

写操作需项目写权限并写审计；跨项目/跨节点/跨标段引用被拒绝。规划状态经 `_chapter_plan_gate` 进入同步与异步正文生成，`review` 模式要求已确认、`review_strict` 连无规划新章节一并阻断、`auto` 放行但记 warning。

### 2.2 管理端知识资产（5，Phase 2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/admin/kbs/{kb_id}/assets` | 资产列表（结构化字段/状态/溯源） |
| GET | `/admin/kbs/{kb_id}/assets/duplicates` | 同库重复候选组 |
| POST | `/admin/kbs/{kb_id}/assets/duplicates/{group_id}/resolve` | 裁决重复组（resolve/dismiss 幂等） |
| POST | `/admin/kbs/{kb_id}/assets/{document_id}/extract` | 从真实 KbChunk 确定性提炼结构化字段 |
| POST | `/admin/kbs/{kb_id}/assets/{document_id}/review` | 审核状态跃迁 |

全部 `require_super`；静态 `duplicates` 路由先于动态 `{document_id}` 注册；动作写 `kb.document_extract` / `kb.document_review` / `kb.duplicate_resolve` 审计。

### 2.3 企业端知识资产（5，Phase 2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/enterprise/{enterprise_id}/assets` | 企业可访问资产列表 |
| GET | `/enterprise/{enterprise_id}/assets/duplicates` | 企业容器内重复候选 |
| POST | `/enterprise/{enterprise_id}/assets/duplicates/{group_id}/resolve` | 重复组裁决 |
| POST | `/enterprise/{enterprise_id}/assets/{document_id}/extract` | 结构化提炼 |
| POST | `/enterprise/{enterprise_id}/assets/{document_id}/review` | 审核跃迁 |

读要求企业可访问、写要求企业 owner（member 403、outsider 404）；`kb_id` 永不信请求体，一律由企业容器解析；跨企业文档访问失败；审计 `enterprise.*`。

### 2.4 其余新增（5）

| 阶段 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| Phase 3 | GET | `/projects/{ref}/retrieval/preview?q=&limit=` | viewer 可读的只读检索预览，返回溯源/重排/可信/降级与 diagnostics |
| Phase 4 | GET | `/agents/{agent_type}/tools?ref=` | 工具目录（name/description/input_schema/side_effect/readonly）；带 `ref` viewer 可读，无 `ref` 需全局管理权限 |
| Phase 5 | GET | `/projects/{ref}/lessons?node_id=&limit=` | viewer 安全的 ADVISORY 建议经验读取，审计 `learning.read` |
| Phase 6 | GET | `/projects/{ref}/evaluation/reports/{report_id}/export` | Markdown 报告附件；跨项目 404，viewer 安全，无 force 绕过 |
| Phase 6 | GET | `/projects/{ref}/evaluation/summary?refs=` | 跨项目质量汇总，仅聚合当前用户有权访问的项目 |

Phase 5 的 lessons 使路由 271→272；Phase 6 的 export + summary 使 272→274。请求模型由 Phase 1 的 3 个章节规划模型（`ChapterPlanGenerateRequest` / `ChapterPlanUpdate` / `ChapterPlanConfirm`）与 Phase 2 的 2 个知识资产模型（`KbAssetReview` / `KbDuplicateGroupResolve`）构成增量；计数口径 91→93（`tests/test_api_package.py`）。

## 3. 响应结构变化（既有路由的契约演进）

- **Passage 溯源与重排**（`bidcraft/knowledge.py::Passage`）：保留 `kb/source/text/score`，新增 `document_id`、`page`、`heading`、`kb_id`、`chunk_id`、`chunk_index`、`score_source`、`score_components`、`rerank_score`、`freshness`、`readiness`。`trace_label` 输出 `块 <id>#<index>`；`rerank_hybrid()` 只写 `rerank_score`，不覆盖调用方持有的原始 `score`。无 provider 元数据时 `source` 为空并进入 title_only/suppressed，不再用 KB 名称冒充来源。
- **KnowledgeContext / ref**（`bidcraft/services/job_support.py`）：`knowledge_context()` 返回 tuple 兼容对象，`__iter__` 仍只 yield `(text, downgrades)`；新增属性 `text`、`downgrades`、`refs`、`diagnostics`。每条 ref 含 `id/kb/kb_id/chunk_id/chunk_index/document_id/page/heading/source/snippet/score/rerank_score/trust_tier/degraded`。失败 KB 记入 `diagnostics.failed`；缺失可信/降级信号保留 `null`，不补 `false`/`trusted`/0。
- **AgentOutput.tool_calls**（`bidcraft/orchestration/registry.py`）：新增 `tool_calls` 字段，每条轨迹**恰好 8 键** `{name, args, result, verification, audit_id, ts, is_error, ms}`；`verification ∈ {verified, unverified, failed}`；校验失败拒绝该输出。持久化到 `WorkflowTask.output["tool_calls"]` 与 `run.result["tool_calls"]`（上限 100），无新表。
- **Fact 确认与来源**（`bidcraft/ledger.py::FactItemV032`）：`FactItem` 之上新增 `confirmed_by`、`confirmed_at`、`source_file_id`、`source_file_name`、`source_quote`、`source_page`；确认人/时间仅由服务端盖章，客户端不能自报。`FactItem`/C1 交换格式 JSON Schema 保持不变，属于响应侧扩展。
- **质量报告零分母语义**（`bidcraft/evaluation/coverage.py` 等）：零分母 `ratio=None`、`measured=false`，不再把「无数据」折算为 100%；grade/verdict 缺失返回 `null`/「—」，不再默认 C/warn；coverage/samples/mismatches 保留 null/0 与 measured 语义；finding 的 stable key 来自真实 Issue/Requirement/Plan/Fact/Audit ID。
- **null 不伪造**（全局契约）：前端 normalizer 对 `null`/`undefined` 不补 `ok`/`verified`/`confirmed`/`trusted`；`apiSurface.test.ts`、`outboundContract.test.ts` 增加新 key 白名单并保留严格 body-key 断言；`updateFact` 只发送 value/status/source_*，不前端自报确认信息。

## 4. 兼容性与不改项

- 所有新增项为加法式演进：旧调用方仍可用 `tuple` 解包 `(text, downgrades)`、旧 `registry.call_tool(name, args, ...)` 两参形态、`FactItem` 原字段与 `save_outline`/`needs` 语义。
- 未改动的门禁：`export_gate.py` 逻辑与阈值、章节确认要求、风险一票否决、MCP stdio 本地受信任语义、插件沙箱/allowlist/Ed25519 验签。
- 权限不变：超管全放行、项目 owner/editor/viewer 口径、企业 owner 写、个人库隔离、项目/标段作用域 fail-closed。
- 计数证据：`tests/test_api_package.py`（274/93）、`tests/test_v030_stage8_observability.py` 路由 docstring、前端契约测试；历史迁移 0001-0012 零改动，Alembic 单 head `0015_fact_confirmation`。
