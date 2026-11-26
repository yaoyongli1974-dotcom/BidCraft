# 真实专家评分集（仅用户提供）

本目录用于放置**脱敏后的真实专家评分**：每条记录是一个
`(系统预测分, 专家评分)` 配对，两域均为 0..100 的六维质量参考分。
仓库只保留本说明和 `dataset.example.json`；`dataset.json` **不得提交**。
没有真实评分时 `collect_quality_calibration()` 返回 `status='未测量'`、
所有指标为 `None`（绝不记为 0.0）。

## 放置步骤

1. 复制 `dataset.example.json` 为 `dataset.json`（不要提交）。
2. 每条 `pairs` 记录写成 `[predicted_score, expert_rating]`，例如
   `[82.5, 90]`。少于 2 对、预测分或专家评分零方差时相关性记为未测量。
3. 预测分取 `QualityReport.overall_score` 或六维分；专家评分由人工按同一
   口径独立打分，不得由预测分复制或反推。
4. 运行 `pytest tests/test_v0330_quality_calibration.py` 验证契约。

## 指标口径

- Spearman：并列分数用平均名次，对名次算 Pearson；
- Pearson：原始分数的积矩相关；
- Brier：把两域同时除以 100 归一化到 0..1 后计算均方误差；
- 分桶：预测分 `[0,60) / [60,75) / [75,90) / [90,100]`，逐桶输出
  样本数、预测均值、专家均值与 `gap=专家均值-预测均值`；空桶均值/gap 为 `None`。

## 安全要求

- 真实评分不得包含可识别评审人/公司的信息，需脱敏后使用。
- 合成夹具 `tests/fixtures/expert_ratings.json` 标记 `"synthetic": true`，
  `load_ratings()` 明确拒绝，不能作为真实专家评分。
- 本目录的 `dataset.json` 暂未被 `.gitignore` 覆盖（本任务不授权修改
  `.gitignore`）：**务必不要 `git add` 真实清单**；建议在仓库层面补充忽略规则。
