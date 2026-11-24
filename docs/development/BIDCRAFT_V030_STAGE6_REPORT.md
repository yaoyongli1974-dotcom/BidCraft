# BidCraft v0.30.0 阶段 6 报告：回归集、判定一致性与质量指标

- 分支：`quality/v0.30.0-reliability`
- 前置：阶段 0–5，最新提交 `a2f2d5f`（1574 pytest）
- 风险报告：`docs/development/BIDCRAFT_V030_STAGE6_RISK_REPORT.md`
- 用户批准：**合成脱敏样本 + 基线值/表格门禁**、**三项质量指标写入报告**。
- 约束遵守：未推送 main、未发版、未改生产、未删测试、未改历史迁移、未降低门禁。

## 1. 问题与修复对照（11 项）

| # | 编号 | 问题 | 修复 | 证据 |
|---|---|---|---|---|
| 1 | V030-14 | 回归集全为扁平手写件，表格/OCR/大写金额/跨章引用缺失 | 新增 4 份全合成样本（appendix/boq/pagination/crossref），加哨兵与真实形态噪声 | `tests/fixtures/regression/synthetic-*.md` |
| 2 | V030-14 | 无脱敏自动检查 | 测试扫描手机/身份证/邮箱/企业后缀并断言 SYNTHETIC 哨兵；原 6 份也统一加哨兵 | `tests/test_v030_stage6_fixtures.py` |
| 3 | V030-14 | 基线只比命中数，错误值/表格失效漏检 | 基线新增 `values` / `fixtures.{expected_values,min_tables,min_materials}`；`compare_baseline` 比值与下限 | `bidcraft/calibrate.py`、`baseline.json` |
| 4 | V030-14 | CI 只跑 calibrate 不比基线 | python-tests 加 `--compare-baseline`，保留 0/1/2 退出码门禁 | `.github/workflows/ci.yml` |
| 5 | V030-15 | `judge_for_project` N+1（100 条款约 900–1100 SQL） | 上下文一次算 meta/deadline/matcher，列表与 PATCH 复用 | `bidcraft/api/shared.py`、`api/routes/projects.py` |
| 6 | V030-15 | GET /requirements 与 /check 判定可能漂移 | 集成测试对同一时限条款断言 state 与 layer4.deadline_window 完全一致 | `tests/test_v030_stage6_verdict.py` |
| 7 | V030-15 | 三项业务指标只有口径无生产者 | `collect_quality_metrics()` 写入 `QualityReport.summary.metrics` | `bidcraft/evaluation/quality_metrics.py`、`evaluation/report.py` |
| 8 | V030-15 | 指标分母无定义 | manual_edit 2/4=0.5；issue_density 5/209×1000=23.9234；boq 3/4=0.75；零分母 0.0 | `tests/test_v030_stage6_metrics.py` |
| 9 | V030-15 | `/admin/health` 依赖运行时 monkey-patch 注入全局指标 | `admin_health` 内显式合并，响应键集合不变（26 键） | `bidcraft/api/routes/admin.py` |
| 10 | 兼容 | 老基线无新键仍原样通过 | schema 仍 1，所有新键可选；负向测试验证值漂移 exit 1、缺失基线 exit 2 | `tests/test_calibrate_baseline.py` |
| 11 | 回归 | 新基线工具实测通过 | 10 文件全 ok/失败 0，`--compare-baseline` exit 0 | `/tmp` 命令输出 |

## 2. 关键数据

- SQL SELECT 计数（1 条 vs 100 条）：projects 1/1、facts 1/1、materials 1/1、parameters 1/1、scoring_items 1/1、requirements 2/2、risks 1/1、model_configs 0/0（LLM 开启时 2/2）。
- 基线：files=10，ok=10，failed=0，required_hit_rate=1.0，avg_materials=0.0，ocr_files=0；4 份合成样本各 6 个期望值，min_tables/min_materials=0（Markdown 文本加载器实测无结构化表格）。
- BOQ 匹配口径：`boq.align()` row status ∈ {matched, qty_diff, unit_diff, spec_diff}，与 `boq._summarize()` 一致。

## 3. 验证结果

| 项目 | 命令 | 结果 |
|---|---|---|
| Lint | `ruff check bidcraft tests scripts` | All checks passed |
| diff | `git diff --check` | 通过 |
| 定向 | `pytest tests/test_calibrate* test_v030_stage6_* test_verdict_api test_evaluation_phase3 test_batch7_* test_api_package test_fix3_health` | **109 passed**, 1 warning |
| 基线门禁 | `.venv/bin/bidcraft calibrate tests/fixtures/regression --compare-baseline tests/fixtures/regression/baseline.json` | exit 0 |
| 全量后端 | `pytest -o addopts="" -q` | 首次 1597 passed / 1 failed / 16 skipped：`test_baseline_catalog.py` 硬编码 "6 份 fixture" 过期（阶段 6 新增 4 份合成样本后为 10）；已改为按目录实测数量断言（非删除/弱化），`tests/test_baseline_catalog.py` 10 passed。第二次全量 **1598 passed, 16 skipped**，891.20s，0 failed（`/tmp/opencode/v030_stage6_full2.out`）。 |

## 4. 兼容性与风险

- 基线 schema 仍为 1；旧基线无新键时不触发新比较。
- 判定四态、证据与阈值完全不变；N+1 修复只复用上下文，LLM 开启时仍只解析一次端点。
- Markdown 回归件无法产生结构化表格，min_tables/min_materials 实测为 0；正向表格门禁需未来补 `.xlsx`/DOCX 样本。
- `issue_density` 统计该项目所有报告中的未处置 finding（含本次），重评估会累积历史；已在 notes 记录。
- 无 HTTP 路由新增（仍 250 条），`report.summary.metrics` 为可选新增键。

## 5. 回滚

1. `git checkout -- bidcraft/calibrate.py bidcraft/api/shared.py bidcraft/api/routes/projects.py bidcraft/api/routes/admin.py bidcraft/evaluation/report.py bidcraft/evaluation/__init__.py .github/workflows/ci.yml docs/calibration.md tests/test_calibrate.py tests/test_calibrate_baseline.py tests/fixtures/regression`
2. 删除 4 份 `synthetic-*.md`、三个 `tests/test_v030_stage6_*.py`、`bidcraft/evaluation/quality_metrics.py`、本报告与风险报告。
3. 无迁移/schema 变更；基线文件回滚后 CI 恢复旧门禁。
