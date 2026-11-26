# v0.33.0 RC-01：Agent Tool Loop 结果验证

## 1. 实际代码审查

- `bidcraft/generation/tool_loop.py`
  - `run_tool_loop()`：模型工具调用循环，执行 `executor` 并把 `role=tool` 观测回灌。
  - `_verification(payload, is_error)`：旧实现把任意非空文本、任意含非空字段的 dict 都判为 `verified`；只有显式 `isError/is_error/error` 才判 `failed`。
  - `_check_required(traces, required)`：旧实现只要求同名工具存在一次 `verified`，不检查同名失败轨迹。
  - `_trace()`：轨迹契约固定为 8 个键，不能通过增加字段来绕过。
- `bidcraft/orchestration/agents/technical.py::_run_tool_loop`：唯一生产启用路径；把 `ToolVerificationError` 转成 `status=failed`，不会把失败伪装成成功。
- `bidcraft/orchestration/registry.py::call_tool_with_trace`：返回 `ToolInvocation`，失败抛 `ToolExecutionError`；审计 id 来自真实 `AuditLog`。
- `bidcraft/generation/agent.py::complete_with_tools`：模型工具调用解析；Ollama `done_reason=stop` 仍按真实 `tool_calls` 继续。
- 现有回归：`tests/test_tool_loop.py`、`tests/test_agent.py`、`tests/test_tool_api_v032.py`。

调用链：`TechnicalAgent._run_tool_loop → ModelClient.complete_with_tools → run_tool_loop → registry.call_tool_with_trace → mcp._call_tool / plugins → AuditLog → role=tool 回灌 → _verification → _check_required → AgentOutput.tool_calls`。

## 2. 确认问题与风险

| 问题 | 旧行为 | 风险 |
|---|---|---|
| 非空文本误判成功 | `"工具执行失败"`、`"error: quota"` 均返回 `verified` | 模型可把失败文本当成功证据；`required_tools` 被错误满足 |
| 业务错误误判成功 | `{"ok": false, "error": "配额不足"}`、`{"status":"failed"}` 被当成普通结构化字段，`verified` | 工具执行成功与业务成功混淆，任务被标成 `ok` |
| Required Tools 不严格 | 同一工具先失败后成功仍可通过 | 失败审计被掩盖，无法证明“必需工具全部成功” |
| 语义不清 | `verified` 没有说明是否等于业务结论通过 | `run_check` 返回风险清单是“证据可核验”，不是“项目无风险” |

## 3. 修复（最小必要改动）

仅改 `bidcraft/generation/tool_loop.py`：

1. 新增 `_ERROR_KEYS` / `_FALSE_SUCCESS_KEYS` / `_FAILED_STATUSES` / `_TEXT_FAILURE_MARKERS` / `_TEXT_ONLY_KEYS`。
2. 新增 `_text_has_failure()`：英文失败词按词边界匹配，避免 `no errors` 误报；中文按子串匹配。
3. 新增 `_explicit_failure()`：识别显式错误字段、`ok/success=false`、`status=failed/blocked/...`、内容文本中的失败词。
4. 新增 `_meaningful_evidence()`：只有结构化字段才算可验证；`text/message/warning/content` 等自由文本不算。
5. 重写 `_verification()`：显式业务失败→`failed`；有结构化证据→`verified`；其余→`unverified`。docstring 明确 `verified` 只表示工具证据可核验，不代表业务结论通过。
6. 严格化 `_check_required()`：必需工具必须至少一次 `verified`，且不能有同名 `failed` 轨迹；失败和未验证分别报错。

不改 `TRACE_KEYS`、不改 API、不改 DB、不改 Worker 状态机；既有真实 MCP 工具调用与审计路径完全保留。

## 4. 测试

新增 `tests/test_v0330_tool_validation.py`（13 参数化场景 + 2 个必需工具严格性测试）：

- 结构化项目/风险清单/检索片段 = `verified`；
- 纯文本、只有 message 的 dict = `unverified`；
- `ok=false` / `success=false` / `status=failed` / 内容失败词 = `failed`；
- 纯文本和不可验证结果无法满足 `required_tools`；
- `run_check` 风格的风险清单仍 `verified`，证明“证据可核验≠业务通过”；
- 同名工具出现失败轨迹后，即使后来成功也拒绝。

执行：

```bash
.venv/bin/python -m ruff check bidcraft/generation/tool_loop.py tests/test_v0330_tool_validation.py
.venv/bin/python -m pytest -o addopts="" -q tests/test_v0330_tool_validation.py tests/test_tool_loop.py
```

实际结果：`All checks passed!`；`26 passed in 3.68s`。

## 5. 影响范围与残余风险

- 影响：仅 Agent 工具循环验证语义；对已有 `get_project` / `get_ledger` / `search_knowledge` 真实工具回归无破坏。
- 残余：插件若只返回自由文本，将被视为 `unverified`，不能满足 `required_tools`；这是刻意保守，避免文本误判成功。
- 未在 RC-01 处理：Tool Trace 参数/结果脱敏、执行预算超时、幂等与重复执行，归 RC-02。

## 6. 回滚

删除 `tests/test_v0330_tool_validation.py`，将 `bidcraft/generation/tool_loop.py` 回退到本模块修改前版本；无 migration、无数据变更。
