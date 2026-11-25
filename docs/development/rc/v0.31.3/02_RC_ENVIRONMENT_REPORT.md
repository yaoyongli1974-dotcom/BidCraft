# G2 RC 环境准备报告（v0.31.3）

## 1. 环境隔离原则

- RC 使用专用临时目录 `BIDCRAFT_HOME=/tmp/opencode/rc-v0313-home`，与生产/开发数据完全分离。
- RC PostgreSQL 使用独立 pgserver 临时集群 `/tmp/opencode/v0313-rc-pgdata`，未连接任何生产库。
- RC 环境文件 `/tmp/opencode/rc-v0313.env`（chmod 600）仅含本地临时口令与不可达假模型地址，不写入仓库、不进入报告。
- 未创建生产账号、未充值、未导入真实客户数据。

## 2. 本地能力核验

| 项目 | 结果 |
|---|---|
| Docker / docker-compose CLI | 不存在 |
| sudo | 不可用（sudo -n 失败） |
| psql / pg_dump | 不存在 |
| Python / venv | 3.13.5，项目 venv 可用 |
| psycopg | 3.3.9 |
| Node / npm | v26.13.1 / 11.20.0 |
| 临时 PG 集群 | pgserver PostgreSQL 16.2，独立 unix socket |
| PG URI | `/tmp/opencode/rc_pg_uri.txt`（本地临时文件，不入库） |

可复现命令：

```
command -v docker || true
command -v docker-compose || true
sudo -n true 2>/dev/null && echo SUDO_OK || echo SUDO_UNAVAILABLE
command -v psql || true
command -v pg_dump || true
```

## 3. RC PostgreSQL 就绪

启动独立临时集群：

```
/tmp/opencode/pgvenv312/bin/python -c "import pgserver,time,pathlib; d='/tmp/opencode/v0313-rc-pgdata'; db=pgserver.get_server(d); pathlib.Path('/tmp/opencode/rc_pg_uri.txt').write_text(db.get_uri().replace('postgresql://','postgresql+psycopg://')); print('RC_PG_READY', flush=True); time.sleep(3600)"
```

就绪 URI（脱敏后）：`postgresql+psycopg://postgres:****@/postgres?host=/tmp/opencode/v0313-rc-pgdata`。

## 4. 结论

- G2 的 PostgreSQL 部分：**A PASS**（独立临时真实 PG 集群已就绪并完成迁移验证，见 `04_DATABASE_MIGRATION_REPORT.md`）。
- G2 的 Docker/Compose 本地部署部分：**D NOT VERIFIED**（本地无 Docker CLI、无 compose、无 sudo，无法在本机启动容器）。
- 远端 CI 的 docker job 已在 G1 最终 SHA `64ab219` 上成功完成镜像构建、invariants、compose 静态校验与 `/health/live` 容器冒烟；该证据属于 CI 环境而不是本地 RC 容器，最终 Go/No-Go 必须如实列为条件项。
