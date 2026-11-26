# v0.33.1 Phase 6 验收报告：PostgreSQL、迁移与 CI

## 1. 结论

本阶段无业务代码变更。本地迁移可逆、Alembic 单 head、版本一致、历史迁移零修改、无测试删除；当前开发分支未推送，最终 SHA 的远程 CI 记为 NOT VERIFIED，不复用旧 SHA 的 9/9 结果作为本分支结论。

## 2. 真实 PostgreSQL 迁移

```bash
PG_URL='postgresql+psycopg://postgres:@127.0.0.1:5433/v0331_phase5'
BIDCRAFT_TEST_PG_URL="$PG_URL" .venv/bin/python -m pytest -o addopts= -q \
  tests/test_alembic.py tests/postgres/test_v032_migrations.py \
  tests/postgres/test_v0313_migration_permissions.py
# 17 passed, 1 warning in 15.63s
```

覆盖：SQLite head→0001→head 往返；PG 上 0013/0014/0015 链式升级、回退、数据保留、NULL 旧值兼容、权限与 finding IDOR。

## 3. 静态门禁

```text
ALEMBIC_HEADS ['0015_fact_confirmation']
VERSION_BACKEND 0.33.0
VERSION_WEB 0.33.0
PYPROJECT/UV_LOCK/WEB_LOCK_TOP 0.33.0
CI_JOBS 9 [docker-image, docx-render, postgres, python-lint, python-tests,
           security-dependency-audit, security-sast, security-secret-scan, web]
DOCKER_TAG_V0330 occurrences 7
git diff 3734997..HEAD -- migrations/versions/0001..0012: empty
git diff --diff-filter=D --name-only 3734997..HEAD -- tests web/src: empty
```

Alembic 单 head 保持；历史迁移未改写；无测试删除；CI 9 job 结构完整；docker image tag 与当前 0.33.0 版本一致。

## 4. 远程 CI 状态

最终开发 SHA `a69e8c2`（Phase 5 提交）未推送，故未取得该 SHA 的 GitHub Actions 记录。按任务要求不把旧 SHA `3734997` 的 9/9 成功记录冒充当前分支证据。

状态：NOT VERIFIED（需用户授权推送分支后另行取证；不得推 main）。

## 5. 残余风险

- 生产等价 PostgreSQL、PgBouncer、连接池权限与生产备份前置未验证。
- 新增迁移如后续阶段出现，必须重新执行本阶段全部检查并更新本报告。
- 本阶段 scratch DB `v0331_phase5` 不接触生产数据。

## 6. 回滚

无业务代码变更；回退本报告即可。PG scratch DB 可独立删除，不影响业务库。
