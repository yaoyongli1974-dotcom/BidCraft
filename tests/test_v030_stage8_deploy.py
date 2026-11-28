"""v0.30 阶段 8：容器/CI 部署配置的静态断言。

只读仓库内的 Dockerfile / compose / CI YAML，不调用 Docker、不连数据库、不改环境。
用于防止加固项被无意回退（env_file 注入、探针路径、资源限制、镜像与迁移预检门禁）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _compose_service() -> dict:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return data["services"]["bidcraft"]


def _ci_jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _step_scripts(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def _seconds(value: object) -> int:
    match = re.fullmatch(r"(\d+)s", str(value))
    assert match, f"无法解析秒数: {value!r}"
    return int(match.group(1))


def test_dockerfile_installs_pg_client_and_probes_live_endpoint():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "postgresql-client" in text, "镜像缺少 pg_dump/psql（bidcraft backup/恢复演练需要）"
    assert re.search(r"^USER\s+(?!root\b|0\b)\S+", text, re.MULTILINE), "容器必须以非 root 用户运行"

    healthcheck = text.split("HEALTHCHECK", 1)[1].split("ENTRYPOINT", 1)[0]
    assert "/health/live" in healthcheck, "HEALTHCHECK 必须改用 /health/live"
    assert "'http://127.0.0.1:8000/health'" not in healthcheck, "不再用遗留 /health 探活"


def test_compose_does_not_enable_env_file():
    text = COMPOSE.read_text(encoding="utf-8")
    assert not re.search(r"^\s*env_file\s*:", text, re.MULTILINE), "不得向容器整份注入 .env"
    assert "env_file" not in _compose_service()


def test_compose_resource_limits_and_hardening():
    service = _compose_service()
    assert _seconds(service["stop_grace_period"]) >= 45
    assert float(str(service["cpus"])) >= 2.0
    assert int(service["pids_limit"]) >= 256
    assert "ALL" in service["cap_drop"], "必须丢弃全部 Linux capabilities"
    assert any("no-new-privileges:true" in str(item) for item in service["security_opt"])


def test_compose_liveness_and_readiness_probe_paths():
    service = _compose_service()
    assert "/health/live" in str(service["healthcheck"]["test"])
    assert service["x-liveness-probe"]["httpGet"]["path"] == "/health/live"
    assert service["x-readiness-probe"]["httpGet"]["path"] == "/health/ready"

    for probe in (service["x-liveness-probe"], service["x-readiness-probe"]):
        assert int(probe["initialDelaySeconds"]) >= 20, "启动宽限期过短"
        assert int(probe["periodSeconds"]) > 0
        assert int(probe["timeoutSeconds"]) >= 4
        assert int(probe["failureThreshold"]) >= 3


def test_ci_has_docker_image_job_with_image_assertions():
    jobs = _ci_jobs()
    assert "docker-image" in jobs, "CI 缺少生产镜像构建 job"
    scripts = _step_scripts(jobs["docker-image"])
    # v0.33.2 将临时镜像标签改为 v0332-check；断言当前标签且不允许旧标签回退。
    assert "docker build -t bidcraft:v0332-check ." in scripts
    assert "v0331-check" not in scripts
    assert "v0330-check" not in scripts
    assert "bidcraft:v0322-check" not in scripts
    assert "bidcraft:v0321-check" not in scripts
    assert "bidcraft:v0313-check" not in scripts
    assert "bidcraft:v030-check" not in scripts
    assert "docker inspect" in scripts and "Config.User" in scripts
    assert "pg_dump --version" in scripts
    # 当前 CLI 提供 version 子命令而非 --version 选项，两者都视为可执行的 CLI 门禁。
    assert "bidcraft --version" in scripts or "bidcraft version" in scripts
    assert "/health/live" in scripts
    # prod 启动校验要求模型配置；CI smoke 用不可达地址满足配置校验，
    # 不连接真实模型，也不触发任何生成调用（/health/live 无外部依赖）。
    assert "BIDCRAFT_MODEL_BASE_URL=http://127.0.0.1:9/v1" in scripts
    assert "BIDCRAFT_MODEL_NAME=rc-smoke-model" in scripts
    assert "env_file" in scripts, "compose 静态断言应覆盖 env_file"


def test_ci_has_alembic_preflight_without_touching_runtime():
    jobs = _ci_jobs()
    python_lint = jobs["python-lint"]
    names = [step.get("name", "") for step in python_lint["steps"]]
    assert any("Lint (ruff)" in name for name in names), "既有 ruff 门禁必须保留"
    assert any("single-head" in name for name in names), "既有单 head 门禁必须保留"

    preflight = [step for step in python_lint["steps"] if "preflight" in step.get("name", "").lower()]
    assert len(preflight) == 1
    script = preflight[0]["run"]
    assert "upgrade" in script and '"head"' in script and "sql=True" in script
    assert "scratch" in script, "预检只能作用于临时库/临时目录"
