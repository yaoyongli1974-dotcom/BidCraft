# BidCraft v0.31.0 阶段 6：真实件质量指标报告

基线：`b65eaa6`（阶段 5）。阶段 0～5 详见对应 `BIDCRAFT_V031_*` 报告。

## 1. 结论

**指标接口与口径已完成并通过契约测试；真实件结果“未测量”——用户尚未提供脱敏真实招标文件及人工标注。** 本阶段没有伪造真实样本，也没有把阶段 5 的合成夹具改名为真实件；未就绪时报告摘要明确写 `status=未测量`，三项比率均为 `None`。

## 2. 交付内容（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 数据集契约复用 | 完成 | `bidcraft/evaluation/tender_quality.py` 直接调用阶段 5 的 `dataset_status()` / `load_dataset()`，不复制校验逻辑 |
| 2 | 未就绪语义 | 完成 | 无清单/空清单/非法清单 → `status=未测量`、`reason` 保留、`notes` 非空 |
| 3 | 真实文件解析 | 完成 | `load_document()` + `parsing.extract.extract()`；包由 `calibrate._select_package()` 按文本选择 |
| 4 | 条款召回率 | 完成 | `required_clauses` 与抽取 Requirement 文本做归一化包含匹配，`clause_recall` 为文件内比例 |
| 5 | 关键值解析率 | 完成 | `required_values` 的 `value` 与解析字段文本做归一化包含匹配，`value_parse_rate` 为文件内比例 |
| 6 | 判定一致性口径 | 完成（接口） | `verdict_agreement` 标注缺失应答文本时强制 `None`，`notes` 明说“未测量”，绝不写 0 |
| 7 | 宏平均 | 完成 | 多文件按有效值取宏平均并保留 4 位小数；单文件解析失败记入 `notes` 并降级状态 |
| 8 | 可审计明细 | 完成 | 每文件返回 `path`、`matched_clauses`、`matched_values`、`normalized` 与 `notes` |
| 9 | 报告摘要集成 | 完成 | `bidcraft/evaluation/report.py` 在 `summary` 中加入 `tender_quality`，与既有 `metrics`、`commercial` 键并存 |
| 10 | 导出符号 | 完成 | `bidcraft/evaluation/__init__.py` 导出 `collect_tender_quality` |
| 11 | 契约测试 | 完成 | `tests/test_tender_quality_v031.py` 4 项：未就绪/就绪计算/未测量一致性/报告摘要集成；与数据集、评估测试合计 27 passed |

## 3. 指标口径与边界

- `clause_recall`、`value_parse_rate` 只对人工标注项计算，分母为标注条目数；无标注时返回 `None`。
- `verdict_agreement` 需要数据集标注 `responses`（条款→应答文本）才能复算；当前 schema 示例只含三类必填标注，所以一律 `None`。这避免“没测”被误读为“质量为零”。
- 单文件解析抛错时该文件不计入宏平均；`status` 降为 `未测量`，错误写入 `notes`。
- 文件内容与指纹仍由阶段 5 的清单校验保证；本模块不写文件、不联网、不改业务规则。

## 4. 验证结果

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | All checks passed |
| `git diff --check` | 无空白错误 |
| `pytest tests/test_tender_quality_v031.py tests/test_tender_dataset_v031.py tests/test_evaluation_phase3.py tests/test_batch7_evaluation.py` | **27 passed** |
| 全量后端 `.venv/bin/python -m pytest -o addopts="" -q` | **1669 passed, 18 skipped, 1 warning in 742.89s**，零失败（`/tmp/opencode/v031_stage6_full.out`） |

## 5. 未就绪项与风险

- **真实招标文件与人工标注未提供**：无法给出真实召回率、误判率或跨格式保真度；本阶段只交接口与口径，归类为“已完成但验证不足（真实指标未测量）”。
- 判定一致性需要扩展数据集标注 `responses` 后才能启用；不得在未扩展前拿合成应答替代。
- 回滚：恢复 `bidcraft/evaluation/{__init__,report}.py`，删除 `bidcraft/evaluation/tender_quality.py`、`tests/test_tender_quality_v031.py` 与本报告；无迁移/数据/API 路由变更。
