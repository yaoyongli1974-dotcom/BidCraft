# 真实检索基准集（仅用户提供）

本目录用于放置**脱敏后的真实检索基准**：人工标注的 qrels、真实检索 run，
以及从 `SearchResult.diagnostics` / `Passage` 如实导出的溯源与范围信号。
仓库只保留本说明和 `dataset.example.json`；`dataset.json` 与实际检索日志
**不得提交**。没有真实数据时 `collect_retrieval_metrics()` 返回
`status='未测量'`、所有指标为 `None`（绝不记为 0.0）。

## 放置步骤

1. 复制 `dataset.example.json` 为 `dataset.json`（不要提交）。
2. 在 `qrels` 中给出每条查询的**人工标注相关文档 ID**；在 `run` 中给出真实
   检索结果的文档 ID 有序列表（越靠前排名越高）。
3. `passages` 每条记录对应一次真实命中，字段取自 `Passage`：
   `doc_id`、`document_id`、`page`、`heading`、`chunk_id`（四要素非空才算
   来源完整），`scope_ok`（project/package 范围与个人库授权校验通过），
   `valid_asset`（资产已发布/在有效期内且块可解析）。
4. `diagnostics` 每条查询记录真实检索诊断：`searched` / `skipped` /
   `failed`；`searched == 0` 或 `failed > 0` 记为一次降级检索。
5. 运行 `pytest tests/test_v0330_retrieval_metrics.py` 验证契约；未就绪时
   测试报告“未测量”，不会把合成夹具冒充真实基准。

## 指标口径（宏平均，先逐查询计算再取算术平均）

- `recall_at_5` / `recall_at_10`：命中相关文档数 / 标注相关文档数；
- `precision_at_5`：命中数 / 5（重复文档先按首次出现去重，不放大命中）；
- `mrr`：首个命中文档位次的倒数（截止 10）；
- `ndcg_at_10`：二值相关性 DCG/IDCG，同等相关文档增益相同，去重后计算；
- `source_completeness`：溯源四要素齐全的 passage 比例；
- `scope_violation`：`scope_ok=false` 的 passage 比例；
- `invalid_asset_rate`：`valid_asset=false` 的 passage 比例；
- `degraded_retrieval_rate`：降级查询数 / 有诊断的查询数。

零分母（无 qrels、无 passage、无诊断）时对应指标为 `None`，不是 `0.0`。

## 安全要求

- 真实检索日志/文件名不得含公司全称、联系人、手机号、邮箱等敏感信息，需脱敏。
- 合成夹具 `tests/fixtures/retrieval_benchmark.json` 标记 `"synthetic": true`，
  `load_benchmark()` 明确拒绝，不能作为真实数据。
- 本目录的 `dataset.json` 暂未被 `.gitignore` 覆盖（本任务不授权修改
  `.gitignore`）：**务必不要 `git add` 真实清单**；建议在仓库层面补充忽略规则。
