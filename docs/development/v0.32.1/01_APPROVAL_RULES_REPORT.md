# v0.32.1 审批链规则命中条件 — 实施报告

> 分支：`feature/v0.32-ai-engine`（本地 commit，等待当前 CI 绿后按循环 push）
> 基线：v0.32 最终验收提交 `4f649ce`
> 范围：报价审批链的可选规则命中条件，不改历史迁移、不加新表、不改 API 路由数。

## 1. 背景

`project.meta["approval_chain"]` 已支持多级会签/或签、`min_amount`、`packages`、`currencies`。本次补齐路线图中「规则命中条件」：允许每个审批阶段附加最多 20 条 AND 规则，按版本金额、包件、币种决定是否生效。

## 2. 实现

生产代码仅改 `bidcraft/boq.py`：

- 新增常量：`STAGE_RULE_MAX_ITEMS=20`、`STAGE_RULE_VALUE_MAX_ITEMS=20`、`STAGE_RULE_STRING_MAX=40`；字段白名单 `total_amount/package_id/currency`；操作符白名单 `gte/lte/eq/ne/in/not_in/contains`。
- `_stage_rules()` 归一化规则：非法字段/操作符、布尔/非有限数、空列表、超长字符串、非 dict 条目一律丢弃；有效规则去重保序；无有效规则时阶段保持无条件生效。
- `_rule_value_matches()` 求值：金额只接受有限数；币种统一大写比较；包件/字符串按原值比较；未知上下文一律「满足」以保持原有 over-review 保守口径。
- `_attach_stage_conditions()` 将有效 `rules` 写入归一化阶段；`_stage_applies()` 在原有金额/包件/币种条件之后 AND 求值规则。
- 旧链（无 `rules`）行为完全不变；空阶段过滤后的全链回退逻辑保持原样。

数据无需迁移：审批链本身存于 `Project.meta` JSON，规则是阶段字典的可选键。API schema 已为 `list[dict[str, Any]]`，路由数保持 274，schema 数保持 93。

## 3. 测试

新增 `tests/test_boq_approval_rules_v032.py`（7 个纯函数测试）：

1. 有效规则保留、非法规则丢弃；
2. 金额 `gte` 过滤阶段并保留全链回退；
3. `lte` + 包件 + 币种 AND 求值；
4. `in` / `not_in` / `contains` 求值；
5. 未知上下文保守放行；
6. `completed_stages` 仍按原始链索引计数、按生效级返回序号；
7. 旧链无 `rules` 时行为不变。

验证命令与结果：

- `ruff check bidcraft/boq.py tests/test_boq_approval_rules_v032.py` → All checks passed。
- `pytest -q tests/test_boq_approval_rules_v032.py tests/test_boq.py tests/test_commercial_approval.py tests/test_quote_approval.py tests/test_quote_approval_chain.py tests/test_quote_approval_condition.py tests/test_quote_approval_conditions.py tests/test_quote_approval_parallel.py tests/test_approvals_pending.py tests/test_batch7_approvals.py tests/test_api_package.py tests/test_api.py` → **122 passed, 1 warning**（仅 Starlette/httpx deprecation）。

## 4. 兼容与风险

- 兼容：旧审批链、金额门槛、包件/币种白名单、审批记录 stage 原始索引语义均未改变。
- 风险：规则目前在 `Project.meta` 中直接配置，尚未做专用管理端表单；可通过 API/CLI 写入 meta 使用。
- 风险：未知上下文按「规则满足」处理，可能多要一级审批，不会漏审批；这是刻意保守口径。

## 5. 回滚

只回退 `bidcraft/boq.py` 本次规则相关 hunk，删除 `tests/test_boq_approval_rules_v032.py` 与本报告，并将 `docs/roadmap.md` 第 2 项恢复原文。无数据库状态需要回滚。
