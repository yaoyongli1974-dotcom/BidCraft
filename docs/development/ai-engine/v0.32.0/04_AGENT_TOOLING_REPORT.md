# v0.32 Phase 4 Agent Tooling 报告

## 1. 范围与基线

- 分支：`feature/v0.32-ai-engine`
- 基线：`b3d0834`（Phase 3）
- 目标：让模型的 tool_call 走唯一既有工具系统（MCP/registry），具备服务端工具策略、权限校验、真实执行、结果验证、审计与前端追踪；禁止 Agent 直写 DB 绕过业务服务。
- 约束：无新表/迁移，无历史迁移编辑，无测试删除/断言放宽，不以 mock 充当工具真实执行证据。

## 2. 实现证据

| 编号 | 项 | 实现 | 证据 |
|---|---|---|---|
| 1 | 真实工具循环 | NEW `generation/tool_loop.py::run_tool_loop` 复用 `ModelClient.complete_with_tools`；忽略 finish_reason，只按 tool_calls 是否为空继续；支持 OpenAI 与 Ollama 停止语义 | `tests/test_tool_loop.py` |
| 2 | 预算/错误边界 | `max_rounds=6`、`max_chars=8000`、`max_result_chars=2000`；溢出 `ToolLoopBudgetError`，必需工具失败 `ToolVerificationError`，非法工具名/坏 JSON 執行前拒绝并回喂错误 observation | `test_tool_budget_*`、`test_tool_required_verification_*` |
| 3 | 唯一工具系统 | executor 为 `registry.call_tool_with_trace`，没有第二套工具注册；`registry.call_tool(name,args,*)` 旧两参调用不变 | `test_registry_call_tool_trace` |
| 4 | 权限收口 | `registry.call_tool` 接收 readonly/actor/project_id/package_id/run_id/task_id；mcp built-ins 用 `authorize_project(s)` 基于 actor 授权；project ref 绑定后模型不能换项目；个人/企业 KB 仍走既有 scope | `test_mcp_builtin_requires_actor_and_authorization`、`test_cross_project_viewer_denied` |
| 5 | 写操作门禁 | readonly 默认；写策略在 actor 为 None 时 fail-closed；mutating 工具仍需既有 export_gate/approval 业务服务，未新增绕过接口 | `test_write_policy_requires_actor` |
| 6 | 审计 | `mcp._call_tool` 每次 built-in 记录 `agent.tool.call`（actor/project_id/tool/isError 等）；插件调用把 actor/project/run/task 写入 `plugins.record_call`；拒绝也记审计 | `test_mcp_audit_row_project_and_user`、`test_plugin_rejection_audited` |
| 7 | 结果验证 | trace dict 固定 8 键 `{name,args,result,verification,audit_id,ts,is_error,ms}`；verification ∈ verified/unverified/failed；结果截断；必需工具 unverified/failed 直接失败任务，不伪造成功 | `test_tool_verification_contract`、`test_required_tool_failure_fails_task` |
| 8 | trace 持久化 | `AgentOutput.tool_calls` 校验契约后写入 `WorkflowTask.output["tool_calls"]` 与 `run.result["tool_calls"]`（上限 100），无新表/迁移 | `test_agent_output_tool_calls_persisted` |
| 9 | Actor 传递 | `workers/agent.py::_actor_from_job` 从 Job.payload owner_id/user_id 取真实 User → `run_workflow` → `dispatch_task` → AgentContext → registry.call_tool | `test_worker_actor_propagation` |
| 10 | 服务端策略 | `planner.TOOL_POLICIES/ToolPolicy/tool_policy` 纯代码配置；用户 payload 的 `tools/*tool*` 键被 `orchestrator._task_payload_for_agent` 剥离；technical agent 默认只读 get_project/get_ledger/search_knowledge | `test_user_tool_keys_stripped`、`test_technical_agent_readonly_policy` |
| 11 | 工具目录 API | `GET /agents/{agent_type}/tools`（`?ref=` viewer 可读；无 ref 需全局管理权限）返回 name/description/input_schema/side_effect/readonly；路由 270→271 | `tests/test_tool_api_v032.py`、`tests/test_api_package.py` |
| 12 | 前端追踪 | AgentsTab 展开每个 task 的 tool_calls：参数、截断结果、verified/unverified/failed 徽标、audit_id、时间、is_error/ms；注册表卡片懒加载工具目录；resume/cancel/approve/review 后刷新 | `AgentsTab` tests、50 files / 394 web tests |
| 13 | 契约防伪造 | 前端 normalizer 对 null/undefined 不补 ok/verified；apiSurface/outboundContract 增加 agentTool/toolCallTrace key allowlist，保留严格 body-key 断言 | `apiSurface.test.ts`、`outboundContract.test.ts` |
| 14 | 无 DB 越权写入 | 工具循环只调用 registry/mcp 业务服务；没有新增 SQL 写入或模型直写字段；只读策略默认，审计行与 trace 一一对应 | `test_tool_loop_uses_executor_only`、静态 diff 检查 |

## 3. 关键文件

- 工具循环：`bidcraft/generation/tool_loop.py`
- 工具系统：`bidcraft/orchestration/registry.py`、`bidcraft/mcp.py`、`bidcraft/plugins.py`
- 工作流：`bidcraft/orchestration/{orchestrator,planner,approval}.py`、`bidcraft/orchestration/agents/technical.py`、`bidcraft/workers/agent.py`
- API：`bidcraft/api/routes/orchestration.py`、`tests/test_tool_api_v032.py`
- 前端：`web/src/lib/types.ts`、`web/src/api/orchestration.ts`、`web/src/pages/tabs/AgentsTab.tsx`、`web/src/api/{apiSurface,outboundContract}.test.ts`、`web/src/styles/workspace.css`

## 4. 权限模型

- 服务端 `ToolPolicy` 决定可用工具集合，用户 payload 无法注入/扩展。
- actor 从 Job 负载的真实 owner/user 解析；API 入口继续使用 `current_user()`；MCP stdio 本地操作者保留既有受信任语义（现有测试要求）。
- `actor=None` 的写策略 fail-closed；只读工具也必须通过项目成员权限。
- 插件作用域/沙箱/Ed25519 验签与既有实现不变，工具调用补齐 actor/project/run/task 审计字段。

## 5. 验证

- `ruff check bidcraft tests scripts`：All checks passed；`git diff --check`：通过
- 后端目标测试：`tests/test_tool_loop.py`、`test_agent.py`、`test_agent_orchestrator.py`、`test_mcp.py`、`test_plugin_isolation.py`、`test_orchestration_api.py`、`test_plugins.py`、`test_tool_api_v032.py`、`test_api_package.py`、`test_v030_stage8_observability.py` → **120 passed, 1 warning**（仅 Starlette/httpx deprecation）
- 新增工具循环测试 7 项；API 新测试 6 项；全部使用真实 DB / registry / MCP / stdio plugin，模型 HTTP 响应为唯一 stub。
- Web：typecheck ✓、lint ✓、`npm test -- --run` → **50 files / 394 tests passed**、build ✓（1719 modules）
- 全量后端 pytest：`.venv/bin/python -m pytest -o addopts="" -q` → **2219 passed, 886 skipped, 1 warning in 1710.72s**，零失败（仅 Starlette/httpx deprecation）。

## 6. 残留风险 / 明确边界

- **BLOCKED（按约束）**：每个项目/工作流在 UI 配置工具策略需要新 DB 字段，本阶段禁止新增表和迁移；当前策略为服务端代码配置，可用 monkeypatch 在测试中验证，不能由 UI 动态编辑。
- 本地 MCP stdio 在 `actor=None` 保留既有受信任操作者语义；Agent 路径强制 actor 与审计。
- 工具目录 GET 不写审计（与既有 GET 路由一致）；viewer 访问需带项目 `ref`。
- `side_effect` 由 MCP readonly 行为派生，TOOL_SPECS 未声明该字段；若未来 spec 显式声明则优先采用。
- 真实外部模型/Ollama 的 function-calling 行为未配置；验证覆盖解析模型响应后的真实工具执行。

## 7. 回滚

1. 代码：删除 `bidcraft/generation/tool_loop.py`、`tests/test_tool_loop.py`、`tests/test_tool_api_v032.py`；revert 本阶段 agent 列出的 modified 文件。
2. API 路由数 271→270；`tests/test_v030_stage8_observability.py` 与 `tests/test_api_package.py` 同步回退。
3. 前端：revert `web/src/`，重建 `web_dist`。
4. 数据库：无新表/字段，无需 Alembic downgrade；已写入 JSON 的 tool_calls 随任务/运行记录保留或按业务清理。
