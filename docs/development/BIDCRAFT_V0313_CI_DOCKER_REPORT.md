# v0.31.3 阶段 6：CI / 测试 / Docker 生产门禁报告

- 分支：`fix/v0.31.3-production-readiness`（阶段 0～5 已提交，HEAD `bdf4573`）
- 目标：把阶段 1～5 的真实 PG、文件副作用、迁移/权限证据接入 CI，并让 Docker/Compose 校验从"静态字符串"升级为真实运行门禁
- 结论：**B 有条件通过**：本地复现并修复了 3 个真实缺口；CI/Docker 运行需远端 workflow 最终确认

## 1. 先复现的缺口（未先改代码）

| 编号 | 复现命令 | 复现结果 |
|---|---|---|
| G1 | `env -u BIDCRAFT_TEST_PG_URL .venv/bin/python -m pytest -o addopts="" -q tests/postgres/` | `exit=1`：**67 ERROR + 801 skipped**。`conftest.py` 里的模块级 `pytestmark` 对测试模块无效，fixture 仍以空 URL 调用 `reset_engine_for_tests("")` |
| G2 | 检查 `.github/workflows/ci.yml` 的 postgres job | 只运行 `tests/test_postgres_integration.py`；阶段 1～5 新增的 6 个 `tests/postgres/test_v0313_*.py`（868 个用例）从未在真实 PG 下进入 CI |
| G3 | 检查 `deploy/docker-compose.yml` 与 `deploy/.env.example` | `BIDCRAFT_ENV` / `BIDCRAFT_DB_URL` / `BIDCRAFT_TRUST_PROXY` / `BIDCRAFT_SECRET_KEY` / 限流 / 日志 / 备份目录等生产变量在 `.env.example` 中有定义，但 compose 的 `environment:` 未透传；部署后回退 `dev` + SQLite |

## 2. 修复

### 2.1 `tests/postgres/conftest.py`

- 删除无效的模块级 `pytestmark`，改为在 `pg_engine` fixture 入口显式 `pytest.skip("需要 BIDCRAFT_TEST_PG_URL（专用测试库）")`。
- 未设 URL 时整目录干净跳过（868 skipped，0 ERROR）；设 URL 时 fixture 照常断言 `dialect.name == "postgresql"`。

### 2.2 `.github/workflows/ci.yml`

- `postgres` job 先 `apt-get install postgresql-client`，让 `pg_dump` 可用。
- 测试命令改为 `tests/postgres tests/test_postgres_integration.py tests/test_v030_pg_isolation.py tests/test_worker_fencing_atomicity.py tests/test_jobqueue_race_conditions.py`，并断言 `tests >= 900`、`skipped == 0`、`failures == 0`。
- `docker-image` job 的临时标签由 `v030-check` 改为 `v0313-check`；新增真实 `docker compose -f deploy/docker-compose.yml config -q` 与容器启动后的 `/health/live` 轮询 smoke（最多 60s），失败即 CI 失败。
- Compose 静态检查新增"必需生产变量"集合断言，避免 `.env.example` 与 `environment:` 再次漂移。
- 三个安全 job（pip/npm audit、Bandit、Gitleaks）移除 `continue-on-error: true`，full-tree `npm audit` 移除 `|| true`；安全门禁不再可静默失败。

### 2.3 `deploy/docker-compose.yml`

显式透传 `.env.example` 中记录的生产变量：`BIDCRAFT_ENV`、`BIDCRAFT_DB_URL`、`BIDCRAFT_TRUST_PROXY`、`BIDCRAFT_PUBLIC_ORIGIN`、`BIDCRAFT_SECRET_KEY`、`BIDCRAFT_BACKUP_DIRS`、`BIDCRAFT_RATE_*`、`BIDCRAFT_LOG_LEVEL` / `BIDCRAFT_LOG_FORMAT`；继续遵守"不启用 `env_file:`"的既定策略。

## 3. 验证

| 检查 | 结果 |
|---|---|
| `env -u BIDCRAFT_TEST_PG_URL pytest -q tests/postgres/` | **868 skipped，0 ERROR**（原 67 ERROR 已消除） |
| 真实 PG 全量套件 | **901 passed / 1 skipped / 1 warning in 76.83s**（本地缺 runner 侧 pg_dump；CI 已安装，`skipped==0` 门禁生效） |
| `ruff check bidcraft tests scripts` / `git diff --check` | 通过 |
| YAML 解析 / CI jobs | 9 个 job；security `continue-on-error` 为 0；compose 必需环境变量缺失为 0 |
| Docker 镜像运行 smoke | 本地无 Docker，**未验证**；CI `docker-image` job 已加入真实启动与探针轮询 |

## 4. 四状态结论

- **A PASS**：G1 conftest 跳过守卫；G2 CI 纳入全部 v0.31.3 PG 用例并强断言；G3 compose 生产变量透传与 CI 漂移断言；security job 全部改为强制门禁。
- **B CONDITIONAL PASS**：真实 PG 兼容本地 pgserver 16.2；CI 使用 `postgres:16-alpine` 服务，需远端 workflow 运行确认。
- **C BLOCKED**：无（本阶段无因风险暂停项）。
- **D NOT VERIFIED**：Docker 容器运行态 / Compose `config -q` / `/health/live` smoke 本地无 Docker，只能由 CI `docker-image` job 验证；尚未在本分支运行远端 CI。

## 5. 残余风险

- CI 的 PG 服务是临时测试库，不代表生产 PostgreSQL 的参数/权限/备份策略；阶段 8 只做上线前验收，不做生产部署。
- 安全 job 改为强制后，若工具源临时不可用会导致 CI 红；这是刻意不放宽门禁的选择。
- 真实脱敏招标文件仍待用户提供；质量件指标继续「未测量」。

## 6. 回滚

回退 `.github/workflows/ci.yml`、`tests/postgres/conftest.py`、`deploy/docker-compose.yml` 与本报告即可；无迁移、无 schema 变化，无生产数据写入。
