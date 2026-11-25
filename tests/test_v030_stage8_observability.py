"""v0.30 阶段 8：任务日志字段、核心指标、存活/就绪探针与访问日志关联。"""

from __future__ import annotations

import io
import json
import logging
import threading
from datetime import timedelta

from sqlalchemy import select


def _log_handler(stream):
    from bidcraft.config import _JobContextFilter, _JsonFormatter, _RequestIdFilter

    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_RequestIdFilter())
    handler.addFilter(_JobContextFilter())
    return handler


def test_json_log_line_has_job_fields_under_execution_context():
    """execution_context 绑定的四个字段出现在每一行 JSON 日志里。"""
    from bidcraft.config import reset_request_id, set_request_id
    from bidcraft.jobqueue import execution_context, log_job_failure

    stream = io.StringIO()
    handler = _log_handler(stream)
    log = logging.getLogger("bidcraft.workers.test-observability")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(handler)
    request_token = set_request_id("req-obs-1")
    try:
        with execution_context(
            "token-1",
            threading.Event(),
            job_id="job-1",
            kind="generate",
            project_id="project-1",
            worker_id="worker-1",
        ):
            log.info("任务开始")
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                log_job_failure(log)
        # 上下文退出后字段复位，不污染后续日志。
        log.info("上下文外")
    finally:
        reset_request_id(request_token)
        log.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3
    assert set(lines[0]) == {
        "ts",
        "level",
        "logger",
        "request_id",
        "message",
        "job_id",
        "kind",
        "project_id",
        "worker_id",
    }
    assert lines[0]["request_id"] == "req-obs-1"
    assert lines[0]["message"] == "任务开始"
    for field, expected in (
        ("job_id", "job-1"),
        ("kind", "generate"),
        ("project_id", "project-1"),
        ("worker_id", "worker-1"),
    ):
        assert lines[0][field] == expected
        assert lines[1][field] == expected
        # 统一失败模板用同样的字段名输出。
        assert f"{field}={expected}" in lines[1]["message"]
    assert lines[1]["level"] == "ERROR"
    assert "exception" in lines[1]
    assert lines[2]["job_id"] == "-" and lines[2]["kind"] == "-"
    assert lines[2]["project_id"] == "-" and lines[2]["worker_id"] == "-"


def test_eight_worker_loggers_use_bidcraft_workers_namespace():
    from bidcraft.workers import (
        agent,
        compliance,
        evaluation,
        export,
        generation,
        images,
        knowledge,
        similarity,
    )

    for module in (
        generation,
        agent,
        compliance,
        evaluation,
        export,
        images,
        knowledge,
        similarity,
    ):
        assert module.logger.name == "bidcraft.workers"


def test_live_and_ready_probes(db, monkeypatch):
    """存活探针恒定 200；DB 正常且离线模式下就绪探针 200。"""
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.api.routes import health as health_routes

    class _BuiltWebPath:
        """最小 Path 替身：测试环境未生成 Web 构建产物，桩掉 is_file。"""

        def __init__(self, _path):
            pass

        def resolve(self):
            return self

        @property
        def parent(self):
            return self

        @property
        def parents(self):
            return (self, self, self)

        def __truediv__(self, _other):
            return self

        def is_file(self):
            return True

    monkeypatch.setattr(health_routes, "Path", _BuiltWebPath)
    client = TestClient(create_app())
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    ready = client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["status"] == "ready"
    assert body["db"] == "ok" and body["web_ready"] is True
    assert body["model_configured"] is False  # 离线模式豁免未配置模型


def test_ready_returns_503_when_db_is_broken(db, monkeypatch):
    """DB 不可用时 ready=503，live 仍为 200（避免无谓重启）。"""
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.api.routes import health as health_routes

    def _broken_session_scope():
        raise RuntimeError("db down")

    monkeypatch.setattr(health_routes, "session_scope", _broken_session_scope)
    client = TestClient(create_app())
    assert client.get("/health/live").status_code == 200
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "数据库" in response.json()["detail"]


def test_ready_returns_503_when_model_unconfigured(db, monkeypatch):
    """非离线部署未配置对话模型时不允许就绪。"""
    from fastapi.testclient import TestClient

    from bidcraft import config as config_module
    from bidcraft.api import create_app
    from bidcraft.api.routes import health as health_routes

    monkeypatch.setattr(config_module.settings, "offline", False)
    monkeypatch.setattr(config_module.settings, "model_base_url", "")
    monkeypatch.setattr(config_module.settings, "model_name", "")
    monkeypatch.setattr(health_routes.model_config, "resolve_chat_endpoint", lambda db: None)
    client = TestClient(create_app())
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "模型" in response.json()["detail"]


def test_global_metrics_ten_keys_include_queue_health(db):
    """10 个全局指标键齐全，包含积压、最老等待时长与陈旧 running 任务。"""
    from bidcraft.clock import utc_now
    from bidcraft.db import session_scope
    from bidcraft.health import global_metrics
    from bidcraft.models import Job

    with session_scope() as session:
        session.add(
            Job(kind="check", status="queued", created_at=utc_now() - timedelta(seconds=45))
        )
        session.add(
            Job(kind="check", status="running", lease_expires_at=utc_now() - timedelta(seconds=5))
        )
        session.flush()

    metrics = global_metrics()
    assert set(metrics) == {
        "projects_by_status",
        "jobs_by_status",
        "projects",
        "jobs",
        "open_findings",
        "users",
        "workers_running",
        "queued_jobs",
        "oldest_queued_age_seconds",
        "stale_running_jobs",
    }
    assert isinstance(metrics["workers_running"], bool)
    assert metrics["queued_jobs"] == 1
    assert metrics["oldest_queued_age_seconds"] >= 40
    assert metrics["stale_running_jobs"] == 1
    # 库内计数与直接查询一致。
    with session_scope() as session:
        queued = session.scalar(select(Job.id).where(Job.status == "queued"))
        assert queued is not None


def test_health_routes_and_total_route_count():
    """公开探针路由 + v0.32 章节规划路由后，路由总数为 259。"""
    from bidcraft.api.routes import (
        account,
        admin,
        approvals,
        auth,
        batch_jobs,
        billing,
        collaboration,
        commercial,
        enterprise,
        evaluation,
        health,
        jobs,
        legal,
        my,
        orchestration,
        packages,
        projects,
        similarity,
    )

    assert {route.path for route in health.router.routes} >= {
        "/health",
        "/health/live",
        "/health/ready",
    }
    modules = [
        account, admin, approvals, auth, batch_jobs, billing, commercial,
        enterprise, evaluation, health, jobs, legal, my, packages, projects, similarity,
        orchestration, collaboration,
    ]
    assert sum(len(mod.router.routes) for mod in modules) == 259


def test_serve_passes_json_access_log_config_with_request_id_filter(db, monkeypatch):
    """serve 把 _JsonFormatter + _RequestIdFilter 挂到 uvicorn.access。"""
    import uvicorn

    from bidcraft import cli, jobqueue

    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(jobqueue, "recover_on_startup", lambda: (0, 0))
    monkeypatch.setattr(jobqueue, "start_workers", lambda: None)
    monkeypatch.setattr(cli, "_install_shutdown_handlers", lambda: None)
    cli.serve(host="127.0.0.1", port=8000)

    config = captured["log_config"]
    assert config["loggers"]["uvicorn.access"]["handlers"] == ["access"]
    handler = config["handlers"]["access"]
    assert handler["formatter"] == "access"
    assert "request_id" in handler["filters"]
    assert config["filters"]["request_id"]["()"] == "bidcraft.config._RequestIdFilter"
    assert config["formatters"]["access"]["()"] == "bidcraft.config._JsonFormatter"
