# 真实件基线目录

本目录存放**真实招标文件回归基线**与索引。基线 JSON 里只有聚合指标和可选的
文件指纹清单（相对路径 + sha256 + 字节数），**不含任何招标文件正文**，因此可以
提交到仓库；真实招标文件本身含商业信息，请放在仓库外的独立目录（仓库已忽略
`real-tenders/`），不要提交。

## 文件

- `index.json`：`bidcraft calibrate --baseline-index` / `bidcraft baselines --add`
  维护的标签索引，只记录 `id`、`label`、`path`、`saved_at`、`sha256`、`size`。
- `*.json`：每批真实件一份基线（schema=1），由 `bidcraft calibrate --save-baseline`
  生成。

## 新建一批基线

```bash
# 真实件放仓库外，例如 /data/tenders/2026-11
bidcraft calibrate /data/tenders/2026-11 --recursive \
  --save-baseline baselines/real-2026-11.json \
  --label real-2026-11 \
  --note "第 3 批公开招标文件" \
  --baseline-index baselines/index.json

# 核对样本集没被换过
bidcraft baselines --verify real-2026-11 --root /data/tenders/2026-11
```

## 日常回归

```bash
# 按标签比对（解析能力退化 → exit 1）
bidcraft calibrate /data/tenders/2026-11 \
  --baseline-index baselines/index.json --compare-baseline real-2026-11

# 样本集指纹核对（文件被替换/移除 → exit 1）
bidcraft baselines --verify real-2026-11 --root /data/tenders/2026-11
```

基线 schema 保持 1：旧基线（只有 `schema`/`saved_at`/`aggregate`）照常可读；新增的
`provenance` 只是可选溯源信息。比对始终只看 `aggregate`，与机器路径无关，便于跨
机器复现。
