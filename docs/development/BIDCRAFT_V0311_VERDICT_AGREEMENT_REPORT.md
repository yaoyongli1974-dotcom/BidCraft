# BidCraft v0.31.1 阶段 6：判定一致性指标启用报告

## 1. 结论

真实件数据集现在可在人工标注同时提供 `expected_verdicts` 与 `responses`（条款→应答正文）时，用与 `check_project` 完全相同的 `verdict.judge()` 引擎复算判定，并计算 `verdict_agreement`。缺少应答文本时仍明确返回 `null`（未测量），绝不记为 0。

## 2. 交付项（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 数据集契约 | 完成 | `tender_dataset.py` 校验可选 `responses` 为字符串映射 |
| 2 | 清单保留 | 完成 | `load_dataset()` 在 responses 存在时透传 |
| 3 | 标注示例 | 完成 | `dataset.example.json` 增加 `responses` 示例 |
| 4 | README | 完成 | 说明判定状态大小写不敏感、缺应答为未测量 |
| 5 | 引擎复用 | 完成 | `tender_quality.py` 直接调用 `verdict.judge()`，无第二套规则 |
| 6 | 状态归一 | 完成 | 实测/期望状态均小写比较 |
| 7 | 宏平均 | 完成 | 逐文件一致率再宏平均 |
| 8 | 不一致证据 | 完成 | `notes` 记录前 3 条不一致条款与期望/实测 |
| 9 | 无应答降级 | 完成 | 无可比条款返回 `None` 并给出说明 |
| 10 | 单文件错误隔离 | 完成 | 沿用 `collect_tender_quality()` 的逐文件 try/except |
| 11 | 合成数据隔离 | 完成 | 仍拒绝 synthetic 标记与示例样本 |

## 3. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | 通过 |
| `pytest tests/test_tender_quality_v031.py tests/test_tender_dataset_v031.py` | **13 passed** |
| 全量后端 | 阶段 8 统一执行 |

## 4. 边界

- 没有真实脱敏样本与人工标注时，指标仍是「未测量」；本阶段只保证口径可计算、可审计。
- 不修改 `verdict.judge()` 的判定规则或四态语义。
