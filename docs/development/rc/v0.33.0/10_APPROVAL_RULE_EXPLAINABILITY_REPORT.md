# RC-09 审批规则可解释性报告

## 1. 范围与基线

- 基线分支：`fix/v0.33.0-rc-reliability`，HEAD `591891c`。
- 本模块仅实施 RC-09；未触碰 `evaluation/retrieval_metrics.py`、`quality_calibration.py`、`evaluation/__init__.py`、`evaluation/report.py`、`quality_metrics.py`、`api/routes/evaluation.py`、`tender_dataset.py`（其他代理所有）。
- 审计基线：`boq.py` 的规则常量/归一化（`:738–823`）、保守求值 `:826–845`、`_stage_applies :913–960`、`applicable/completed :963–1041`、`version_payload :1067–1119`、`pending :1450–1505`；商务标三处复用 `:1239–1251 / :1313–1319 / :1555–1560`。

## 2. 问题与契约变更

1. 生效判定藏在 `_stage_applies` 的多个早退分支里，前端只能本地镜像 `applicableStages`，无法知道“哪条规则没命中、哪个上下文字段缺失”。
2. 商务标审批复用 BOQ 的 `applicable_stages` / `completed_stages` 时没有传 `summary`，导致 `priced ≥ N` 等报价摘要规则在商务标链路上永远按“未知 → 命中”放行；且落库 `validation.quote` 原本不带这些指标。
3. Web `BoqQuoteVersion` / `PendingApproval` 类型没有解释字段，版本面板无法展示命中明细。

契约变化（只新增）：

- 新纯函数 `_stage_explain(stage, total, package, currency, *, amount_known, summary=None) -> {"applicable", "conditions", "missing_data"}`；每条 condition 恰好 `field/op/value/actual/hit/missing` 六键。
- `version_payload` 新增 `approval_explain`：与原链同序的逐阶段解释列表（每项含 `index`）。
- 含 `rules` 的审批链在 BOQ / 商务标的 pending item 上新增 `approval_explain`（当前待审级的解释）。仅 `min_amount`/`packages`/`currencies` 的链与空链保持旧 `PENDING_KEYS` 形状不变。
- `validation.quote` 落库新增 `boq_rows`/`priced`/`unpriced`/`match_rate`（仅已审批版本暴露数值）。

## 3. 测试先行

先新增 `tests/test_v0330_approval_explain.py`（8 用例）并在 `tests/test_commercial_approval.py` 追加 `priced ≥ N` 状态机用例：

- 全 7 操作符矩阵（gte/lte/eq/ne/in/not_in/contains）与逐条 `hit/missing`；
- 未知数值保守命中且列入 `missing_data`；
- 金额门槛 / 包件 / 币种条件、AND、无条件阶段；
- monkeypatch `_stage_explain` 验证 `_stage_applies` 确实委托它（单一来源）；
- `version_payload` 带真实 `actual=7, hit=False` 与缺失数据；
- BOQ / 商务标 pending 带真实解释；
- 商务标 `priced=7` 走兜底级、`priced=10` 走规则级且非本级审批人被拒。

首次运行：8 failed（缺 `_stage_explain`、`approval_explain`、商务标 summary 接线），精确复现缺口。

## 4. 生产实现

### 4.1 `bidcraft/boq.py`

- 新增 `_stage_explain`：所有条件（金额门槛、包件/币种白名单、rules）统一产出 condition 与 `missing_data`，`applicable = all(hit)`；未知金额/规则仍保守命中，白名单缺值仍跳过，行为与旧实现逐项一致。
- `_stage_applies` 缩为 `_stage_explain(...)["applicable"]`，过滤与解释不可能分叉。
- 新增 `_chain_explain` 辅助；`version_payload` 回传 `approval_explain`。
- `pending_for_user`：当前待审级解释随 item 返回；仅当链含 `rules` 时附带，兼容旧精确键契约。

### 4.2 `bidcraft/commercial.py`

- `_review_context`、`review_commercial` 的 final 判定、`pending_for_user` 三处全部改为 `summary=quote`，落库 `validation.quote` 真正参与规则求值。
- `_quote_reference` 把已审批版本的 `boq_rows`/`priced`/`unpriced`/`match_rate` 写进引用摘要；未审批版本不暴露。
- 商务标 pending item 在含规则链时回传 `approval_explain`。

### 4.3 Web

- `lib/types.ts`：新增 `ApprovalExplainCondition` / `StageApprovalExplain`；`BoqQuoteVersion.approval_explain`、`PendingApproval.approval_explain` 均为可选字段。
- `api/project.ts`：新增 `normalizeApprovalExplain`（后端回显防御性归一）与 `explainApprovalStages`（与 `applicableStages` 同源的本地镜像，兼容旧后端未回传字段的情况）。
- `pages/tabs/BoqVersions.tsx`：`ApprovalExplainPanel` 展示字段/操作符/期望值/实际值/命中与“数据缺失”，后端解释优先、本地镜像兜底。
- `styles/workspace.css`：解释面板样式（命中/未命中/缺失标记）。

## 5. 验证（实际执行）

```bash
.venv/bin/python -m pytest -q tests/test_v0330_approval_explain.py
# 8 passed

.venv/bin/python -m pytest -q tests/test_boq_approval_rules_v032.py \
  tests/test_boq_approval_rules_api_v032.py tests/test_boq_approval_rules_integration_v032.py \
  tests/test_commercial_approval.py tests/test_api_package.py tests/test_batch8_contract.py \
  tests/test_v030_stage8_observability.py tests/test_v032_fact_governance.py
# 50 passed

.venv/bin/python -m pytest -q tests/test_v0330_approval_explain.py \
  tests/test_approvals_pending.py tests/test_quote_approval_chain.py \
  tests/test_quote_approval_conditions.py tests/test_quote_approval_condition.py \
  tests/test_quote_approval_parallel.py
# 75 passed（含精确 PENDING_KEYS 契约）

.venv/bin/python -m ruff check <全部 owned Python 文件>
# All checks passed!

cd web && npm run typecheck            # 通过
cd web && npm run lint                  # 通过
cd web && npx vitest run src/lib/api.test.ts src/api/learning.test.ts \
  src/pages/tabs/QualityTab.lessons.test.tsx
# 137 passed
cd web && npm test -- --run             # 57 files / 442 passed
```

未运行全量后端套件（按任务约束）。

## 6. 兼容性与残余风险

- 无 schema 迁移；`approval_explain` 为附加字段，旧调用方忽略即可。
- `_stage_explain` 与旧 `_stage_applies` 的保守语义逐项对齐：未知金额/规则值命中，包件/币种白名单缺值跳过；既有规则回归全绿。
- pending item 的解释只在“含 rules 的链”上出现：因为两个既有用例对空链与仅 `min_amount` 链断言精确 `PENDING_KEYS`，本模块不修改非 owned 文件；若后续统一放开，需要同步更新那两个用例。
- Web 版本列表的 `approval_explain` 字段当前由本地镜像兜底：`web/src/api/boq.ts` / `shared.ts` 不在本模块 ownership 内，未在其归一化器中透传后端字段；渲染结果同源但多一次本地计算。后续由对应 owner 更新归一化器后会自动优先用后端值。
- 解释中的 `actual` 对未知值返回 `None`/空串并配 `missing=true`，UI 显示“未知/数据缺失”，不伪造 0 或默认币种。
- 解释仅覆盖审批链生效条件，不审计审批人身份或签名状态；后者仍由既有审批记录与 `approval_stage` 表达。

## 7. 回滚

代码层按需回退：`boq.py` 的 `_stage_explain`/`_chain_explain`、委托与两处 `approval_explain` 接线；`commercial.py` 的三处 `summary=quote` 与 `_quote_reference` 指标；Web 的解释类型、归一化/本地镜像、渲染与样式；删除 `tests/test_v0330_approval_explain.py` 与本报告，并移除 `test_commercial_approval.py` 的新用例。已落库的 `validation.quote` 多出的指标键可保留（旧代码忽略未知键），无需数据库回滚操作。
