# BidCraft v0.31.0 阶段 5：真实招标文件数据集框架报告

基线：`549a908`（阶段 4）。阶段 0～4 详见对应 `BIDCRAFT_V031_*` 报告。

## 1. 结论

**框架已完成并通过契约测试；真实样本未就绪（需用户提供脱敏真实文件）。** 本阶段没有编造真实样本，也没有把现有 10 份合成夹具改名为“真实件”。仓库状态通过测试断言锁定：`tests/fixtures/tender_real/dataset.json` 不存在。

## 2. 交付内容（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 独立目录与忽略规则 | 完成 | `.gitignore` 新增 `tests/fixtures/tender_real/*` 并白名单 README/示例清单；真实文件与 `dataset.json` 永不入库 |
| 2 | 数据集清单规范 | 完成 | `bidcraft/evaluation/tender_dataset.py` `DATASET_SCHEMA=1`、`dataset.example.json` |
| 3 | 相对路径与穿越防护 | 完成 | 拒绝绝对路径、`..` 分段、重复路径 |
| 4 | 文件存在性 | 完成 | `load_dataset()` 逐项检查 |
| 5 | SHA-256 指纹校验 | 完成 | 流式哈希；清单给出指纹时强制匹配 |
| 6 | 人工标注完整性 | 完成 | 强制 `required_clauses` / `required_values` / `expected_verdicts` 三项非空 |
| 7 | 合成样本防混入 | 完成 | 拒绝 `synthetic: true` 与文件头 `SYNTHETIC`/`模拟`/`示例数据` 标记 |
| 8 | 就绪状态 API | 完成 | `dataset_status()` 对缺清单/空清单/非法清单返回 `ready=False` + 明确原因，不抛异常 |
| 9 | 导出符号 | 完成 | `bidcraft/evaluation/__init__.py` 导出 `DATASET_SCHEMA`/`load_dataset`/`dataset_status` |
| 10 | 契约测试 | 完成 | `tests/test_tender_dataset_v031.py` 9 项：缺失/空集/有效集/指纹错/合成标记/缺标注/路径穿越/仓库无真实集 |
| 11 | 与现有回归的关系 | 完成 | 既有 10 份合成夹具与 `calibrate --compare-baseline` 门禁原样保留，`test_calibrate*` 48 passed |

## 3. 真实件接入步骤

1. 用户将脱敏后的真实招标文件放入 `tests/fixtures/tender_real/`（不入 git）。
2. 复制 `dataset.example.json` 为 `dataset.json`，逐项填写 `path`、`sha256`、三类人工标注。
3. 运行 `pytest tests/test_tender_dataset_v031.py` 与既有 calibrate 门禁；`dataset_status()` 返回 `ready=True` 后方可把真实件纳入 CI 回归。

## 4. 验证结果

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests` | All checks passed |
| `git diff --check` | 无空白错误 |
| `pytest tests/test_tender_dataset_v031.py tests/test_calibrate.py tests/test_calibrate_baseline.py` | **48 passed** |

## 5. 未就绪项与风险

- **真实招标文件与人工标注未提供**：无法计算真实召回率、误判率或跨格式保真度；阶段 6 的“真实件质量指标”只能保留接口与口径，不得声称已验证。
- 数据集仅校验契约，不执行解析/判定；与 `calibrate.py` 的联动需真实文件到位后再接入。
- 回滚：恢复 `.gitignore`、`bidcraft/evaluation/{__init__,tender_dataset}.py`，删除 `tests/fixtures/tender_real/{README.md,dataset.example.json}`、`tests/test_tender_dataset_v031.py` 与本报告；无迁移/数据变更。
