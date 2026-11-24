# BidCraft v0.30.0 阶段 5 风险报告：DOCX 与资源边界

- 状态：用户已批准（`G2/G3/G6` 实施，`G8` 暂缓）
- 审计来源：只读审计 ses_df33f6e03ffe7MXeqFz5O20hld（HEAD e409371 + 阶段 4 未提交代码）
- 约束：不改历史迁移、不删测试、不降低门禁、不推送 main、不发版。

## 1. 已在基线完成，不重复实施

- CI `docx-render` job（阶段 1）：`fonts-noto-cjk + libreoffice-writer + poppler-utils`，`BIDCRAFT_DOCX_REQUIRE_RENDER=1`，断言 0 skipped/0 failures。
- Golden 严格模式、字体映射测试、包完整性校验、确定性字节、超链接、专业引擎接线均已存在。

## 2. 无需额外批准的内部修复

- G1：表格校验区分 layout/form/data/content（内部 IR/OXML marker，不改 HTTP 契约）。
- G4：页面查找预计算 compacted pages，消除 O(candidates × pages × page_len)。
- G5：统一图片识别 pattern，让 IR 与导出共用同一限制口径。
- G7：将 guard 提升到 `render_document` / `from_markdown_sections`，避免直接调用绕过。

## 3. 需批准的新限制（已批准）

| 编号 | 风险 | 决定 | 边界 |
|---|---|---|---|
| G2 | 上传 ZIP 解压 bombs（压缩 50MB 可膨胀为数 GB，API worker OOM） | 批准 | `load_document` 读取前累计 `ZipFile.infolist().file_size`，超过 `MAX_UNCOMPRESSED_UPLOAD_BYTES`（建议 512MB）抛 `ValueError` → 上传接口 400；测试用稀疏 zip bomb。 |
| G3 | 解析文本/表/页无上限，GB 级写入 `SourceFile.text_content` | 批准 | 增加 `MAX_PARSED_CHARS` / `MAX_PARSED_TABLES` / `MAX_PARSED_PAGES`，在 `parse_documents` 抽取前截断或拒绝，保证后续 extract 有界；测试覆盖超限。 |
| G6 | 导出正文无总字符上限（5000×200KB ≈ 1GB，DOCX 重写 ZIP 时 OOM） | 批准 | `export/docx.py::_guard_limits` 增加 `MAX_TOTAL_BODY_CHARS` / `MAX_NODE_BODY_CHARS`，超限返回明确错误（HTTP 500 + 可读信息）而非崩溃；测试覆盖。 |

## 4. 暂缓

- G8：PDF 导出超时按文档规模自适应或迁到队列。涉及 API 行为/队列语义变更，用户决定暂缓；保留当前 240s 默认 + fallback，登记为已知限制。

## 5. 验收要求

- 所有新增限制必须有明确常量与错误信息，且测试覆盖“边界内成功 / 超限拒绝”。
- 不得通过跳过或放宽现有 golden/CI 断言来规避资源问题。
