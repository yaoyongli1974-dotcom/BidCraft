# BidCraft v0.30.0 阶段 1 报告：CI 失败专项治理

- 日期：2026-11-24
- 分支：`quality/v0.30.0-reliability`
- 基线：`4d7b4dc`（阶段 0 报告见 `BIDCRAFT_V030_QUALITY_BASELINE.md`）
- 约束遵守：未推送 main、未发版、未改生产、未改历史迁移、未删测试、未降低安全阈值。

## 1. 阶段目标与结果

| 编号 | 问题 | 本阶段处理 | 状态 |
|---|---|---|---|
| V030-01 | 中文字体测试与宿主字体环境耦合，缺字体即 skip | 新增永不跳过的字体映射纯测试；`fc-match` 测试仅在确无字体环境时窄范围跳过；新增 `docx-render` CI job（安装 Noto CJK + LibreOffice + poppler，严格模式禁止 skip） | 已完成，待 CI 实跑验证 |
| V030-02 | Python 主 job 缺 psycopg | 阶段 0 前 hotfix 已解决；本阶段在拆分后的 lint/tests 两个 job 中继续保留 `--extra postgres` | 已完成 |
| V030-03 | PG 并发测试任务串扰 | 每个用例前 `TRUNCATE ... RESTART IDENTITY CASCADE` 数据边界（保留 `alembic_version`）；移除 `_make_jobs()` 全局 delete；新增 20 轮并发认领压测 + 双 worker 池并行隔离测试 | 已完成，待 CI 真 PG 验证 |
| V030-04 | CI job 职责混杂、缺失败摘要/报告/环境信息 | 拆分 `python-lint` / `python-tests`；web/postgres/docx-render 增加环境信息；pytest/PG/DOCX JUnit 报告上传；失败摘要列出失败用例；新增 `docx-render` 严格 job | 已完成 |

## 2. 变更清单

### `.github/workflows/ci.yml`
- 拆分 `python-lint`（ruff、Alembic 单 head、版本/tag/changelog/web 版本一致性、环境信息）与 `python-tests`（全量 pytest + JUnit、calibration、verdict regression、失败摘要、报告上传）。
- `web` / `postgres` job 增加 Environment info；`postgres` 增加 JUnit 上传与失败摘要。
- 新增 `docx-render` job：apt 安装 `fonts-noto-cjk libreoffice-writer poppler-utils`，`BIDCRAFT_DOCX_REQUIRE_RENDER=1` 严格运行 DOCX/导出测试，断言 0 skipped / 0 failures。
- 保留 hotfix 后的安全作业门禁（生产依赖 npm audit 为门禁，全量审计 advisory；Bandit 精确抑制改进留待阶段 2）。

### `tests/test_batch4_export_engine.py`
- 新增 `_CJK_FONT_MARKERS`、`_assert_cjk_capable_name` 与 `test_all_preset_fonts_have_cjk_capable_substitutions()`：不依赖宿主字体，永远运行，校验每个预设字体都有 CJK 能力替代映射。
- 原 `fc-match` 测试仅在 `fc-list` 缺失或系统完全无 `:lang=zh` 字体时跳过；docstring 明确 CI 渲染 job 不得跳过。

### `tests/test_batch4_golden.py`
- 移除 `@pytest.mark.skipif`；新增 `_require_render_tools()`：设置 `BIDCRAFT_DOCX_REQUIRE_RENDER=1` 时若缺 `soffice/pdfinfo/pdftotext` 则 `pytest.fail` 列出缺失工具，否则本地仍有条件 skip。所有 golden 断言保持不变。

### `tests/test_postgres_integration.py` + `tests/test_v030_pg_isolation.py`
- 两个模块共享隔离契约：autouse fixture 在每个用例前动态读取所有应用表，执行 `TRUNCATE ... RESTART IDENTITY CASCADE`，仅排除 `alembic_version`。
- 移除 `_make_jobs()` 中的全局 `Job` 删除；断言改为严格匹配本用例 id 集合。
- 新增 20 轮 × 4 worker 并发认领压力测试；新增两个 `ThreadPoolExecutor` 并行池交叉认领隔离测试。

## 3. 验证结果

| 检查 | 命令 | 结果 |
|---|---|---|
| Ruff | `.venv/bin/python -m ruff check bidcraft tests` | All checks passed |
| diff | `git diff --check` | 通过 |
| DOCX/字体定向 | `pytest tests/test_batch4_export_engine.py tests/test_batch4_golden.py tests/test_docx_renderer.py tests/test_docx_validator.py` | 11 passed / 1 skipped（本地缺渲染工具） |
| 严格渲染模式 | `BIDCRAFT_DOCX_REQUIRE_RENDER=1 pytest ...` | 1 failed / 11 passed（失败信息明确列出缺失 soffice/pdfinfo/pdftotext，符合设计） |
| PG 定向 | `pytest tests/test_postgres_integration.py tests/test_v030_pg_isolation.py` | 12 skipped（本地无 `BIDCRAFT_TEST_PG_URL`，环境性跳过，非测试删除） |
| CI YAML | `yaml.safe_load` | 8 个 job 解析通过（python-lint/python-tests/web/postgres/docx-render + 3 安全作业） |
| 版本一致性 | 脚本检查 backend/web/package-lock | 全部 0.29.0 |

## 4. 遗留风险与交接

1. **真实 PG 未本地验证**：本机无 PostgreSQL / docker，`BIDCRAFT_TEST_PG_URL` 未设置；新 TRUNCATE 边界、20 轮压测与并行隔离测试须由 CI 的 `postgres:16-alpine` job 实跑确认。
2. **DOCX golden 未本地渲染**：本机无 LibreOffice/poppler；CI 新增 `docx-render` job 是首个真实渲染防线，需关注该 job 首页运行结果。
3. **阶段 2 仍待处理**：Bandit 迁移目录级排除需改为精确 per-line 白名单；B307 需补恶意表达式测试或 AST 解释器；npm critical/high 需通过评估后的 major 升级清零。

## 5. 回滚说明

本阶段仅改动上述 5 个文件（含 1 个新文件），均在独立分支本地提交前状态。逐文件 revert 即可回滚，不影响业务代码与历史迁移。
