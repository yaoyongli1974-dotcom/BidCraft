# 真实招标文件回归校准

`bidcraft calibrate` 用真实招标文件目录跑一遍「解析 → 字段/表格/材料抽取」，输出字段命中率、
必填缺失和 OCR 需求，用来判断是该补别名、补规则包还是需要 OCR。命令**只读文件系统**：
不写数据库、不创建项目、不调用模型。

## 命令

```bash
# 扫描目录下的 PDF/DOCX/TXT/MD/XLSX（默认不递归）
bidcraft calibrate /path/to/tenders

# 递归扫描子目录，并保存 JSON 报告
bidcraft calibrate /path/to/tenders --recursive --output /tmp/calibrate-report.json
```

- 支持后缀：`.pdf`、`.docx`、`.txt`、`.md`/`.markdown`、`.xlsx`/`.xlsm`。
- 单个文件解析或抽取失败只记入该文件 `error`，命令整体仍以 `0` 退出；目录不存在时以 `1` 退出并提示中文错误。
- 每个文件会按规则包 `parse.keywords` 自动选择行业包（弱电、消防、安装、土建、暖通、装饰）；
  没有命中时使用通用事实包 `common-bid-facts`。
- PDF 的 OCR 行为遵循 `BIDCRAFT_OCR` 与 OCR 相关环境变量；扫描件会标记 `needs_ocr`。

## 报告结构

```json
{
  "root": "/path/to/tenders",
  "recursive": false,
  "per_file": [
    {
      "file": "weakcurrent-tender.md",
      "pages": 1,
      "tables": 0,
      "materials": 0,
      "facts_filled": 23,
      "facts_required": 16,
      "facts_missing_required": 0,
      "warnings": [],
      "needs_ocr": false,
      "error": null
    }
  ],
  "aggregate": {
    "files": 1,
    "ok": 1,
    "failed": 0,
    "field_hits": {"project.name": 1},
    "required_hit_rate": 1.0,
    "avg_materials": 0.0,
    "ocr_files": 0
  }
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `per_file[].file` | 相对扫描根目录的路径 |
| `per_file[].pages` | 解析出的页数/工作表数 |
| `per_file[].tables` | 解析出的结构化表格数（Markdown/TXT 通常为 0） |
| `per_file[].materials` | 按表头识别出的材料/工程量行数 |
| `per_file[].facts_filled` | 所选规则包中已抽到值的事实数 |
| `per_file[].facts_required` | 所选规则包中的必填事实数 |
| `per_file[].facts_missing_required` | 必填但未抽到的事实数 |
| `per_file[].warnings` | PDF/OCR/空白页等非致命提示 |
| `per_file[].needs_ocr` | 是否未提取到文本、需要 OCR |
| `per_file[].error` | 解析失败原因；成功为 `null` |
| `aggregate.field_hits` | `{字段 key: 命中文件数}`，只列出至少命中 1 次的字段 |
| `aggregate.required_hit_rate` | 所有成功文件的「已填必填事实 / 必填事实」汇总，`0.0`–`1.0` |
| `aggregate.avg_materials` | 成功文件的平均材料行数 |
| `aggregate.ocr_files` | `needs_ocr=true` 的文件数 |

CLI 会额外打印汇总表、每文件一行和字段命中 Top 10；`--output` 写入完整 JSON。

## 看到结果后怎么处理

1. **字段命中率低**：先看 `field_hits` 缺哪些 key，再检查源文件里的实际标签。
   - 通用同义标签（如「计划工期」「缺陷责任期」「磋商保证金」）加在
     `bidcraft/parsing/extract.py` 的 `FIELD_ALIAS_AUGMENT`，只追加、不覆盖规则包原有别名。
   - 行业专属字段或解析规则应改对应规则包 YAML，并跑 `bidcraft packages --validate`。
2. **`facts_missing_required` 高**：核对是否真的缺失，或标签与规则包不匹配；
   必要时在「事实台账」中人工补齐后生成，不能猜测。
3. **`needs_ocr=true`**：扫描件需要安装 `pdftoppm` + `tesseract` 中文语言包，或提供可复制文本的 PDF。
4. **`error` 非空**：确认文件未损坏、后缀正确；旧版 `.doc`/`.xls` 需另存为 `.docx`/`.xlsx` 或 PDF。

## fixtures 与真实件

`tests/fixtures/regression/` 下的 `weakcurrent-tender.md`、`fire-tender.md` 是**仿真件**，
用于回归测试别名与抽取口径；`boq-weakcurrent.xlsx` 由测试用 openpyxl 动态生成，不入库。
真实招标文件涉及商业信息，需由用户自行放入独立目录运行校准，不要提交到仓库。

运行内置回归：

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/test_calibrate.py
```
