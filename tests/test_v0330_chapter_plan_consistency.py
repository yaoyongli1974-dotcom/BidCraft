"""v0.33.0 RC-03：章节规划依赖一致性与契约一致性回归（真实 DB / TestClient）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from bidcraft.db import session_scope  # noqa: E402
from bidcraft.generation.chapter_plan import (  # noqa: E402
    CONFIRMED,
    REJECTED,
    confirm_chapter_plan,
    generate_chapter_plan,
    load_active_plan,
    plan_dependency_staleness,
)
from bidcraft.models import (  # noqa: E402
    ChapterPlan,
    Fact,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    OutlineNode,
    Project,
    Requirement,
    ScoringItem,
)


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    return TestClient(create_app())


def _register(username: str):
    client = _client()
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "password": "v0330-pass-1",
            "display_name": username,
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    return client


def _project(client, name: str) -> str:
    created = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _set_credits(username: str, amount: int) -> None:
    from bidcraft.models import User

    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == username))
        user.credits = amount
        db.flush()


def _new_project(db, name: str, *, package_id: str = "pkg-1") -> Project:
    project = Project(id=f"p-{uuid.uuid4().hex}", name=name, package="weakcurrent-cabling")
    db.add(project)
    db.flush()
    project.meta = {**(project.meta or {}), "package_id": package_id}
    db.flush()
    return project


def _seed(db, project_id: str, *, heading: str = "技术方案", kb_package: str | None = None):
    node = OutlineNode(
        project_id=project_id, heading=heading, intent="逐条响应评分标准",
        target_words=1200, needs=["project.name"],
    )
    db.add(node)
    score = ScoringItem(
        project_id=project_id, name="技术方案", score=40.0, criteria="方案完整、响应招标要求"
    )
    db.add(score)
    requirement = Requirement(
        project_id=project_id, kind="substantive", text="★提供营业执照", mandatory=True
    )
    db.add(requirement)
    fact = Fact(
        project_id=project_id, key="project.name", label="项目名称", value="示例项目",
        status="confirmed", required=True,
    )
    db.add(fact)
    kb = KnowledgeBase(
        name=f"kb-{uuid.uuid4().hex}", kind="local", enabled=True,
        options={"package_id": kb_package} if kb_package else {},
    )
    db.add(kb)
    db.flush()
    document = KbDocument(kb_id=kb.id, source="企业技术资料.pdf", status="ready")
    db.add(document)
    db.flush()
    chunk = KbChunk(
        kb_id=kb.id, document_id=document.id, heading="技术方案",
        text="技术方案需要逐条响应评分标准", source=document.source,
    )
    db.add(chunk)
    db.flush()
    return {
        "node": node.id, "score": score.id, "requirement": requirement.id,
        "fact": fact.id, "kb": kb.id, "document": document.id, "chunk": chunk.id,
    }


def _retrieval_context(ids: dict[str, str]):
    return SimpleNamespace(
        text="技术方案需要逐条响应评分标准",
        downgrades=0,
        refs=[{
            "id": ids["chunk"], "chunk_id": ids["chunk"], "kb_id": ids["kb"],
            "document_id": ids["document"], "page": 3, "heading": "技术方案",
            "source": "企业技术资料.pdf", "snippet": "技术方案需要逐条响应评分标准",
            "score": 0.91, "rerank_score": 0.87,
        }],
        diagnostics=[],
    )


def _report():
    return SimpleNamespace(
        written=["技术方案"], fallback=[], failed=[], suspicious={}, suspicious_count=0,
        knowledge_downgrades=0, verification={}, usage={},
    )


def test_plan_dependency_staleness_detects_live_value_changes(db):
    with session_scope() as session:
        project = _new_project(session, "依赖变化项目")
        ids = _seed(session, project.id)
        node = session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(
            session, project, node, created_by="tester",
            knowledge_provider=lambda _q: _retrieval_context(ids),
        )
        confirm_chapter_plan(session, plan, "reviewer")
        assert plan_dependency_staleness(session, plan) == []

        session.get(ScoringItem, ids["score"]).score = 60.0
        session.get(Requirement, ids["requirement"]).text = "★提供其他证明"
        session.get(Fact, ids["fact"]).value = "另一项目"
        session.delete(session.get(KbChunk, ids["chunk"]))
        session.flush()

        reasons = plan_dependency_staleness(session, plan)
        joined = "；".join(reasons)
        assert "评分项" in joined
        assert "要求" in joined
        assert "事实" in joined
        assert "知识" in joined


def test_load_active_plan_does_not_fall_back_to_older_confirmed(db):
    with session_scope() as session:
        project = _new_project(session, "不回退旧版项目")
        ids = _seed(session, project.id)
        node = session.get(OutlineNode, ids["node"])
        old = generate_chapter_plan(session, project, node, created_by="tester")
        confirm_chapter_plan(session, old, "reviewer")
        latest = generate_chapter_plan(session, project, node, created_by="tester", regenerate=True)
        latest.status = REJECTED
        session.flush()
        assert load_active_plan(session, project.id, node.id) is None


@pytest.mark.usefixtures("db")
def test_stale_confirmed_plan_blocks_chapter_generation(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v0330-stale-gate"
    client = _register(username)
    ref = _project(client, "依赖失效门禁项目")
    with session_scope() as session:
        ids = _seed(session, ref)
        node = session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(session, session.get(Project, ref), node, created_by=username)
        confirm_chapter_plan(session, plan, username)
    _set_credits(username, 50)
    monkeypatch.setattr(
        routes_projects, "_generation_client",
        lambda *_a, **_k: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(routes_projects, "generate_document", lambda *_a, **_k: _report())

    with session_scope() as session:
        session.get(Fact, ids["fact"]).value = "变化后的项目名"
        session.flush()
    blocked = client.post(f"/projects/{ref}/chapters/{ids['node']}/generate")
    assert blocked.status_code == 409, blocked.text
    assert "依赖" in blocked.json()["detail"]
    with session_scope() as session:
        assert session.get(ChapterPlan, plan.id).status == REJECTED


@pytest.mark.usefixtures("db")
def test_generation_freezes_gated_plan_identity(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v0330-plan-freeze"
    client = _register(username)
    ref = _project(client, "规划身份冻结项目")
    with session_scope() as session:
        ids = _seed(session, ref)
        node = session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(session, session.get(Project, ref), node, created_by=username)
        confirm_chapter_plan(session, plan, username)
        plan_id = plan.id
    _set_credits(username, 50)
    seen: list[object] = []

    def fake_generate(*_args, **kwargs):
        provider = kwargs.get("plan_provider")
        plan_value = provider(ids["node"]) if provider else None
        seen.append(plan_value)
        with session_scope() as session:
            row = session.get(ChapterPlan, plan_id)
            row.status = "DRAFT"
            session.flush()
        return _report()

    monkeypatch.setattr(
        routes_projects, "_generation_client",
        lambda *_a, **_k: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(routes_projects, "generate_document", fake_generate)
    response = client.post(f"/projects/{ref}/chapters/{ids['node']}/generate")
    assert response.status_code == 200, response.text
    assert len(seen) == 1
    assert getattr(seen[0], "id", None) == plan_id
    assert getattr(seen[0], "status", None) == CONFIRMED


@pytest.mark.usefixtures("db")
def test_plan_generate_honors_regenerate_payload():
    client = _register("v0330-plan-regenerate")
    ref = _project(client, "重新生成契约项目")
    with session_scope() as session:
        ids = _seed(session, ref)
    base = f"/projects/{ref}/chapters/{ids['node']}/plan"
    first = client.post(base, json={})
    assert first.status_code == 200, first.text
    second = client.post(base, json={"regenerate": True})
    assert second.status_code == 200, second.text
    assert first.json()["plan"]["version"] == 1
    assert second.json()["plan"]["version"] == 2


@pytest.mark.usefixtures("db")
def test_confirm_keeps_plan_package_scope_for_knowledge_refs():
    client = _register("v0330-plan-package")
    ref = _project(client, "标段规划项目")
    with session_scope() as session:
        ids = _seed(session, ref, kb_package="pkg-1")
        project = session.get(Project, ref)
        project.meta = {**(project.meta or {}), "package_id": "pkg-2"}
        session.flush()
        node = session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(
            session,
            project,
            node,
            created_by="v0330-plan-package",
            package_id="pkg-1",
            knowledge_provider=lambda _q: _retrieval_context(ids),
        )
        assert ids["chunk"] in [row["id"] for row in plan.knowledge_refs]
        plan_id = plan.id

    base = f"/projects/{ref}/chapters/{ids['node']}/plan"
    confirmed = client.post(f"{base}/confirm", json={"note": "按规划标段确认"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["plan"]["id"] == plan_id
    assert ids["chunk"] in [row["id"] for row in confirmed.json()["plan"]["knowledge_refs"]]


@pytest.mark.usefixtures("db")
def test_plan_generation_passes_lessons_into_prompt(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v0330-plan-lessons"
    client = _register(username)
    ref = _project(client, "经验提示词项目")
    with session_scope() as session:
        ids = _seed(session, ref)
    prompts: list[str] = []

    class FakeClient:
        available = True

        def complete(self, messages, *, max_tokens=None, temperature=None):
            prompts.append("\n".join(str(row.get("content") or "") for row in messages))
            return ""

    monkeypatch.setattr(routes_projects, "_generation_client", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr(
        routes_projects, "lessons_block",
        lambda db, project, node_id=None: "【未审核建议经验】按历史复核意见补充实施路径",
    )
    response = client.post(
        f"/projects/{ref}/chapters/{ids['node']}/plan",
        json={"user_instruction": "突出实施路径"},
    )
    assert response.status_code == 200, response.text
    assert prompts
    assert "【未审核建议经验】" in prompts[0]
