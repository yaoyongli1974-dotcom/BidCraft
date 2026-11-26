# RC-07 AI 质量校准报告

## 1. 范围与基线

- 分支：`fix/v0.33.0-rc-reliability`，基线 HEAD `591891c`。
- 目标：把系统质量分（六维评分 / `overall_score`）与专家评分配对，计算
  Spearman、Pearson、分桶校准与 Brier；建立真实专家评分数据集契约。
- 审计事实：`calibration.py` 已有 FAR/FRR 判定回归（与本次评分校准不同口径）；
  `scorer.py` 提供六维分；仓库原本没有 Spearman/Pearson/Brier 或专家相关性指标。
- 约束同 RC-06：仅授权文件，不碰 `web/src`、`quality_metrics.py`、评估路由、
  既有测试；不提交/推送、不 `ruff format`、不跑全量套件。

## 2. 实现

### 新增 `bidcraft/evaluation/quality_calibration.py`

纯函数、确定性数学，无 numpy/scipy、不查库：

| 函数 | 口径 |
| --- | --- |
| `pearson_correlation(pairs)` | 原始 0..100 分数的积矩相关 |
| `spearman_correlation(pairs)` | 并列分数取平均名次后对名次算 Pearson |
| `brier_score(pairs)` | 两域同除 100 归一化到 0..1 的均方误差 |
| `binned_calibration(pairs)` | 预测分 `[0,60)/[60,75)/[75,90)/[90,100]` 分桶，输出 n/均值/gap |
| `calibrate_ratings(pairs)` | 汇总：n<2 或任一域零方差 → 全 `None`、`measured=False` |
| `load_ratings(root)` | 真实清单契约校验：`schema=1`、`synthetic` 与合成标记拒绝、0..100 范围校验 |
| `expert_status(root)` | 就绪状态，缺数据不抛异常 |
| `collect_quality_calibration(root)` | 未就绪时 `status='未测量'`、`measured=False`、指标全 `None` |

所有相关系数与比率 `round(..., 4)`；空校准桶保留 `n=0` 且均值/gap 为 `None`，
不用 0 伪造“校准完美”。样本少于 2 对或预测/专家分数零方差时相关性一律
“未测量”。

### 报告摘要注入

`build_quality_report()` 追加 `calibration` 键（与 RC-06 的
`retrieval_benchmark` 同处注入），默认未测量；`report_payload()` 字段集合不变。

### 夹具与导出

- `tests/fixtures/expert_ratings.json`：`synthetic=true` 的自检夹具；
  `load_ratings()` 拒绝其作为真实评分，`calibrate_ratings()` 可用于手算值自检。
- `tests/fixtures/expert_ratings_real/README.md`、`dataset.example.json`：真实
  评分放置步骤、分桶口径与脱敏要求；仓库内**没有** `dataset.json`。
- `bidcraft/evaluation/__init__.py`：新增 8 个公共符号，`__all__` 保持排序。

## 3. 测试与验证

新增 `tests/test_v0330_quality_calibration.py`（11 项：手算 Spearman/Pearson/
Brier、并列平均名次、分桶精确值与空桶、n<2/零方差未测量、非法输入拒绝、
真实清单精确汇总、缺数据全 None、空清单未就绪、合成夹具拒绝、真实数据缺席、
报告摘要注入）。

手算基准：`PAIRS=[(55,60),(65,70),(70,80),(80,90),(85,75),(95,100)]`
→ Spearman `0.8286`、Pearson `0.8693`、Brier `0.0063`；
并列例 `[(80,90),(80,70),(70,60)]` → Spearman `0.866`、Pearson `0.7559`。

实际命令与结果（与 RC-06 合并验证，见 `07_RETRIEVAL_BENCHMARK_REPORT.md` 第 3 节）：

```text
.venv/bin/python -m pytest tests/test_v0330_retrieval_metrics.py \
  tests/test_v0330_quality_calibration.py --disable-warnings --tb=short
21 passed in 1.58s

.venv/bin/ruff check bidcraft/evaluation/retrieval_metrics.py \
  bidcraft/evaluation/quality_calibration.py bidcraft/evaluation/__init__.py \
  bidcraft/evaluation/report.py tests/test_v0330_retrieval_metrics.py \
  tests/test_v0330_quality_calibration.py
All checks passed!

.venv/bin/python -m pytest tests/test_v0330_retrieval_metrics.py \
  tests/test_v0330_quality_calibration.py tests/test_tender_dataset_v031.py \
  tests/test_tender_quality_v031.py tests/test_v030_stage6_metrics.py \
  --disable-warnings --tb=short
42 passed in 4.11s
```

## 4. 兼容性

- 纯追加模块 + 一个摘要键；无迁移、无 schema/API 变化、无生产数据改写。
- 未修改/删除任何既有测试或断言；与 `calibration.py` 的 FAR/FRR 判定回归互不干扰。
- Brier 明确按 0..1 归一化，避免与 0..100 分数直接相减放大 10⁴ 倍。

## 5. 残余风险与数据阻塞

- **真实专家评分数据阻塞**：仓库没有真实评分，运行时 `calibration` 恒为
  “未测量”、指标全 `None`；需用户提供脱敏的真实配对评分放入
  `tests/fixtures/expert_ratings_real/dataset.json` 后才产出数值。
- 本任务不授权修改 `.gitignore`，新目录的 `dataset.json` 暂未被忽略：真实数据
  只能本地/CI 使用，**不得 `git add`**；建议仓库层面补忽略规则。
- 评分口径要求预测分与专家评分同源（六维参考分 0..100）；跨量表数据须先由
  人工归一化，模块只做范围校验，不推断量表。
- 相关性只描述一致性，不代表系统分数绝对正确；Brier 同时受校准与评分离散度
  影响，需结合分桶 gap 人工复核。

## 6. 回滚

纯代码回滚，无数据迁移：

- 删除 `bidcraft/evaluation/quality_calibration.py`、
  `tests/test_v0330_quality_calibration.py`、本报告和 `expert_ratings_real`/
  `expert_ratings.json` 夹具；
- 从 `bidcraft/evaluation/__init__.py` 移除本任务新增导入与 `__all__` 项；
- 从 `bidcraft/evaluation/report.py` 移除 `calibration` 注入；
- 回滚后旧报告快照不受影响（历史 summary 中可能仍含该键，只读展示）。
