# 真实招标文件回归集（仅用户提供）

本目录用于放置**脱敏后的真实招标文件**及其人工标注。实际文件与清单
`dataset.json` 均被 `.gitignore` 排除，禁止提交到仓库。仓库只保留本说明和
`dataset.example.json` 示例结构。

## 放置步骤

1. 复制 `dataset.example.json` 为 `dataset.json`（不会进版本库）。
2. 将脱敏文件放在本目录或子目录；清单中的 `path` 必须是相对路径且不能含 `..`。
3. 为每个文件填写 `sha256`（可用 `sha256sum <file>` 生成）与人工标注：
   - `required_clauses`：必须被识别的实质性条款原文片段数组；
   - `required_values`：关键数值/等级/日期及其单位数组；
   - `expected_verdicts`：条款片段到期望判定状态的映射。
4. 运行 `pytest tests/test_tender_dataset_v031.py` 验证契约；数据未就绪时测试
   报告“未就绪”，不会把合成夹具冒充真实样本。

## 安全要求

- 文件名与内容不得包含公司全称、统一社会信用代码、联系人、手机号、邮箱、
  银行账号；这些信息需脱敏后再放入。
- 不要修改 `bidcraft/migrations/versions/` 下的历史迁移来适配本数据集。
- 数据仅用于本地/CI 回归，不上传、不推送、不用于生产库。
