# v0.33.0 RC-02：Agent 执行安全、审计与脱敏

## 1. 实际代码审查

调用链：`orchestrator.dispatch_task → TechnicalAgent.execute/_tool_assisted → run_tool_loop → registry.call_tool_with_trace/call_tool → mcp._call_tool/_call_plugin_tool → plugins.call_tool → AuditLog`；轨迹持久化于 `WorkflowTask.output` / `WorkflowRun.result`，经 `api/routes/orchestration.py` 暴露。

已确认既有能力（未重建）：MCP 内置工具项目授权与 `ref` 绑定（`mcp.py:366-407`）、插件沙箱/SSRF/allowlist（`plugins.py:171-240,429-439,789-857`）、写工具项目绑定与显式 actor（`registry.py:364-386`）、工作流预算与终态幂等（`orchestrator.py:144-160,289-301,462-467`）、`ToolInvocation.audit_id` 契约（`tool_loop.py:44-51`）。

## 2. 确认问题

| 缺口 | 位置 | 旧行为/风险 |
|---|---|---|
| 标准策略未失败即失败 | `planner.py:91-98`、`tool_loop.py` | 工具失败只记轨迹，模型继续输出文本即可让任务成功 |
| token 预算未计入 | `orchestrator.py:426-440` vs `agents/technical.py` | `ModelClient.usage` 从不写入 `result['tokens']`，`max_tokens` 形同虚设 |
| 内置写工具缺少审批 Gate | `registry.py:333-361` | `_authorize_mutating_tool` 仅用于插件；内置 `run_check/export_document` 可在等待审批时执行 |
| 审计 ID 猜测 | `registry.py:420-442` | 扫描最近 100 条按 tool/run/task/project 猜 ID，可能复用历史行或给未审计失败配上旧 ID |
| Trace 明文泄露 | `tool_loop.py:200-221,334-343`、`mcp.py:324-330` | 模型参数、工具结果、审计 detail 可能含 Bearer/API Key/密码；any project viewer 可读 |
| 无循环墙钟预算 | `tool_loop.py:258-347` | 6 轮 × model_timeout + 插件时间，task 边界才检查 `max_seconds` |
| 同循环重复执行 | `tool_loop.py:280-319` | 相同工具/参数可被模型重复调用，写工具风险更大 |
| 审计静默失败 | `mcp.py:357-364` | 审计写失败只 warn，工具照常返回成功，`audit_id` 为空 |
| 插件拒绝无痕 | `mcp.py:617-620` | disabled / allowlist 拒绝没有 `agent.tool.call` 审计 |

## 3. 修改

1. `bidcraft/security.py`：新增 `redact_text()` / `redact_mapping()`，遮蔽 Bearer、API Key、password、token、secret、cookie 等；按 key 递归脱敏，英文词按边界匹配。
2. `bidcraft/generation/tool_loop.py`：
   - `_trace()` 对 `args`/`result` 强制脱敏；
   - `is_error` 纳入 `_explicit_failure(payload)`，业务失败不再 `is_error=False`；
   - 新增 `max_seconds` 墙钟预算（轮次间与调用后检查，软截止，明确不抢占阻塞中的同步 executor）；
   - 新增 `fail_on_tool_error`：任一失败轨迹禁止最终成功；
   - 新增 `dedupe=True`：同一循环内同工具同参数且已验证成功的结果复用，不重复调用 executor；
   - `_check_required()` 先于全局失败检查，保留既有“必需工具”错误语义。
3. `bidcraft/orchestration/planner.py`：`ToolPolicy` 增加 `max_seconds=60`、`strict=True`、`dedupe`，`as_dict()` 同步输出；写策略自动 dedupe。
4. `bidcraft/orchestration/agents/technical.py`：工具循环传入预算/strict/dedupe；`_usage()` 把真实 `ModelClient.usage` 写入 `result['tokens']` 与 `usage`，工具失败/降级路径同样计入。
5. `bidcraft/orchestration/registry.py`：
   - `_check_pending_approval(run_id)` 对所有 `readonly=False` 调用生效（内置与插件），不再只管插件；
   - `call_tool_with_trace()` 生成 `call_id`，审计关联只按本次 `call_id`，不再猜测历史行。
6. `bidcraft/mcp.py` / `bidcraft/plugins.py`：透传 `call_id`；审计 detail 含 `call_id`；`_safe_args` 先脱敏再截断；审计写失败抛 `ToolError`；插件 disabled / allowlist 拒绝走 `_audit_plugin_rejection`。

无新增 Migration、无 API 路由/schema 变化、无前端变化、无业务数据表写入。

## 4. 测试与结果

新增 `tests/test_v0330_agent_security.py`（8 项）：轨迹脱敏、失败即失败、墙钟预算、重复调用去重、真实 token 统计、等待审批禁止内置写工具、审计失败不放行、audit_id 不猜测。

```bash
.venv/bin/python -m ruff check bidcraft tests
.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0330_agent_security.py tests/test_v0330_tool_validation.py \
  tests/test_tool_loop.py tests/test_agent_orchestrator.py
.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_mcp.py tests/test_plugins.py tests/test_plugin_sandbox.py \
  tests/test_plugin_guard.py tests/test_tool_api_v032.py \
  tests/test_orchestration_api.py tests/test_batch7_orchestration.py \
  tests/test_v030_stage4_fencing.py tests/test_worker_reliability_matrix.py
```

实际结果：`All checks passed!`；`46 passed in 7.78s`；`127 passed, 1 warning in 36.22s`。

基线完整回归：`2270 passed, 888 skipped, 1 warning in 989.67s`（本模块修改前）。

## 5. 残余风险

- `max_seconds` 是轮次间的软截止；若同步 executor 自身长时间阻塞，需依赖插件 `plugin_timeout` / 模型 `model_timeout`，本循环不能抢占线程。
- `dedupe` 只在单次循环内生效；跨进程/跨重试的写工具幂等需要 RC-05 的 Job 级状态与 fenced 终态共同保证。
- 脱敏基于已知敏感键/模式；自定义插件若把密钥写进非敏感字段文本且无关键字，无法保证识别。

## 6. 回滚

回退 `security.py`、`tool_loop.py`、`planner.py`、`technical.py`、`registry.py`、`mcp.py`、`plugins.py` 对应 hunk，删除 `tests/test_v0330_agent_security.py`；无数据迁移回滚。
