# v0.33.1 Phase 3 验收报告：长文档 / 跨章节一致性 / DOCX-PDF 工程验证

## 1. 范围与结论

本阶段只做**合成工程验证**，不把合成文档冒充真实招标文件、真实长文档版式或真实客户数据。

结论：已完成。跨章节上下文在长文档下会丢失全书章节索引的真实缺陷已修复；DOCX 大型合成文档结构与 DOCX 包校验通过；PDF 超时配置可覆盖已由测试确认。真实版式、真实 PDF 渲染、真实跨章节业务冲突仍标记 NOT VERIFIED / DATA BLOCKED。

## 2. 代码与测试改动

| 项目 | 实际文件/函数 | 改动 |
|---|---|---|
| 跨章节上下文 | `bidcraft/generation/writer.py::continuity_digest` | 新增 `_clip_lines()`；长文档优先保留每一章标题，必要时先舍弃摘要再舍弃近期正文，所有截断按行边界，避免 `text[:cap]` 把全书索引整段截掉或截断半行 |
| 长文档测试 | `tests/test_writer_digest.py::test_long_document_digest_keeps_every_heading_within_cap` | 50 章 × 长正文，cap=1200，断言全部章节标题存在、长度受限、不以半行正文结束 |
| 大型 DOCX | `tests/test_docx_validator.py::test_large_synthetic_document_validates_without_structural_errors` | 60 章 + 6 张表合成文档，验证 `validate_docx_package` 无 error、`build_document_report` 统计一致 |
| PDF 超时 | `tests/test_pdf.py::test_pdf_timeout_env_override` | 验证 `BIDCRAFT_PDF_TIMEOUT=600` 可覆盖模块级默认值；测试结束恢复模块状态 |

无新增 migration、无 API 路由/schema 改动、无前端改动。

## 3. 测试优先复现

先新增 50 章长文档测试并执行：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_writer_digest.py
```

结果：`1 failed, 4 passed`。失败显示 `第5章` 不在摘要中，根因是原实现先拼接近期正文再拼接全书索引，`text[:cap]` 直接把后半段索引截掉。

修复后重跑：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_writer_digest.py tests/test_batch3_generation_fixes.py tests/test_v0330_chapter_plan_consistency.py
```

结果：`26 passed, 1 warning`。

## 4. 验证命令与真实结果

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_pdf.py tests/test_docx_validator.py tests/test_writer_digest.py
# 13 passed, 2 skipped in 1.86s（skip = 本机无 LibreOffice）

.venv/bin/python -m pytest -o addopts= -q \
  tests/test_writer_digest.py tests/test_batch3_generation_fixes.py \
  tests/test_batch4_export_engine.py tests/test_batch4_golden.py \
  tests/test_docx_renderer.py tests/test_docx_validator.py tests/test_pdf.py \
  tests/test_v032_chapter_planning.py tests/test_v0330_chapter_plan_consistency.py \
  tests/test_v0330_generation_concurrency.py
# 83 passed, 3 skipped, 1 warning in 23.20s

.venv/bin/python -m pytest -o addopts= -q
# 2373 passed, 1008 skipped, 1 warning in 976.18s

.venv/bin/python -m ruff check bidcraft tests scripts
# All checks passed!

git diff --check
# clean
```

## 5. 未完成与风险

- NOT VERIFIED：真实招标文件的大章节结构、真实图表/跨页/页眉页脚版式；本机无 LibreOffice/pdfinfo/pdftotext，PDF golden 与真实渲染留给 CI/用户环境。
- DATA BLOCKED：真实跨章节数值冲突样本；当前仅验证工程摘要不会丢章节索引。
- `continuity_digest` 是提示词上下文压缩，不替代业务一致性判定；跨章节事实冲突仍需真实数据核验。
- PDF 超时是模块加载时读取环境变量；运行期修改需重载模块，测试已按此语义验证。

## 6. 回滚

删除 `tests/test_writer_digest.py` 新增用例、`tests/test_docx_validator.py` 新增用例、`tests/test_pdf.py` 新增用例；将 `writer.py` 的 `continuity_digest` 恢复为原 `text[:cap]` 实现。无数据库/API/前端状态需要回滚。
