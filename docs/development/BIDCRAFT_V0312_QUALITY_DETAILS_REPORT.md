# v0.31.2 阶段 6：质量指标 sample / coverage / mismatch 明细报告

- 分支：`fix/v0.31.2-concurrency`（阶段 0～5 已提交，HEAD `886c760`）
- 目标：补齐 V0312-06「质量指标缺少 sample/coverage/mismatch 明细」；只增加可审计明细，不改变任何既有比率口径或业务状态。
- 结论：**已完成并通过验证**。

## 1. 项目级指标明细（`bidcraft/evaluation/quality_metrics.py`）

返回值由 4 键扩展为 7 键，旧 4 键含义与数值完全不变：

| 键 | 内容 |
| --- | --- |
| `coverage` | 每个指标的 `numerator` / `denominator` / `sample_count`；`manual_edit_rate`、`boq_match_rate` 另含 `ratio`，`issue_density` 含 `ratio_per_1000` 与 `unit` |
| `samples` | 每个指标最多 20 条具体样本：章节 id/标题/revision/是否人工修改；finding id/级别/状态/标题；BOQ 快照 id/表/行/名称/状态/是否匹配 |
| `mismatches` | 未处置 severe finding 明细 + 未匹配 BOQ 行明细（含 `metric` 与 `kind` 前缀） |

样本列表只用于人工抽查；分子/分母和比率始终由全量数据计算，`MAX_SAMPLES=20` 不影响指标值。分母为 0、无 BOQ 快照、DB 不可用等降级路径仍返回全键结构（空 `coverage`/`samples`/`mismatches`）且不抛异常。

## 2. 真实件指标明细（`bidcraft/evaluation/tender_quality.py`）

### 单文件

每份真实招标文件新增：

- `coverage.clauses / coverage.values / coverage.verdict`：命中数、标注总数、比率；verdict 另含 compared/agreed/annotated。
- `samples.matched_clauses / matched_values / verdict_checks`：可抽查样本（条款最多 120 字）。
- `mismatches`：`verdict`（期望 vs 实测）、`clause_not_extracted`、`value_not_parsed` 三类具体明细。
- 原 `notes` 中的判定不一致中文摘要保留，用于日志/UI 快速阅读。

### 汇总

`collect_tender_quality()` 汇总新增：

- `coverage`：三项宏平均 `macro_ratio`，以及跨文件 `covered/total/files/compared/agreed/annotated` 计数。
- `samples.files`：前 5 个文件的单文件样本。
- `mismatches`：合并所有文件明细并加 `file` 字段。
- 数据集未就绪时返回空明细容器，不伪造测量值。

## 3. 报告摘要集成

`build_quality_report()` 仍把 `summary['metrics']` 和 `summary['tender_quality']` 整体写入 JSON 列。新明细随两个对象一起持久化；旧报告无这些键时 `report_payload()` 照常渲染，不需要迁移。

## 4. 测试调整（未删测试、未降低断言）

1. `tests/test_v030_stage6_metrics.py`：`METRIC_KEYS` 从 4 键更新为 7 键，原精确比率断言（0.5、5/209×1000、0.75）保留。
2. `tests/test_batch7_quality.py::test_evaluation_job_retry_reuses_report`：阶段 4 给所有 worker 加了 `if job.status != 'running': return`，该用例却把首轮 Job 建成 `queued`，导致 worker 直接返回、从未生成报告。改为 `status='running'`（更贴近真实认领态），断言不变。
3. 新增 `tests/test_v0312_quality_details.py`（4 个用例）：项目级 coverage/samples/mismatches 精确计数、真实件判定不一致与未抽取/未解析明细、未测量时空容器、报告摘要持久化。

## 5. 验证命令与结果

```bash
.venv/bin/python -m ruff check bidcraft tests                 # All checks passed
.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_v0312_quality_details.py tests/test_v030_stage6_metrics.py \
  tests/test_tender_quality_v031.py tests/test_batch7_quality.py
# 23 passed, 1 warning

.venv/bin/python -m pytest -o addopts="" -q \
  tests/test_evaluation_phase3.py tests/test_batch7_evaluation.py \
  tests/test_batch7_quality.py tests/test_v030_stage6_metrics.py \
  tests/test_v030_stage6_verdict.py tests/test_tender_quality_v031.py \
  tests/test_v0312_quality_details.py tests/test_api_package.py
# 51 passed, 1 warning
```

全量后端回归将在阶段 7 最终验收统一执行。

## 6. 兼容性

- 无 API 路径/路由数量变化；无 migration、模型字段变化。
- 既有前端 QualityTab 读取的数值键不变；新明细对象对旧前端透明，可由后续迭代决定是否展示。
- `verdict_agreement` 仍在没有应答文本标注时为 `None`（未测量），不误报 0。

## 7. 残余风险

- 真实招标文件仍未由用户提供；本阶段只验证明细结构，不声称真实指标已测量。
- 样本上限 20 条；全量列表可通过直接调用收集函数获取，报告摘要只保留截断样本，避免报告 JSON 过大。
- `issue_density` 的 mismatch 列出的是未处置 severe finding，不代表人工复核后的误报判定。

## 8. 回滚

删除 `tests/test_v0312_quality_details.py` 与本报告；把 `bidcraft/evaluation/quality_metrics.py` / `tender_quality.py` 新增键移除、`tests/test_v030_stage6_metrics.py` 的 `METRIC_KEYS` 改回 4 键、`tests/test_batch7_quality.py` 首轮 Job 状态改回 `queued`。无数据迁移可回滚。
