# v0.31.3 阶段 3：文件副作用与 DB fencing 边界报告

- 分支：`fix/v0.31.3-production-readiness`（阶段 0 `20266e5`、1 `fcf66dd`、2 `3f1f325` 之后）
- 目标：纠正「DB 事务回滚会一并回滚文件系统」的错误假设，用真实 PostgreSQL Job + 临时文件系统验证导出 zip / 配图文件的边界行为。
- 结论：**A PASS（有边界说明）**。

## 1. 核心事实

数据库事务只能回滚数据库写入；worker 已经落盘的 zip / 图片文件不会随事务回滚自动消失。本阶段不把文件一致性伪装成事务原子性，也不做大规模文件一致性重构。

## 2. 生产改动

| 文件 | 改动 | 原因 |
|---|---|---|
| `bidcraft/workers/export.py` | 新增 `_discard_partial_zip()`；在 `JobAborted` 与通用异常路径中清理本次生成但尚未交付的 `batch-<job_id[:8]>.zip` | 终态 fencing 失败时 zip 已写入磁盘；归档名含唯一 job_id，清理安全；重跑会重新生成 |
| `bidcraft/workers/export.py` | `zip_path` 初始化为 `None`，仅在成功 finalize 后保留 | 避免误删已成功交付的归档 |

图片侧不做文件删除：`generate_image` 在短事务中写入 `ImageAsset` 与图片文件；即使随后终态 fencing 失败，资产和文件保留，重试时会命中 `status=ready` 并跳过，属于幂等恢复。知识库索引只写 DB 表（`KbChunk` / 向量），无新增外部文件；重跑重建索引即可。

## 3. 测试证据

新增 `tests/postgres/test_v0313_file_side_effects.py`（真实 PG 16.2 + 临时目录 + 真实 Job/JobEvent）：

| 场景 | 断言 |
|---|---|
| 批量导出终态 fencing 失效 | zip 被显式清理；Job 保持 `running`、`result` 无内容、无 `done` 事件 |
| 批量导出正常完成 | zip 保留且内容含导出条目；Job `done`，`result.zip_path` 匹配，恰好 1 个 `done` 事件 |
| 配图终态 fencing 失效后重试 | `ImageAsset` 与图片文件保留；失败时无 `done`；重试命中已存在资产并 `skipped=1`，恰好 1 个 `done` 事件，文件仍存在 |

测试不 mock 核心 claim / fencing SQL；只把外部渲染/绘图函数替换为可控的落盘 stub，以隔离真实模型服务。

## 4. 验证命令与结果

```bash
.venv/bin/python -m ruff check bidcraft tests
BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/v0313_pg_uri.txt)" \
  .venv/bin/python -m pytest -o addopts="" -q \
  tests/postgres/test_v0313_file_side_effects.py
# 3 passed in 1.82s

BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/v0313_pg_uri.txt)" \
  .venv/bin/python -m pytest -o addopts="" -q \
  tests/postgres tests/test_postgres_integration.py \
  tests/test_v030_pg_isolation.py tests/test_worker_fencing_atomicity.py \
  tests/test_jobqueue_race_conditions.py
# 894 passed / 1 skipped in 71.56s
```

## 5. 残余风险

- 生产环境若使用对象存储或跨主机共享目录，`batch-<job_id[:8]>.zip` 的本地清理只作用于当前进程可见路径；部署需保证 output 目录一致。
- 图片生成在极少数 provider 已扣费但本地文件写入失败时仍可能产生外部费用；该风险属于 provider 侧补偿，不在本阶段范围。
- 本地 pgserver 是真实 PostgreSQL 16.2，但不是生产实例；CI 生产型 PG 集成仍应在阶段 6 复核。

## 6. 回滚

仅回退 `bidcraft/workers/export.py` 的 `_discard_partial_zip` / `zip_path` 清理逻辑并删除本阶段新增测试与报告；无 schema 变化。
