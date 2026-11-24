"""v0.30 Stage6-B：requirements 判定的项目级查询复用与 /check 判定一致性。

两组验收：
1. GET /requirements 对 PROJECTS / FACTS / MATERIALS / PARAMETERS /
   SCORING_ITEMS / REQUIREMENTS / RISKS / MODEL_CONFIGS 的 SELECT 次数不随
   条款数增长（1 条 vs 100 条记录实际值）。
2. 带招标截止日期事实 + 时间窗复合条款时，GET /requirements 的 verdict 与
   layer4.deadline_window 证据与 pipeline.check_project 的 SUB 问题逐字段一致。
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "stage6-verdict-pass-1"

# 本用例关心的项目级表 —— 这些 SELECT 必须与条款数无关。
_COUNTED_TABLES = (
    "projects",
    "facts",
    "materials",
    "parameters",
    "scoring_items",
    "requirements",
    "risks",
    "model_configs",
)
_FROM_RE = {
    table: re.compile(r'\bfrom\s+"?' + table + r'"?\b', re.IGNORECASE)
    for table in _COUNTED_TABLES
}


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def owner(db):
    admin = _login(*ADMIN)
    response = admin.post(
        "/admin/users",
        json={
            "username": "s6-owner",
            "display_name": "s6-owner",
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert response.status_code == 201, response.text
    return _login("s6-owner")


def _project(client) -> str:
    response = client.post(
        "/projects", json={"name": "Stage6 判定项目", "package": "weakcurrent-cabling"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _seed_requirements(project_id: str, count: int, body: str) -> list[str]:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Requirement, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading="技术方案", position=1)
        session.add(node)
        session.flush()
        session.add(
            Section(
                node_id=node.id,
                body=body,
                status="written",
                words=len(body),
                review_status="confirmed",
            )
        )
        ids: list[str] = []
        for index in range(count):
            row = Requirement(
                project_id=project_id,
                kind="substantive",
                text=f"★ 传输带宽不小于1000Mbps（条款{index}）",
                mandatory=True,
                source_quote="带宽要求",
                marker="★",
                response_status="pending",
                node_id=node.id,
            )
            session.add(row)
            session.flush()
            ids.append(row.id)
        return ids


def _counted_selects(engine, call):
    """统计某个调用期间按表分组的 SELECT 次数。"""
    from sqlalchemy import event

    counts = {table: 0 for table in _COUNTED_TABLES}

    def before(_conn, _cursor, statement, _params, _context, _many):
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        for table, pattern in _FROM_RE.items():
            if pattern.search(statement):
                counts[table] += 1

    event.listen(engine, "before_cursor_execute", before)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return result, counts


@pytest.mark.parametrize("count", (1, 100))
def test_requirements_project_level_selects_are_constant(owner, count):
    """单条与 100 条条款的项目级 SELECT 次数完全相同（Stage6 N+1 回归）。"""
    from bidcraft.db import get_engine

    pid = _project(owner)
    _seed_requirements(pid, count, "我方带宽达到1200Mbps，满足要求。")
    engine = get_engine()

    items, counts = _counted_selects(
        engine, lambda: owner.get(f"/projects/{pid}/requirements").json()
    )
    assert len(items) == count

    # Stage6 修复后的实测基准值（1 条与 100 条完全相同）：
    # projects=1（同一会话 identity-map 命中，_project 已加载项目）、facts=1、
    # materials=1、parameters=1、scoring_items=1、requirements=2
    # （ledger + 列表）、risks=1、model_configs=0（LLM 语义层默认关闭）。
    assert counts == {
        "projects": 1,
        "facts": 1,
        "materials": 1,
        "parameters": 1,
        "scoring_items": 1,
        "requirements": 2,
        "risks": 1,
        "model_configs": 0,
    }, (count, counts)


class StubMatcher:
    """确定性语义层替身：两个判定入口共享同一实例口径。"""

    def coverage(self, text: str, body: str, req_type: str = "") -> float:
        return 0.8


@pytest.mark.parametrize("count", (1, 100))
def test_requirements_matcher_selects_are_constant_when_llm_enabled(
    owner, monkeypatch, count
):
    """开启可选 LLM matcher 时，MODEL_CONFIGS 查询也不随条款数增长。"""
    from bidcraft import verdict_llm
    from bidcraft.config import settings
    from bidcraft.db import get_engine

    class _FakeClient:
        available = True

        def __init__(self, *_args, **_kwargs):
            pass

        def complete(self, *_args, **_kwargs):
            return "0.8"

    monkeypatch.setattr(settings, "offline", False)
    monkeypatch.setattr(settings, "verdict_llm", True)
    monkeypatch.setattr(settings, "model_base_url", "http://llm.invalid")
    monkeypatch.setattr(settings, "model_name", "stub-model")
    monkeypatch.setattr(verdict_llm, "ModelClient", _FakeClient)

    pid = _project(owner)
    _seed_requirements(pid, count, "我方带宽达到1200Mbps，满足要求。")
    engine = get_engine()

    items, counts = _counted_selects(
        engine, lambda: owner.get(f"/projects/{pid}/requirements").json()
    )
    assert len(items) == count
    # 真实 resolve_chat_endpoint 每次执行两条 MODEL_CONFIGS SELECT；修复后
    # 整个列表请求只构造一次 matcher（仍为 2，而不是 2 * count）。
    assert counts["model_configs"] == 2, (count, counts)
    assert counts["facts"] == 1 and counts["requirements"] == 2, (count, counts)
    assert {item["verdict_layers"]["layer4"]["matcher"] for item in items} == {
        "LLMSemanticMatcher"
    }


def test_requirements_match_check_project_deadline_verdict(owner, monkeypatch):
    """同一截止日期事实 + 同一 matcher：GET /requirements 与 /check 判定一致。"""
    from bidcraft import verdict_llm
    from bidcraft.db import session_scope
    from bidcraft.models import Fact, OutlineNode, Requirement, Section
    from bidcraft.pipeline import check_project

    matcher = StubMatcher()
    monkeypatch.setattr(verdict_llm, "build_verdict_matcher", lambda db=None: matcher)

    pid = _project(owner)
    body = "2025年完成A合同复印件。2020年完成B合同复印件。2023年12月完成C合同复印件。"
    with session_scope() as session:
        session.add(
            Fact(
                project_id=pid,
                key="project.bid_deadline",
                label="投标截止日期",
                value="2026-11-22",
                status="confirmed",
            )
        )
        node = OutlineNode(project_id=pid, heading="业绩与资质", position=1)
        session.add(node)
        session.flush()
        session.add(
            Section(
                node_id=node.id,
                body=body,
                status="written",
                words=len(body),
                review_status="confirmed",
            )
        )
        requirement = Requirement(
            project_id=pid,
            kind="substantive",
            text="★ 近三年不少于3项类似项目业绩，并提供合同复印件",
            mandatory=True,
            source_quote="业绩要求",
            marker="★",
            response_status="pending",
            node_id=node.id,
        )
        session.add(requirement)
        session.flush()
        req_id = requirement.id

    items = owner.get(f"/projects/{pid}/requirements").json()
    api_item = next(item for item in items if item["id"] == req_id)
    api_state = api_item["verdict"]
    api_window = api_item["verdict_layers"]["layer4"]["deadline_window"]
    assert api_window["has_deadline"] is True
    assert api_window["deadline"] == "2026-11-22"
    assert api_window["valid"] >= 1 and api_window["expired"] >= 1

    with session_scope() as session:
        results, _summary = check_project(session, pid)

    sub = next(
        result
        for result in results
        if result.code.startswith("SUB-")
        and result.evidence.get("requirement_id") == req_id
    )
    check_state = sub.evidence["verdict"]
    check_window = sub.evidence["layers"]["layer4"]["deadline_window"]

    assert sub.evidence["layers"]["layer4"]["matcher"] == "StubMatcher"
    assert api_state == check_state, (api_state, check_state)
    assert api_window == check_window, (api_window, check_window)


def test_admin_health_owns_global_metrics_without_double_computation(db, monkeypatch):
    """Stage6/8：admin_health 自带 10 键指标（含队列健康度），且只计算一次。"""
    from bidcraft import health

    calls = {"n": 0}
    real = health.global_metrics

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(health, "global_metrics", counting)
    client = _login(*ADMIN)

    body = client.get("/admin/health").json()
    assert set(body["global_metrics"]) == {
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
    # 原有响应字段一个不少；global_metrics 由端点自身计算，包装层不重复调用。
    assert {
        "status", "version", "started_at", "uptime_seconds", "python", "platform",
        "home", "db_size_bytes", "disk_free_bytes", "web_ready", "offline",
        "docs_enabled", "health_public", "secure_cookie", "model_configured",
        "model_label", "embedding_configured", "embedding_model", "models",
        "knowledge_bases", "users", "auth_required", "registration",
        "wechat_login", "wechat_pay", "global_metrics",
    } <= set(body)
    assert calls["n"] == 1, calls
    assert "global_metrics" not in client.get("/health").json()
