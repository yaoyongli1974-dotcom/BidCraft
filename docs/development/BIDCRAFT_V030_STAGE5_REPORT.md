# BidCraft v0.30.0 阶段 5 报告：DOCX 引擎与资源边界

- 分支：`quality/v0.30.0-reliability`
- 前置：阶段 0–4，最新提交 `55adb2e`（1549 pytest）
- 风险报告：`docs/development/BIDCRAFT_V030_STAGE5_RISK_REPORT.md`
- 用户批准：**G2/G3/G6** 新资源上限；**G8 PDF 队列化暂缓**
- 约束遵守：未推送 main、未发版、未改生产、未删测试、未改历史迁移、未降低门禁。

## 1. 问题与修复对照（11 项）

| # | 编号 | 问题 | 修复 | 证据 |
|---|---|---|---|---|
| 1 | G1 | `_check_tables` 把所有表格当数据表，布局/表单表首行空单元格被判 error，阻断交付 | `DocumentTable.kind` + `w:tblDescription=bidcraft-kind:<kind>` 标记；仅 `data` 表沿用表头/数据行校验，其余仅结构校验；无标记旧表仍按 data 处理 | `document_ir.py`、`renderer.py`、`validator.py`、`export/docx.py` |
| 2 | G2 | 上传 ZIP 仅校验压缩体积，解压炸弹可 OOM | `OversizedUploadError` + 中央目录 `file_size` 合计上限 512 MiB（可 env 覆盖）；TXT 同限；HTTP 400 | `parsing/loader.py`、`config.py`、`pipeline.py` |
| 3 | G3 | 解析文本/表格/页数无上限 | 800 万字符 / 2000 表 / 5000 页，超限确定性截断并留可见标记、parse_note、响应 notes 与审计 | `pipeline.py`、`api/routes/projects.py`、`config.py` |
| 4 | G4 | `_page_for_quote` 每条要求重复 compact 所有页面 | 每次 `extract()` 预计算 compacted pages 并复用 | `parsing/extract.py` |
| 5 | G5 | 通用 `![](...)` 图片语法绕过 MAX_IMAGES | 统一 IR 正则与计数，两种引擎共用同一个 guard | `document_ir.py`、`export/docx.py` |
| 6 | G6 | 无正文总字符上限 | `MAX_TOTAL_BODY_CHARS=20M` / `MAX_NODE_BODY_CHARS=200k`（env 可覆盖），超限明确失败 | `renderer.py`、`export/docx.py` |
| 7 | G7 | 专业 renderer 无守卫，直接调用绕过 export 限制 | 单一 `guard_export_limits()` 在 `render_document` 入口生效，export 传参不重复检查 | `renderer.py` |
| 8 | G8 | PDF 超时/队列化 | 用户决定暂缓；保留现有 240s 超时与 CI job | 无改动 |
| 9 | 回归 | 表格分类标记在两条渲染路径均存活 | renderer/export 写入，validator 读取 | `tests/test_v030_stage5_tables.py` |
| 10 | 回归 | 超限上传/截断/页数预计算有测试 | 11 个新限额测试 + 12 个新表格测试 | `tests/test_v030_stage5_limits.py`、`tests/test_v030_stage5_tables.py` |
| 11 | 兼容 | 旧 DOCX 与常规大文档不误伤 | 无标记表格仍按 data；默认值远高于真实标书，现有导出/解析套件全过 | 定向 94 passed / 1 skipped |

## 2. 变更清单

- `bidcraft/documents/{document_ir,renderer,validator}.py`、`bidcraft/export/docx.py`。
- `bidcraft/config.py`：新增 `BIDCRAFT_MAX_UNCOMPRESSED_UPLOAD_BYTES`、`BIDCRAFT_MAX_PARSED_CHARS/TABLES/PAGES` 及 clamp。
- `bidcraft/parsing/{loader,extract}.py`、`bidcraft/pipeline.py`、`bidcraft/api/routes/projects.py`（仅上传/重解析审计）。
- 新测试：`tests/test_v030_stage5_tables.py`（12）、`tests/test_v030_stage5_limits.py`（11）。
- `bidcraft/api/shared.py` 已检查错误映射正确，无需改动。

## 3. 验证结果

| 项目 | 命令 | 结果 |
|---|---|---|
| Lint | `ruff check bidcraft tests scripts` | All checks passed |
| diff | `git diff --check` | 通过 |
| 定向 | `pytest tests/test_v030_stage5_* test_docx_* test_batch4_* test_extract test_xlsx test_ocr test_calibrate test_api` | **94 passed, 1 skipped**，1 warning |
| 全量后端 | `pytest -o addopts="" -q` | **1574 passed, 16 skipped**，889.50s，0 failed（`/tmp/opencode/v030_stage5_full.out`） |

## 4. 兼容性与已知限制

- `w:tblDescription` 为标准字段，不影响可见内容；未知 kind 回退 layout，缺失标记回退 data（保持旧误报严重度）。
- ZIP 大小守卫信任中央目录声明；刻意低报解压大小的畸形 ZIP 不在本轮范围。
- 截断为头部保留，尾部附录被丢弃但始终有可见标记；CLI 路径无 AuditLog 行。
- PDF 队列化/G8 暂缓，沿用现有超时与 CI docx-render job。

## 5. 回滚

1. `git checkout -- bidcraft/documents/*.py bidcraft/export/docx.py bidcraft/config.py bidcraft/parsing/*.py bidcraft/pipeline.py bidcraft/api/routes/projects.py`
2. 删除 `tests/test_v030_stage5_*.py` 与本报告。
3. 无迁移/schema 变更；已被截断入库的文本需提高限额后重新解析才能恢复。
