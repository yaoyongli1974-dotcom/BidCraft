# v0.33.1 Phase 2：真实数据阻塞与未测量契约报告

基线：Phase 1 提交 `5b5f1ed`。本阶段没有新增业务功能、没有把合成件标成真实件、没有改动导出门禁；只补齐 CI 数据状态可见性并以测试锁定“缺数据 = 未测量 ≠ 0.0”的契约。

## 1. 结论

| 项 | 结论 |
|---|---|
| 真实招标文件 E2E | DATA BLOCKED（用户尚未提供真实/脱敏标注件） |
| 真实检索 qrels 基准 | DATA BLOCKED（无真实 qrels/run/diagnostics） |
| 真实专家评分校准 | DATA BLOCKED（无真实配对评分） |
| 未测量契约 | PASS：指标为 `None`、`measured=false`、状态“未测量”，不用 0.0 冒充实测 |
| CI 数据状态可见性 | PASS：三类数据均新增非门禁状态步骤 |
| 生产数据/真实模型 | 未触碰 |

## 2. 实测仓库状态

- `tests/fixtures/tender_real/`、`tests/fixtures/retrieval_real/`、`tests/fixtures/expert_ratings_real/` 均只有 `README.md` 与 `dataset.example.json`。
- 三个目录都没有 `dataset.json`，且 `.gitignore` 排除真实 manifest 与真实样本文件，避免商业数据入库。
- `dataset_status()` 返回 `ready=false` 并带明确 reason；`retrieval_status()` 与 `expert_status()` 同样不抛异常。

## 3. CI 改动

在 `.github/workflows/ci.yml` 的 python-tests job 中，原“Real tender dataset status (non-gating)”之后新增：

1. `Real retrieval qrels status (non-gating)`：读取 `BIDCRAFT_REAL_RETRIEVAL_ROOT`（可由 GitHub Variables 注入），调用 `retrieval_status()`；就绪打印 query 数，否则打印 `DATA BLOCKED (skip): ...`。
2. `Real expert ratings status (non-gating)`：读取 `BIDCRAFT_REAL_EXPERT_ROOT`，调用 `expert_status()`；就绪打印评分对数，否则打印 `DATA BLOCKED (skip): ...`。

三步都非门禁：不把 skip 数当失败，也不在没有数据时生成假指标。原有真实招标件状态步骤、calibration 基线门禁与 verdict FAR 门禁未改。

## 4. 测试

新增 `tests/test_v0331_data_contract.py`：

- 仓库不带三个真实 `dataset.json`，但保留 README 与示例 manifest；
- 缺失数据下 `collect_retrieval_metrics()` 的 9 个指标全部为 `None`、`measured=false`、状态“未测量”，字符串里没有 `0.0`；
- 缺失数据下 `collect_quality_calibration()` 的 3 个指标全部为 `None`、`bins=[]`、`measured=false`；
- CI YAML 文本同时包含三类非门禁状态步骤且顺序为 tender → retrieval → expert。

测试先行结果：`2 passed, 1 failed`（CI 缺 retrieval/expert 状态步骤）；补齐 CI 后：`34 passed, 1 skipped`（skip 为 dataset-gated 真实 E2E，原因 DATA BLOCKED）。

## 5. 用户数据交接清单（仍未完成）

要关闭这三类 DATA BLOCKED，需要用户在隔离环境自行提供：

| 数据 | 路径 / 变量 | 必需内容 |
|---|---|---|
| 真实/脱敏招标文件 | `tests/fixtures/tender_real/dataset.json`（或 `BIDCRAFT_REAL_TENDER_ROOT`） | `source=user-provided`、schema=1、文件清单与 sha256、`required_clauses` / `required_values` / `expected_verdicts` / 可选 `responses`；文件正文不得含 SYNTHETIC/模拟/示例数据标记 |
| 检索 qrels | `tests/fixtures/retrieval_real/dataset.json`（或 `BIDCRAFT_REAL_RETRIEVAL_ROOT`） | schema=1、真实 query、qrels、run、passage diagnostics；不得使用合成 benchmark 充当真实集 |
| 专家评分 | `tests/fixtures/expert_ratings_real/dataset.json`（或 `BIDCRAFT_REAL_EXPERT_ROOT`） | schema=1、成对的系统分与专家分（0–100）、样本标签；n<2 或零方差仍按未测量 |

收到数据后，才能做真实 E2E / IR 指标 / 专家相关性验收；在此之前这些项目在最终报告中必须保持 DATA BLOCKED 或 NOT VERIFIED。

## 6. 验证命令与结果

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q tests/test_v0331_data_contract.py tests/test_v0330_dataset_gate.py tests/test_v0330_retrieval_metrics.py tests/test_v0330_quality_calibration.py tests/test_tender_dataset_v031.py` | 34 passed, 1 skipped |
| `.venv/bin/python -c "import yaml; yaml.safe_load(...)"` | YAML_OK |
| `.venv/bin/python -m ruff check bidcraft tests scripts` | All checks passed |
| `git diff --check` | clean |

## 7. 残余风险

- CI 状态步骤依赖仓库 Variables 指向真实数据根；未提供时保持 DATA BLOCKED，不会自动变绿或变红。
- 真实模型 / function-calling 与生产级 PG 仍未验证，属环境阻塞，不在本阶段处理。
- 未改任何历史迁移、业务数据表或生产配置。
