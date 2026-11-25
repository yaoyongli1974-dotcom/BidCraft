# v0.31.3 阶段 4：质量指标与真实件口径报告

- 分支：`fix/v0.31.3-production-readiness`（阶段 0～3 已提交，HEAD `f8f9469`）
- 目标：生产验收口径不得把合成夹具当真实招标文件，不得在无样本/无应答时伪造指标；项目级指标必须在真实 PostgreSQL 数据上按既定分母计算。
- 结论：**A PASS**。无生产代码改动，只补真实 PG 回归证据。

## 1. 验证事实

| 口径 | 验证 |
|---|---|
| 数据集缺失 | `collect_tender_quality(tmp_path)` 返回 `status=未测量`，三项比率均为 `None`，`coverage`/`samples` 为空，notes 明示“数据集清单不存在” |
| 合成样本 | manifest 含 `SYNTHETIC` 文件头标记时 `dataset_status().ready=False`，原因包含“合成”；不会被提升为真实样本 |
| 有标注数据集 | `required_clauses` / `required_values` / `expected_verdicts` + `responses` 齐备时才计算；本阶段 PG 回归得到 clause_recall=1.0、value_parse_rate=1.0、verdict_agreement=1.0，mismatches 为空 |
| 无应答文本 | 既有契约保持 `verdict_agreement=None` 并说明“未测量”，不按 0 计入 |
| 项目级指标 | 真实 PG 数据中 2 节有正文、1 节修订>1 → manual_edit_rate=0.5；4 词 + 1 条未处置 major → issue_density=250/千词；2 条 BOQ、1 条匹配 → boq_match_rate=0.5 |

项目级指标的 `coverage` 分子/分母与 `samples`/`mismatches` 均随结果返回；未处置 finding 与未匹配 BOQ 行可在明细中抽查。样本截断不影响比率。

## 2. 新增测试

`tests/postgres/test_v0313_quality_metrics.py`（4 项，真实 PG 16.2 + 临时数据集目录）：

1. `test_project_metrics_on_postgres_use_documented_denominators`
2. `test_unmeasured_dataset_is_explicit_and_never_fabricated`
3. `test_synthetic_marker_cannot_be_promoted_to_real_sample`
4. `test_annotated_dataset_measures_clauses_values_and_verdict`

第 4 项仅把文档解析/抽取替换为可控 stub（`load_document` / `extract`），不 mock 判定引擎与指标计算；第 1 项使用真实 `boq.align()` 与真实 `QualityFinding` / `QualityReport` / `Section` / `OutlineNode` 行。

## 3. 验证命令与结果

```bash
.venv/bin/python -m ruff check bidcraft tests
BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/v0313_pg_uri.txt)" \
  .venv/bin/python -m pytest -o addopts="" -q tests/postgres/test_v0313_quality_metrics.py
# 4 passed in 1.96s
```

## 4. 残余风险

- 真实脱敏招标文件仍必须由用户提供；本阶段只验证契约与计算路径，不代表真实召回率/一致率已被测量。
- 本地 pgserver 是真实 PostgreSQL 16.2，但不是生产实例；CI 生产型 PG 集成仍由阶段 6 复核。
- `issue_density` 仍统计项目全部报告的 finding（含当前报告），该口径记录在 notes 中，未在本阶段变更。

## 5. 回滚

仅删除本阶段新增测试与报告；无生产代码、schema 或配置变化。
