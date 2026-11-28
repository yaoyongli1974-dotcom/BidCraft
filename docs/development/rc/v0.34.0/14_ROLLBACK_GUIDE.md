# BidCraft v0.34.0 回滚指南（RC）

> 状态：**NOT VERIFIED**。本指南只给出可执行流程；本次专项没有对生产库做任何回滚/恢复操作。任何回滚前必须先做当前状态备份。

## 1. 决策矩阵

| 问题范围 | 回滚层级 | 是否动数据库 |
|---|---|---|
| 前端终态/双击/健康标签显示异常 | 仅前端产物 + 对应 web 源码 | 否 |
| CI 配置或文档错误 | 仅 `.github/workflows/ci.yml` / 文档 | 否 |
| 迁移 0018 或后端 v0.34.0 逻辑导致数据/服务异常 | 整版本回滚：旧镜像 + 用升级前备份恢复 | 是（先备份当前） |
| 只有队列/任务显示异常，服务与数据正常 | 优先按运维手段处理，不急回滚 | 否 |

始终先回答「升级前备份是否可 `backup-verify` 通过」；不通过则停止回滚，先恢复备份可读性。

## 2. 回滚前强制快照

```bash
docker exec bidcraft bidcraft backup
docker exec bidcraft bidcraft backup-verify /data/backups/bidcraft-YYYYmmdd-HHMMSS.zip
docker exec bidcraft bidcraft backup --list
```

记录快照路径、SHA-256、操作人、回滚原因。不要删除升级前那份备份。

## 3. 仅回滚前端/CI/文档

```bash
# 前端生命周期改动（本专项）
git restore web/src/hooks/watchJob.ts web/src/pages/BillingPage.tsx \
  web/src/pages/admin/AdminHealthTab.tsx web/src/lib/types.ts \
  web/src/hooks/watchJob.test.ts web/src/pages/admin/AdminHealthTab.test.tsx \
  web/src/pages/tabs/QualityTab.evidence.test.tsx \
  web/src/pages/tabs/QualityTab.uxFix.test.tsx
rm web/src/pages/BillingPage.test.tsx

# CI 配置与备份/恢复手册
git restore .github/workflows/ci.yml docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md

rm docs/development/rc/v0.34.0/10_TEST_REPORT.md \
   docs/development/rc/v0.34.0/11_CI_REPORT.md \
   docs/development/rc/v0.34.0/13_DEPLOYMENT_GUIDE.md \
   docs/development/rc/v0.34.0/14_ROLLBACK_GUIDE.md
```

重新构建并替换前端产物/镜像后验证 `npm run typecheck && npm run lint && npm test -- --run && npm run build`（详见 `10_TEST_REPORT.md`）。恢复的行为：cancelled 任务无限轮询、订单终态后仍可能继续轮询、双击可创建重复订单、`workers_running=false` 被误标为生产故障。

## 4. 整版本回滚（涉及数据库）

1. 完成第 2 节快照；低峰停服：`docker compose down`。确认无写库连接。
2. 保留当前数据目录副本：`cp -a /opt/bidcraft/data /opt/bidcraft/data.before-rollback`。
3. 用升级前备份恢复到隔离环境先验（完整步骤见 `docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md`）：
   - SQLite：解压 `bidcraft.db`、`secret.key`、数据目录到临时 `BIDCRAFT_HOME`，`bidcraft doctor` 验证可读；
   - PostgreSQL：把 `dump.sql` 导入临时库，核对表/行数与 `manifest.json`，`BIDCRAFT_DB_URL=<临时库> bidcraft doctor` 验证迁移链。
4. 确认目标 head 与旧版本一致（动态计算，不写死 revision）：
   ```bash
   uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; cfg = Config(); cfg.set_main_option('script_location', 'bidcraft/migrations'); print(ScriptDirectory.from_config(cfg).get_heads())"
   ```
   若旧代码在另一个 checkout，必须在该 checkout 内计算 head；不要拿 v0.34.0 的 head 去 stamp 旧库。
5. 覆盖回生产 `BIDCRAFT_HOME`（含同代 `secret.key`），切回旧镜像 `image: bidcraft:<上一版本>`，`docker compose up -d`。
6. 验证：`bidcraft version`、`bidcraft doctor`、`curl -fsS /health/live`、`curl -fsS /health/ready`；登录 + 项目/章节/导出冒烟；核对余额与流水（v0.34.0 的退款/租约逻辑已回滚，不应再产生新的退款事件）。
7. 留痕：回滚原因、快照路径、恢复耗时、验证结果、操作人。

**注意**：优先用备份恢复，不依赖迁移 `downgrade`。历史迁移在 SQLite 下可能依赖 `inspect(bind)`，离线 SQL 模式不适用；且 v0.34.0 迁移（如 0018）的 downgrade 由对应任务负责，本阶段未验证。

## 5. 回滚后的反向验证

```bash
# web 侧
cd web && npm run typecheck && npm run lint && npm test -- --run && npm run build
# CI 侧
.venv/bin/python - <<'PY'
import yaml
from pathlib import Path
doc = yaml.safe_load(Path('.github/workflows/ci.yml').read_text())
assert len(doc['jobs']) == 9
print('CI jobs restored:', list(doc['jobs']))
PY
```

## 6. 残余风险

- 整版本回滚会丢掉回滚窗口内产生的新数据；必须用第 2 节的最新快照把损失控制在已知范围。
- `secret.key` 必须与数据库快照同代，否则 API Key 解密与 OAuth state 校验会失败。
- 若并行任务已提交迁移 0018，仅回滚前端/CI 而不回滚后端镜像时，数据库 schema 仍是新版本；这是允许的（迁移向前兼容），但不要再运行 v0.34.0 的并发/退款逻辑验证。

## 7. 结论

回滚流程 **CONDITIONAL**（文档级完整，真实回滚 **NOT VERIFIED**）。发版决策 **CONDITIONAL GO**：先满足 `11_CI_REPORT.md` 中的远程 CI/PG/版本号门禁，再按 `13_DEPLOYMENT_GUIDE.md` 发布。
