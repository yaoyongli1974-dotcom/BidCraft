# v0.32.1 审批链规则命中条件 — 实施报告

> 分支：`feature/v0.32-ai-engine`（本地 commit，等待当前 CI 绿后按循环 push）
> 基线：v0.32 最终验收提交 `4f649ce`
> 范围：报价审批链的可选规则命中条件，不改历史迁移、不加新表、不改 API 路由数。

## 1. 背景

`project.meta["approval_chain"]` 已支持多级会签/或签、`min_amount`、`packages`、`currencies`。本次补齐路线图中「规则命中条件」：允许每个审批阶段附加最多 20 条 AND 规则，按报价版本上下文决定是否生效。字段分为字符串字段 `package_id`/`currency` 与数值字段 `total_amount`/`boq_rows`/`priced`/`unpriced`/`match_rate`；后四项来自报价版本 `summary`，仅在 BOQ 报价审批链路传入，商务标审批保持旧三字段上下文。

## 2. 实现

生产代码仅改 `bidcraft/boq.py`：

- 新增常量：`STAGE_RULE_MAX_ITEMS=20`、`STAGE_RULE_VALUE_MAX_ITEMS=20`、`STAGE_RULE_STRING_MAX=40`、`STAGE_RULE_NUMERIC_FIELDS` / `STAGE_RULE_STRING_FIELDS`；操作符白名单 `gte/lte/eq/ne/in/not_in/contains`。
- `_stage_rules()` 归一化规则：非法字段/操作符、布尔/非有限数、空列表、超长字符串、非 dict 条目一律丢弃；有效规则去重保序；无有效规则时阶段保持无条件生效。
- `_rule_value_matches()` 求值：数值字段只接受有限数（`in`/`not_in` 仍按字符串列表）；币种统一大写比较；包件/字符串按原值比较；未知上下文一律「满足」以保持原有 over-review 保守口径。
- `_attach_stage_conditions()` 将有效 `rules` 写入归一化阶段；`_stage_applies()` 在原有金额/包件/币种条件之后 AND 求值规则；`applicable_stages()` / `completed_stages()` 接受可选 `summary` 并向后转发。
- BOQ 的 `version_payload`、`review_version`、`pending_for_user` 均传入版本 `summary`；`commercial.py` 审批上下文不传，保持三字段旧语义。
- 旧链（无 `rules`）行为完全不变；空阶段过滤后的全链回退逻辑保持原样。

数据无需迁移：审批链本身存于 `Project.meta` JSON，规则是阶段字典的可选键。API schema 已为 `list[dict[str, Any]]`，路由数保持 274，schema 数保持 93。

## 3. 测试

新增 `tests/test_boq_approval_rules_v032.py`（9 个纯函数测试）：

1. 有效规则保留、非法规则丢弃；
2. 金额 `gte` 过滤阶段并保留全链回退；
3. `lte` + 包件 + 币种 AND 求值；
4. `in` / `not_in` / `contains` 求值；
5. 未知上下文保守放行；
6. `completed_stages` 仍按原始链索引计数、按生效级返回序号；
7. 旧链无 `rules` 时行为不变；
8. 报价摘要 `boq_rows/priced/unpriced/match_rate` 数值规则 AND 过滤；
9. 摘要缺失或部分缺失时数值规则保守命中。

验证命令与结果：

- `ruff check bidcraft tests scripts` → All checks passed；`git diff --check` clean。
- `pytest -q tests/test_boq_approval_rules_v032.py tests/test_boq.py` → **30 passed**（新增数值规则两段已覆盖）。
- 前一段（仅三字段规则）曾跑过 122 passed / 1 warning；本段数值规则改动后以 30 passed 为准，最终全量回归由后续 CI/本地全量确认。

## 3.1 前端配置面板（第二段本地提交）

前端配置面板已随 `37d5ac0` 推送；本段新增数值字段后重新本地验证，待当前 CI 绿后推送：

- `web/src/lib/types.ts`：`ApprovalRule` / `ApprovalRuleField` / `ApprovalRuleOp`，`ApprovalStage` 与 `ApprovalStageInput` 增加可选 `rules`。
- `web/src/api/project.ts`：`normalizeApprovalRules()` 与后端白名单同口径（最多 20 条、每条 value 最多 20 项 / 40 字，非法项丢弃）；`normalizeApprovalChain()` 回显保留 rules；`applicableStages()` 在前端按同一 AND 规则计算生效级，未知上下文保守命中。
- `web/src/pages/tabs/BoqVersions.tsx`：审批链弹窗每级新增「规则条件」区，支持字段 / 操作符 / 值输入、多条规则胶囊、点击移除、保存时随 `approval_chain` 一并 PATCH；提示文案同步。
- `web/src/lib/api.test.ts`：新增归一化、in/去重、币种大写、金额过滤、AND 过滤与未知上下文保守命中测试。

验证：`npm run typecheck`、`npm run lint`、`npm test -- --run` → 57 files / 433 tests 全通过（新增数值规则测试；ErrorBoundary 用例打印预期 stack trace）。

## 3.2 真实 DB 集成验证（第三段本地提交）

新增 `tests/test_boq_approval_rules_integration_v032.py`（3 个真实 DB 测试）：在项目 `meta.approval_chain` 配置「规则级 + 兜底」两级审批后，验证

- `version_payload()` 按 `version.summary` 中的 `priced` 值给出 `approval_stage_count=1`；
- `review_version()` 在规则级被过滤后由兜底审批人通过，approve 记录仍保留原链索引 `1`，且规则未命中时非当前级审批人得到 `BoqForbiddenError`；
- `pending_for_user()` 对命中/未命中规则的报价分别把当前审批人落到规则级或兜底级，`stage`/`stage_count` 与规则过滤结果一致。

验证：`pytest -q tests/test_boq_approval_rules_v032.py tests/test_boq.py tests/test_approvals_pending.py tests/test_batch5_commercial.py tests/test_commercial_approval.py` → **65 passed, 1 warning**；ruff、`git diff --check` 均通过。

## 3.3 用户配置路径回显（第四段本地提交）

新增 `tests/test_boq_approval_rules_api_v032.py`，用真实 `TestClient` 走 `PATCH /projects/{ref}` → `GET /projects/{ref}` 验证：

- 审批链阶段中合法的 `boq_rows/priced/currency` 规则被 `boq._stage_rules()` 归一化后写入 `project.meta.approval_chain` 的 `stage_snapshot` 并原样回显；
- 非法字段、非法操作符、布尔数值被裁剪，不静默保留；
- 审批人 id 列表按接口约定以字符串数组提交，显示名仍由服务端从用户记录补全。

`PATCH /projects/{ref}` 现通过 `..shared` 已有的 `boq` 重导出调用 `boq._stage_rules()`，无新增依赖或路由。

验证：`pytest -q tests/test_boq_approval_rules_api_v032.py tests/test_boq_approval_rules_integration_v032.py tests/test_boq_approval_rules_v032.py tests/test_quote_approval_conditions.py tests/test_api_package.py` → **34 passed, 1 warning**；ruff、`git diff --check` 均通过。

## 4. 兼容与风险

- 兼容：旧审批链、金额门槛、包件/币种白名单、审批记录 stage 原始索引语义均未改变。
- 风险：未知上下文按「规则满足」处理，可能多要一级审批，不会漏审批；这是刻意保守口径。

## 5. 回滚

只回退 `bidcraft/boq.py` 本次规则相关 hunk，删除 `tests/test_boq_approval_rules_v032.py` 与本报告，并将 `docs/roadmap.md` 第 2 项恢复原文。无数据库状态需要回滚。
