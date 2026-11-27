#!/usr/bin/env bash
# BidCraft 本地测试环境（隔离，不触碰生产数据）
# 用法：./scripts/local_test_env.sh <命令> [pytest 额外参数]
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 持久化到用户数据目录：/tmp 在重启后会清空，不能再作为测试数据默认位置。
STATE_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/bidcraft-local-test"
HOME_DIR="${STATE_DIR}/home"
ENV_FILE="${STATE_DIR}/env"
PG_DATA="${STATE_DIR}/pgdata"
PG_URI_FILE="${STATE_DIR}/pg-uri.txt"
PG_VENV="${STATE_DIR}/pgvenv"
PG_PY="${PG_VENV}/bin/python"
# 兼容旧版 /tmp 布局，仅在首次迁移时读取。
LEGACY_HOME_DIR="/tmp/opencode/bidcraft-local-test-home"
LEGACY_ENV_FILE="/tmp/opencode/bidcraft-local-test.env"
LEGACY_PG_DATA="/tmp/opencode/bidcraft-local-pgdata"
LEGACY_PG_URI_FILE="/tmp/opencode/bidcraft-local-pg-uri.txt"
UV="${HOME}/.local/bin/uv"
[ -x "$UV" ] || UV="uv"

cd "$ROOT"

need_backend() {
  if [ ! -x .venv/bin/python ]; then
    echo "[init] 创建 Python 虚拟环境..."
    "$UV" sync --frozen --extra dev --extra api --extra postgres
  fi
}

need_web() {
  if [ ! -d web/node_modules ]; then
    echo "[init] 安装前端依赖..."
    (cd web && npm ci --no-audit --no-fund)
  fi
}

source_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "缺少环境文件：$ENV_FILE；请先执行 init" >&2
    exit 2
  fi
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
}

migrate_legacy_env() {
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  if [ ! -f "$ENV_FILE" ] && [ -f "$LEGACY_ENV_FILE" ]; then
    cp "$LEGACY_ENV_FILE" "$ENV_FILE"
    # 旧 env 里 BIDCRAFT_HOME 指向 /tmp；迁移后必须指向持久目录。
    python3 - "$ENV_FILE" "$HOME_DIR" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
home = sys.argv[2]
lines = []
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith("BIDCRAFT_HOME="):
        line = f"BIDCRAFT_HOME={home}"
    lines.append(line)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    chmod 600 "$ENV_FILE"
    echo "[init] 已从 /tmp 旧位置迁移环境文件到：$ENV_FILE"
  fi
  if [ ! -d "$HOME_DIR" ] && [ -d "$LEGACY_HOME_DIR" ]; then
    cp -a "$LEGACY_HOME_DIR" "$HOME_DIR"
    echo "[init] 已从 /tmp 旧位置迁移测试数据到：$HOME_DIR"
  fi
  if [ ! -f "$PG_URI_FILE" ] && [ -f "$LEGACY_PG_URI_FILE" ]; then
    cp "$LEGACY_PG_URI_FILE" "$PG_URI_FILE"
    chmod 600 "$PG_URI_FILE"
  fi
}

pg_setup() {
  if [ -x "$PG_PY" ]; then
    return 0
  fi
  echo "[pg-setup] 创建 PostgreSQL 运行器：$PG_VENV"
  mkdir -p "$STATE_DIR"
  "$UV" venv --python 3.12 "$PG_VENV"
  "$UV" pip install --python "$PG_PY" pgserver
}

init_env() {
  migrate_legacy_env
  need_backend
  need_web
  mkdir -p "$HOME_DIR"
  chmod 700 "$HOME_DIR"
  if [ ! -f "$ENV_FILE" ]; then
    local pass
    pass="$($ROOT/.venv/bin/python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
    cat > "$ENV_FILE" <<EOF
BIDCRAFT_ENV=test
BIDCRAFT_HOME=${HOME_DIR}
BIDCRAFT_OFFLINE=1
BIDCRAFT_MODEL_BASE_URL=http://127.0.0.1:9/v1
BIDCRAFT_MODEL_NAME=local-test-model
BIDCRAFT_ADMIN_PASSWORD=${pass}
EOF
    chmod 600 "$ENV_FILE"
    echo "[init] 已创建隔离测试环境：$ENV_FILE（权限 600）"
  else
    echo "[init] 复用已有环境：$ENV_FILE"
  fi
  echo "[init] 后端：$(.venv/bin/python --version)"
  echo "[init] 前端：$(cd web && node --version && npm --version)"
  echo "[init] 真实招标件/qrels/专家评分未提供，对应测试按 DATA BLOCKED 处理"
}

backend() {
  source_env
  .venv/bin/python -m pytest -o addopts="" -q "$@"
}

backend_quick() {
  source_env
  .venv/bin/python -m pytest -o addopts="" -q \
    tests/test_api_package.py \
    tests/test_alembic.py \
    tests/test_jobqueue.py \
    tests/test_worker_reliability_matrix.py \
    tests/test_v0331_vector_readiness.py \
    tests/test_v0331_phase4_security.py \
    "$@"
}

web_checks() {
  (cd web && npm run typecheck && npm run lint && npm test -- --run && npm run build)
}

pg_start() {
  pg_setup
  if [ ! -x "$PG_PY" ]; then
    echo "未找到本地 PostgreSQL 运行器：$PG_PY" >&2
    return 1
  fi
  "$PG_PY" - <<PY
from pathlib import Path
import pgserver
pgdata = Path("${PG_DATA}")
srv = pgserver.get_server(pgdata, cleanup_mode=None)
exists = srv.psql("SELECT 1 FROM pg_database WHERE datname='bidcraft_local_test'") or ""
if not any(line.strip() == "1" for line in str(exists).splitlines()):
    srv.psql("CREATE DATABASE bidcraft_local_test")
uri = srv.get_uri("bidcraft_local_test")
Path("${PG_URI_FILE}").write_text(uri)
print("PG_READY", uri)
PY
}

pg_stop() {
  pg_setup
  if [ ! -x "$PG_PY" ]; then
    echo "未找到本地 PostgreSQL 运行器：$PG_PY" >&2
    return 1
  fi
  "$PG_PY" - <<PY
from pathlib import Path
import pgserver
srv = pgserver.get_server(Path("${PG_DATA}"), cleanup_mode="stop")
srv.cleanup()
print("PG_STOPPED")
PY
}

pg_test() {
  source_env
  if [ ! -f "$PG_URI_FILE" ]; then
    echo "缺少 PG URI 文件：$PG_URI_FILE；请先执行 pg-start" >&2
    exit 2
  fi
  export BIDCRAFT_TEST_PG_URL="$(cat "$PG_URI_FILE")"
  if [ "$#" -gt 0 ]; then
    # 显式传入用例时只运行这些用例，避免把迁移往返与并发用例混在同一库同一轮。
    .venv/bin/python -m pytest -o addopts="" -q "$@"
    return
  fi
  .venv/bin/python -m pytest -o addopts="" -q \
    tests/postgres \
    tests/test_postgres_integration.py \
    tests/test_v030_pg_isolation.py \
    tests/test_worker_fencing_atomicity.py \
    tests/test_jobqueue_race_conditions.py
}

model_on() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "缺少环境文件：$ENV_FILE；请先执行 init" >&2
    exit 2
  fi
  local tmp
  tmp="$(mktemp)"
  grep -v -E '^BIDCRAFT_(OFFLINE|MODEL_BASE_URL|MODEL_NAME|MODEL_API_KEY)=' "$ENV_FILE" > "$tmp" || true
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "[model-on] 已移除离线/假模型环境变量；服务将使用后台模型配置。"
}

model_off() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "缺少环境文件：$ENV_FILE；请先执行 init" >&2
    exit 2
  fi
  local tmp
  tmp="$(mktemp)"
  grep -v -E '^BIDCRAFT_(OFFLINE|MODEL_BASE_URL|MODEL_NAME|MODEL_API_KEY)=' "$ENV_FILE" > "$tmp" || true
  {
    cat "$tmp"
    printf '%s\n' \
      'BIDCRAFT_OFFLINE=1' \
      'BIDCRAFT_MODEL_BASE_URL=http://127.0.0.1:9/v1' \
      'BIDCRAFT_MODEL_NAME=local-test-model'
  } > "$ENV_FILE"
  rm -f "$tmp"
  chmod 600 "$ENV_FILE"
  echo "[model-off] 已恢复离线/假模型环境变量。"
}

serve_local() {
  source_env
  exec .venv/bin/bidcraft serve "$@"
}

case "${1:-}" in
  init) init_env ;;
  backend) shift; backend "$@" ;;
  backend-quick) shift; backend_quick "$@" ;;
  web) web_checks ;;
  all) init_env; backend; web_checks ;;
  pg-start) pg_start ;;
  pg-stop) pg_stop ;;
  pg-test) shift; pg_test "$@" ;;
  model-on) model_on ;;
  model-off) model_off ;;
  serve) shift; serve_local "$@" ;;
  *)
    cat <<'EOF'
BidCraft 本地测试环境

用法：
  ./scripts/local_test_env.sh init           # 创建/复用隔离环境与 .env（chmod 600）
  ./scripts/local_test_env.sh backend        # 后端全量 pytest
  ./scripts/local_test_env.sh backend-quick  # 后端快速核心回归
  ./scripts/local_test_env.sh web            # 前端 typecheck/lint/test/build
  ./scripts/local_test_env.sh all            # init + 后端全量 + 前端全量
  ./scripts/local_test_env.sh pg-start       # 首次自动安装并启动隔离 PostgreSQL（可选）
  ./scripts/local_test_env.sh pg-test        # 运行真实 PostgreSQL 套件
  ./scripts/local_test_env.sh pg-stop        # 停止隔离 PostgreSQL
  ./scripts/local_test_env.sh model-on       # 移除离线/假模型变量，使用后台模型配置
  ./scripts/local_test_env.sh model-off      # 恢复离线/假模型变量（仅测试）
  ./scripts/local_test_env.sh serve          # 用测试环境启动本地服务

可直接把额外参数传给 pytest：
  ./scripts/local_test_env.sh backend tests/test_api_package.py -q
EOF
    ;;
esac
