# RC-06 检索基准报告

## 1. 范围与基线

- 分支：`fix/v0.33.0-rc-reliability`，基线 HEAD `591891c`。
- 目标：为知识检索增加**纯 IR 指标**（Recall/Precision/MRR/NDCG）与四项系统信号
  （来源完整、范围越权、无效资产、检索降级），并建立真实 qrels 数据集契约。
- 约束：仅本任务授权文件；不改 `web/src`、`quality_metrics.py`、评估路由、
  `learning.py`、`boq.py`、`commercial.py`、`projects.py`、既有测试；不提交/推送、
  不跑 `ruff format`、不跑全量套件。
- 审计事实：检索侧已有 `Passage` 文档/块级溯源与就绪/新鲜度信号
  （`knowledge.py`）、`rerank_hybrid()` 纯融合排序与 `search()` 诊断
  （`diagnostics.searched/skipped/failed`）；仓库中原本不存在 Recall/Precision/
  MRR/NDCG、qrels 或真实检索基准。

## 2. 实现

### 新增 `bidcraft/evaluation/retrieval_metrics.py`

纯函数，确定性数学，不查库、不调用检索、无 numpy/scipy 依赖：

| 函数 | 口径 |
| --- | --- |
| `recall_at_k(qrels, run, qid, k)` | 命中标注文档数 / 标注文档数；缺 qid、空 run、k<=0 → `0.0` |
| `precision_at_k(qrels, run, qid, k)` | top-k 命中数 / k；分母固定 k |
| `mrr(qrels, run, qid, *, k=10)` | 首个命中位次的倒数，截止 10 |
| `ndcg_at_k(qrels, run, qid, k)` | 二值相关性 DCG/IDCG；等增益文档增益相同 |
| `evaluate_benchmark(data)` | 按查询宏平均聚合 IR 指标 + 逐 passage/查询信号比率 |
| `load_benchmark(root)` | 真实清单契约校验：`schema=1`、`synthetic` 与合成标记拒绝、类型/结构校验 |
| `retrieval_status(root)` | 机器可读就绪状态，缺数据不抛异常 |
| `collect_retrieval_metrics(root)` | 汇总：未就绪时 `status='未测量'`、`measured=False`、九项指标全 `None` |

**平局/重复处理**：`_ranked()` 按首次出现去重，重复文档 ID 不放大命中数或
DCG；并列相关性只用标注集合的规模决定 IDCG，指标恒不超过 1。

**四项系统信号**（取自真实检索 run 的 passage 与诊断字段，零分母 → `None`）：

- `source_completeness`：`document_id`/`page`/`heading`/`chunk_id` 四要素齐全比例；
- `scope_violation`：`scope_ok` 非真比例（失败关闭，缺失标记算违规）；
- `invalid_asset_rate`：`valid_asset` 非真比例；
- `degraded_retrieval_rate`：`searched==0` 或 `failed>0` 的查询比例。

### 报告摘要注入

`bidcraft/evaluation/report.py::build_quality_report()` 在原有
`metrics` / `tender_quality` 注入处追加 `retrieval_benchmark` 与 `calibration`
两个键，整体替换 `report.summary` 以触发 JSON 变更追踪；既有键（`coverage`、
`recommendations`、`note`、`evaluation_job_id`、`commercial`、`metrics`、
`tender_quality`）与 `report_payload()` 结构完全保留。

### 夹具与导出

- `tests/fixtures/retrieval_benchmark.json`：`synthetic=true` 的算法自检夹具，
  `load_benchmark()` 明确拒绝其作为真实基准，`evaluate_benchmark()` 可用于手算值自检。
- `tests/fixtures/retrieval_real/README.md`、`dataset.example.json`：真实数据放置
  步骤、字段口径与脱敏要求；仓库内**没有** `dataset.json`。
- `bidcraft/evaluation/__init__.py`：新增 9 个公共符号导出，`__all__` 保持排序
  （RUF022）。

## 3. 测试与验证

新增 `tests/test_v0330_retrieval_metrics.py`（10 项，含手算值、重复 ID 去重、
缺 qid/空 run/零 k、宏平均精确值、未测量全 None、空清单未就绪、合成夹具拒绝、
真实数据集缺席、导出排序、报告摘要注入与 `report_payload` 结构不变）。

实际命令与结果（均在 `/home/yao/project`，仓库 `.venv`）：

```text
# 测试优先：实现前先运行两个新测试文件（预期红）
.venv/bin/python -m pytest tests/test_v0330_retrieval_metrics.py \
  tests/test_v0330_quality_calibration.py --disable-warnings --tb=line
2 errors during collection（ImportError: cannot import name
'retrieval_metrics' / 'quality_calibration' from 'bidcraft.evaluation'）

# 实现后：RC-06 + RC-07 新测试
.venv/bin/python -m pytest tests/test_v0330_retrieval_metrics.py \
  tests/test_v0330_quality_calibration.py --disable-warnings --tb=short
21 passed in 1.58s

# 静态检查（本任务拥有的 Python 文件）
.venv/bin/ruff check bidcraft/evaluation/retrieval_metrics.py \
  bidcraft/evaluation/quality_calibration.py bidcraft/evaluation/__init__.py \
  bidcraft/evaluation/report.py tests/test_v0330_retrieval_metrics.py \
  tests/test_v0330_quality_calibration.py
All checks passed!

# 指定目标回归（含既有数据集/真实件质量/v0.30 指标）
.venv/bin/python -m pytest tests/test_v0330_retrieval_metrics.py \
  tests/test_v0330_quality_calibration.py tests/test_tender_dataset_v031.py \
  tests/test_tender_quality_v031.py tests/test_v030_stage6_metrics.py \
  --disable-warnings --tb=short
42 passed in 4.11s
```

手算校验示例：`run=["d4","d1","d5","d2","d6","d3","d1"]`、
`qrels={d1,d2,d3}` → Recall@5=`0.6667`、Recall@10=`1.0`、Precision@5=`0.4`、
MRR=`0.5`、NDCG@5=`0.4982`；聚合样例中 q1 命中第 1/3 位、q2 命中第 2 位，
宏平均 NDCG@10=`0.7753`。

## 4. 失败历史

1. 测试先行阶段两个新文件因模块不存在而收集失败（预期的红灯），实现后通过。
2. 首轮实现后 1 项失败：聚合 NDCG 期望值写错（把 q1 第二个相关文档误算在第 2 位，
   实际在去重后第 3 位）。按手算把期望从 `0.8155` 修正为 `0.7753`，未删断言、
   未降低强度。
3. 首轮 `ruff check` 报 3 项可修复问题（`__init__.py` 导入块顺序、
   `Mapping`/`Iterable`/`Sequence` 应从 `collections.abc` 导入）；修正后全通过。

## 5. 兼容性

- 纯追加模块与两个摘要键；无数据库迁移、无 schema/API/路由变化、无生产数据改写。
- 未修改任何既有测试或断言；`report_payload()` 返回字段集合未变。
- `__all__` 新增符号按排序插入，与既有导出共存。

## 6. 残余风险与数据阻塞

- **真实 qrels 数据阻塞**：仓库没有真实检索基准，运行时 `retrieval_benchmark`
  恒为“未测量”、指标全 `None`。需用户提供脱敏的真实 qrels/run/诊断后放入
  `tests/fixtures/retrieval_real/dataset.json` 才会产出数值。
- 本任务不授权修改 `.gitignore`，新目录的 `dataset.json` 暂未被忽略：真实数据
  只能本地/CI 使用，**不得 `git add`**；建议仓库层面补忽略规则。
- 指标消费的是用户导出的真实 passage/诊断信号；本任务不拥有 `knowledge.py`，
  未把采集代码接入检索链路，导出口径需人工与 `Passage`/`diagnostics` 对齐。
- Precision@k 分母固定为 k（即使去重后结果不足 k），短 run 会被惩罚；这是刻意
  固定的口径，避免按结果长度变化造成跨基准不可比。

## 7. 回滚

纯代码回滚，无数据迁移：

- 删除 `bidcraft/evaluation/retrieval_metrics.py`、
  `tests/test_v0330_retrieval_metrics.py`、本报告和 `retrieval_real`/`retrieval_benchmark.json` 夹具；
- 从 `bidcraft/evaluation/__init__.py` 移除本任务新增导入与 `__all__` 项；
- 从 `bidcraft/evaluation/report.py` 移除 `retrieval_benchmark` 注入（保留
  RC-07 的 `calibration` 时需同步从共享的本次改动中取舍）；
- 回滚后旧报告快照不受影响（历史 summary 中可能仍含该键，只读展示）。
