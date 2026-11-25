# G8 安全验收报告（RC v0.31.3）

## 1. 结论

| 项目 | 状态 |
|---|---|
| RC 隔离环境鉴权/审计/配置安全单元测试 | A PASS |
| 真实 PG 权限与跨项目隔离 | A PASS |
| CI 安全作业（依赖审计 / Bandit / secret scan） | A PASS |
| 生产账号、真实密钥、真实租户数据 | D NOT VERIFIED |

## 2. 实测证据

### 2.1 SQLite 安全测试（干净环境）

第一次执行时在污染环境中全局导出了 `BIDCRAFT_DB_URL`，`tests/conftest.py` 虽重置为 SQLite，`db._migration_guard` 仍读取到 PG URL，产生 `pg_advisory_lock` 错误；这是调用方式错误，不是应用缺陷。清除相关 env 后：

```
env -u BIDCRAFT_DB_URL -u BIDCRAFT_ENV -u BIDCRAFT_MODEL_BASE_URL \
    -u BIDCRAFT_MODEL_NAME -u BIDCRAFT_TEST_PG_URL \
    -u BIDCRAFT_ADMIN_PASSWORD -u BIDCRAFT_HOME \
    .venv/bin/python -m pytest -o addopts="" -q \
    tests/test_sec_fix_audit.py tests/test_sec_fix_http.py tests/test_sec_fix_config.py
```

结果：**15 passed, 1 warning in 6.83s**。覆盖审计留痕、HTTP 安全中间件、限流、CSRF、配置校验等。

### 2.2 RC 真实 PostgreSQL 权限/IDOR

在 RC PG（TCP `127.0.0.1:5432`）以隔离数据执行：

```
set -a; . /tmp/opencode/rc-v0313.env; set +a
export BIDCRAFT_DB_URL='postgresql+psycopg://postgres:@127.0.0.1:5432/postgres'
export BIDCRAFT_TEST_PG_URL="$BIDCRAFT_DB_URL"
.venv/bin/python -m pytest -o addopts="" -q tests/postgres/test_v0313_migration_permissions.py
```

结果：**3 passed, 1 warning in 4.02s**。覆盖：迁移往返后 schema 一致、非法成员角色不授权、`_require_write` 403、跨项目 finding 查询/更新返回 404 且无越权审计。

### 2.3 CI 安全作业

CI Run `45570895467` 中 `security (dependency audit)`、`security (bandit SAST)`、`security (secret scan)` 均 SUCCESS，且已移除 `continue-on-error`。

## 3. 未验证项

- 未使用生产账号、生产密钥、真实客户数据或真实租户配置。
- RC 使用假模型地址；模型供应商凭据与出站白名单未在生产网络验证。
- 真实 Nginx/反向代理的 `BIDCRAFT_TRUST_PROXY` / `BIDCRAFT_PUBLIC_ORIGIN` 组合未验证。

## 4. 结论

RC 隔离环境下的安全与权限控制满足上线评审条件；生产环境的账号、密钥、网络边界与代理配置仍需在部署窗口单独验收。
