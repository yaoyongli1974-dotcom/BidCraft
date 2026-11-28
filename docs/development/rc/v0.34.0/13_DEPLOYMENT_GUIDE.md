# BidCraft v0.34.0 部署指南（RC）

> 状态：**NOT VERIFIED**。本指南由代码/配置层面编写；本次专项未部署生产、未操作生产库、未触发远程 CI。所有命令均为待执行的操作手册，不代表已执行。

## 1. 前置门禁（缺一不可）

1. 版本号收敛：`bidcraft/__init__.py`、`pyproject.toml`、`web/package.json`、`web/package-lock.json` 均为 `0.34.0`，`docs/release-notes/v0.34.0.md` 与 `CHANGELOG.md` 的 `## v0.34.0` 已落地。当前工作区仍是 `0.33.2`，本阶段按所有权未改。
2. 远程 CI 9/9 通过（见 `11_CI_REPORT.md`）；其中 PG job 必须报告 `tests >= 900, skipped == 0, failures == 0`，且 JUnit 中含 `test_v0340_db_concurrency.py`。
3. 并行任务提供的迁移 `0018_file_checksums.py` 与 v0.34.0 测试文件已提交；Alembic 恰好单 head。
4. 升级前备份完成且 `backup-verify` 通过（见 `docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md`）。

本地可复核 head（动态，不硬编码 revision）：

```bash
uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; cfg = Config(); cfg.set_main_option('script_location', 'bidcraft/migrations'); print(ScriptDirectory.from_config(cfg).get_heads())"
# 本次读到 ['0018_file_checksums']；以仓库当前计算值为准
```

## 2. 构建与镜像校验

```bash
cd /path/to/bidcraft
docker build -t bidcraft:0.34.0 .
docker inspect -f '{{.Config.User}}' bidcraft:0.34.0          # 非 root
docker run --rm --entrypoint sh bidcraft:0.34.0 -c 'pg_dump --version'
docker run --rm --entrypoint sh bidcraft:0.34.0 -c 'bidcraft version'   # 应为 0.34.0
```

镜像内已内置 `web_dist`、CJK 字体、LibreOffice、`postgresql-client`；`/health/live` 是 liveness，`/health/ready` 是 readiness（查 DB + 模型 + web_dist）。

## 3. 升级前备份（强制）

```bash
docker exec bidcraft bidcraft backup
docker exec bidcraft bidcraft backup-verify /data/backups/bidcraft-YYYYmmdd-HHMMSS.zip
docker exec bidcraft bidcraft backup --list
```

记录 zip 路径、字节数/SHA-256、操作人。任何迁移/重启/磁盘维护前至少保留这一份。

## 4. 发布步骤

1. 低峰窗口停服，给在途任务留优雅退出时间：`docker compose down`（compose 已配置 `stop_grace_period: 45s`）。确认没有写库连接。
2. 替换 `deploy/docker-compose.yml` 中 `image: bidcraft:0.34.0`；`.env` 只做 `${VAR}` 插值，所有生产变量必须在 `environment` 显式透传（清单见 CI 的 compose 加固断言）。
3. 启动：`docker compose up -d`。
4. 应用首次 `init_db()` 会把库带到当前 Alembic head；不要在未备份的库上手工 `stamp` 或跳过迁移。
5. 轮询探针：

```bash
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/health/live && break
  sleep 2
done
curl -fsS http://127.0.0.1:8000/health/ready
# 期望 {"status":"ready","db":"ok","model_configured":true,"web_ready":true}
```

`BIDCRAFT_OFFLINE=1` 会豁免模型依赖；生产若配置了对话模型，应以真实模型配置让 readiness 返回 200，不要用离线模式长期掩盖模型故障。

## 5. 发布后验证

```bash
docker exec bidcraft bidcraft version        # 0.34.0
docker exec bidcraft bidcraft doctor         # DB / 模型 / 环境检查
docker logs bidcraft --tail 200              # 无迁移失败/重复退款日志
```

业务冒烟（人工，逐项记录）：

- 登录 → 打开既有项目 → 查看章节与核对结果；确认余额与升级前备份对账一致。
- 触发一次短后台生成任务，观察前端直到 `done`/`failed`/`cancelled` 终态停止轮询，且成功/失败提示与实际状态一致。
- 充值订单：确认 `paid/closed/expired/failed` 后页面不再轮询、双击不会创建重复订单（前端行为见 `10_TEST_REPORT.md`）。
- 超管后台「服务健康」：确认 `workers_running=false` 只显示「未启动内置工作进程（可为 API-only 部署）」，不再被误判为生产故障；只有 `stale_running_jobs>0` 才告警。

## 6. 已知风险与观察项

- `workers_running` 只反映当前进程的内置 worker 池；多实例/外部 worker 部署下为 `false` 属正常，请通过 `queued_jobs`、`oldest_queued_age_seconds`、`stale_running_jobs` 判断队列健康。
- 版本 bump 与发版说明尚未落地，若此时发布会直接触发 CI 版本一致性失败。
- 一次 web 全量测试中曾出现未复现的单用例失败（`10_TEST_REPORT.md`），发布前以最新一次全绿为准并保留日志。

## 7. 回滚

出现阻塞问题时按 `14_ROLLBACK_GUIDE.md` 执行：先备份当前状态，再决定「仅回滚前端/配置」还是「整版本回滚 + 数据库恢复到升级前快照」。

## 8. 结论

部署准备度 **CONDITIONAL**：代码与配置就绪，远程 CI / 真实部署 **NOT VERIFIED**。建议 **CONDITIONAL GO**，待第 1 节门禁全部满足后执行。
