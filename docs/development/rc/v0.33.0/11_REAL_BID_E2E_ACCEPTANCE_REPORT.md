# RC-10 真实/脱敏投标 E2E 验收报告（含 RC-11 过程指标与 summary 契约）

分支：`fix/v0.33.0-rc-reliability`，HEAD `be4f8b9`。本地实现，未 commit/push、未打 tag、
未发版、未部署、未接生产数据。所有改动仅限本批次 ownership 文件。

## 1. RC-10：数据集门禁与真实件 E2E

### 1.1 数据与阻塞状态

- 默认数据集根：`tests/fixtures/tender_real`（由 `bidcraft.evaluation.tender_dataset.DEFAULT_ROOT`
  提供，测试代码不硬编码）。用户可通过 `BIDCRAFT_REAL_TENDER_ROOT` 注入任意位置。
- 当前状态：**DATA BLOCKED（skip）**。真实/脱敏招标文件及其 `dataset.json` 未提供：
  `dataset_status()` 返回 `ready=False`，原因 `真实招标文件数据集清单不存在：tests/fixtures/tender_real/dataset.json`。
- 因此 `tests/test_v0330_dataset_gate.py::test_real_tender_dataset_gated_e2e` 以显式
  `DATA BLOCKED: 未提供真实/脱敏招标文件及人工标注，跳过真实件 E2E` 跳过；**没有**用
  `tests/fixtures/tender_cabling.txt` 或其它合成夹具冒充真实样本。CI 没有 `skip==0` 断言。

### 1.2 代码改动

- `bidcraft/evaluation/tender_dataset.py`
  - `_has_synthetic_marker()`：按 64 KiB 分块扫描**整个文件**（块间保留 64 字节重叠，
    防止中文标记/多字节字符跨块漏检），替换原来只看前 4096 字节的逻辑。
  - `load_dataset()`：每个条目必须显式 `source == 'user-provided'`，否则 `ValueError`；
    归一化条目保留经校验的 source。
  - `dataset_status()` 仍不抛异常，把新的校验错误纳入 `reason`。
- `tests/test_tender_dataset_v031.py`：`_entry()` 支持 `marker_offset` / `source`；新增
  「标记在 4096 字节之后」「非 user-provided 来源（含空值）」两组拒绝用例。
- `tests/conftest.py`：新增 `real_tender_root` fixture，只从 `BIDCRAFT_REAL_TENDER_ROOT`
  读取，缺省回退模块 `DEFAULT_ROOT`。
- `tests/test_v0330_dataset_gate.py`（NEW）：`dataset_status()` 未就绪即 skip；就绪时按
  `dataset.json` 逐文件跑 `add_source→parse_project→build_outline/save_outline→
  generate_document→check_project→export_docx/export_markdown`，并回检
  `required_clauses`（解析条款/生成正文）、`required_values`（台账渲染文本）、
  `expected_verdicts`（有 `responses` 时用 `verdict.judge` 复算并逐条断言一致；
  无应答即视为未测量，不伪造通过）。`source.checksum` 必须与清单 sha256 一致。
- `.github/workflows/ci.yml`：仅在 `python-tests` job 增加非门禁步骤
  `Real tender dataset status (non-gating)`，打印 `READY (run): N ...` 或
  `DATA BLOCKED (skip): <reason>`；其它 job 与断言均未改动，无 skip 数量门禁。

### 1.3 验证结果

```
.venv/bin/python -m ruff check bidcraft/evaluation/tender_dataset.py ... (全部 owned Python)
All checks passed!

.venv/bin/python -m pytest tests/test_tender_dataset_v031.py tests/test_v0330_dataset_gate.py \
  tests/test_e2e.py tests/test_v030_stage6_metrics.py tests/test_v032_quality_api.py
26 passed, 1 skipped, 1 warning in 12.73s
# 唯一 skip 为 DATA BLOCKED 的真实件 E2E；warning 为 starlette httpx 弃用提示

# CI 步骤本地等价执行（未注入 root）
DATA BLOCKED (skip): 真实招标文件数据集清单不存在：tests/fixtures/tender_real/dataset.json

# 临时 ready 清单（不入库、不计入真实验收）验证 CI READY 分支
READY (run): 1 annotated real/redacted tender file(s)

# 临时 ready 清单验证 E2E READY 分支
BIDCRAFT_REAL_TENDER_ROOT=<临时目录> .venv/bin/python -m pytest tests/test_v0330_dataset_gate.py
1 passed in 1.90s
# 通过后才清理临时目录；标注不准确的条款/判定会真实失败（验证过失败路径）
```

## 2. RC-11：过程指标与 summary 契约

### 2.1 后端

- `bidcraft/evaluation/quality_metrics.py`
  - 新增 `measured` 映射（`manual_edit_rate` / `issue_density` / `boq_match_rate`），
    分母 ≤ 0 时为 `False`，其余比率键、coverage/samples/mismatches/notes 原样保留。
  - 采集器异常时只把该采集器负责的指标回退为 `measured=False`（即使异常发生在设置
    标记之后），比率保留 0.0 兼容旧调用方。
  - `RATIO_KEYS` / `_COLLECTOR_METRICS` 固定口径；文档字符串同步说明未测量契约。
- `bidcraft/api/routes/evaluation.py`
  - `_render_quality_markdown()`：`summary.metrics.measured[key] is False` 时渲染
    `未测量`，旧报告无 `measured` 字段仍渲染原始比率（历史快照不回改）。
  - `evaluation_summary()`：每条含 `project_id` / `ref` / `name` / `report_id` /
    `report_version` / `overall_score` / `grade` / `verdict` / `created_at` / `measured`；
    请求过的可访问项目即使无报告也返回 `measured=False` 占位（分数/等级/结论/时间
    一律 null），无权访问/不存在的 ref 仍由授权层过滤。
- `tests/test_v030_stage6_metrics.py`：新增 measured 精确分母/零分母/采集失败断言；
  `tests/test_v032_quality_api.py`：summary 全字段与未测量占位、Markdown `未测量` 断言。

### 2.2 前端

- `web/src/lib/types.ts`：`metrics.measured?: Record<string, boolean>`；修正「无样本时
  后端写 0」的过时注释，说明 0.0 仅为兼容值；更新概览 `measured=false` 注释。
- `web/src/api/evaluation.ts`：`asMetricMeasured()` 逐指标读取后端标记，未测量比率归一为
  `null`（界面显示「未测量」），旧报告缺字段按已测量处理；
  `normalizeEvaluationSummaryItem()` 只有后端显式 `measured=true` 且有 report_id 才算
  已测量，缺省一律 false。
- `web/src/pages/tabs/QualityTab.tsx`：StatTile 与 `MetricDetails` 同时尊重 `measured`
  标记（双层防线）；判定核对样本 `{clause, expected, actual, agree}` 渲染为
  「判定：期望 X / 实测 Y / 一致|不一致」而不是原始 JSON。
- `web/src/api/boq.ts`：`normalizeBoqQuoteVersion()` 用 `normalizeApprovalExplain()` 透传
  后端 `approval_explain`（含 create 回退对象）；`BoqVersions.tsx` 既有逻辑已优先取
  `version.approval_explain`，本次不改该文件。
- 测试：`web/src/api/evaluation.test.ts`（零分母未测量、summary 缺省未测量、身份字段）、
  `web/src/api/boq.test.ts`（NEW，后端 explain payload 透传/坏条目丢弃/旧后端空数组）、
  `web/src/api/outboundContract.test.ts`（summary 身份字段与缺省未测量）、
  `web/src/pages/tabs/QualityTab.evidence.test.tsx`（0.0 不显示、判定渲染）。

### 2.3 验证结果

```
.venv/bin/python -m ruff check <owned python>          -> All checks passed!
.venv/bin/python -m pytest <5 touched backend files>   -> 26 passed, 1 skipped (DATA BLOCKED)
web: npm run typecheck                                  -> 通过（无输出错误）
web: npm run lint                                       -> 通过（无输出错误）
web: npx vitest run <4 targeted files>                  -> 4 files / 45 tests passed
web: npm test -- --run                                  -> 58 files / 451 tests passed
CI YAML: yaml.safe_load(.github/workflows/ci.yml)      -> OK，non-gating step 存在（13 行 run）
```

未运行完整后端套件（按要求）；未运行 build/deploy/prod 相关流程。

## 3. 残余风险与待办

1. 真实/脱敏数据集未提供：RC-10 的 READY 路径只通过临时本地清单做过冒烟验证，
   **不代表真实投标验收通过**；最终结论须在用户提供数据后重跑该用例。
2. 数据集标注必须诚实：`required_clauses` 应为解析器可抽取的原文片段。临时冒烟验证过
   标注不准时用例真实失败（条款未识别 / 判定不一致），这是预期门禁行为。
3. `approval_explain` 的普通条件链（仅 min_amount/packages/currencies）在 RC-09 报告中
   仍为待办；本次只保证后端字段被前端归一化器保留，不改变解释生成规则。
4. 旧报告快照没有 `measured` 字段时按已测量处理；若历史报告把零分母 0.0 当作实测值，
   该口径保持不变（不回改历史快照）。
5. `docs/development/rc/v0.33.0/00_RC_MASTER_TASKBOOK.md` 中 RC-08/09 提交号的变化不是
   本批次编辑；本 agent 未修改该文件（严格遵守 ownership）。

## 4. 回滚

按文件独立 revert 即可，无 schema 迁移、无生产状态变更：

- 后端：`git checkout -- bidcraft/evaluation/tender_dataset.py bidcraft/evaluation/quality_metrics.py bidcraft/api/routes/evaluation.py tests/conftest.py tests/test_tender_dataset_v031.py tests/test_v030_stage6_metrics.py tests/test_v032_quality_api.py .github/workflows/ci.yml docs/evaluation.md`，
  并删除 `tests/test_v0330_dataset_gate.py`。
- 前端：`git checkout -- web/src/api/evaluation.ts web/src/api/evaluation.test.ts web/src/api/boq.ts web/src/lib/types.ts web/src/pages/tabs/QualityTab.tsx web/src/pages/tabs/QualityTab.evidence.test.tsx web/src/api/outboundContract.test.ts`，
  并删除 `web/src/api/boq.test.ts`。
- 本报告删除即可：`docs/development/rc/v0.33.0/11_REAL_BID_E2E_ACCEPTANCE_REPORT.md`。
- 无需数据库回滚；已落库的历史报告快照不含 `measured` 字段时仍可被旧前端读取。
