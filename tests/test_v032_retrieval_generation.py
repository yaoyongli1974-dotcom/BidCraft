"""v0.32 Phase 3：检索引用治理、规划围栏、导出过期门禁与检索预览。

核心证据全部落在仓库真实 SQLite 库上；模型/检索端点只用确定性 stub 替代，
不靠 mock 伪造 DB 写入或门禁结果。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from bidcraft import jobqueue, provenance  # noqa: E402
from bidcraft.clock import utc_now  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.export_gate import ExportGateError, enforce_export_gate  # noqa: E402
from bidcraft.generation.chapter_plan import (  # noqa: E402
    generate_chapter_plan,
    normalize_retrieval,
    plan_context_text,
    retrieval_ref_payload,
)
from bidcraft.generation.writer import suspicious_numbers, unused_plan_refs  # noqa: E402
from bidcraft.jobqueue import claim_context  # noqa: E402
from bidcraft.ledger import load_ledger  # noqa: E402
from bidcraft.models import (  # noqa: E402
    AuditLog,
    ChapterPlan,
    Fact,
    FactCitation,
    Job,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    OutlineNode,
    Project,
    Requirement,
    ScoringItem,
    Section,
)
from bidcraft.workers.generation import (  # noqa: E402
    _resolve_worker_plan,
    commit_generation_chapter,
)

TOKEN = "v032-phase3-claim"


def _new_project(db, name: str) -> Project:
    row = Project(id=f"p-{uuid.uuid4().hex}", name=name, package="weakcurrent-cabling")
    db.add(row)
    db.flush()
    return row


def _seed(db, project_id: str) -> dict[str, str]:
    node = OutlineNode(
        project_id=project_id, heading="技术方案", intent="逐条响应评分标准",
        target_words=1200, needs=["project.name"],
    )
    db.add(node)
    score = ScoringItem(
        project_id=project_id, name="技术方案", score=40.0, criteria="方案完整"
    )
    requirement = Requirement(
        project_id=project_id, kind="substantive", text="★提供营业执照", mandatory=True
    )
    fact = Fact(
        project_id=project_id, key="project.name", label="项目名称", value="示例项目",
        status="confirmed", required=True,
    )
    db.add_all([score, requirement, fact])
    kb = KnowledgeBase(name=f"kb-{uuid.uuid4().hex}", kind="local", enabled=True)
    db.add(kb)
    db.flush()
    document = KbDocument(kb_id=kb.id, source="企业资料.pdf", status="ready")
    db.add(document)
    db.flush()
    chunk = KbChunk(
        kb_id=kb.id, document_id=document.id, heading="技术方案",
        text="真实资料内容", source=document.source,
    )
    db.add(chunk)
    db.flush()
    project = db.get(Project, project_id)
    project.meta = {**(project.meta or {}), "package_id": "pkg-1"}
    db.flush()
    return {
        "node": node.id, "score": score.id, "requirement": requirement.id,
        "fact": fact.id, "kb": kb.id, "document": document.id, "chunk": chunk.id,
    }


def _context(ids: dict[str, str], *, snippet: str = "真实资料内容") -> SimpleNamespace:
    return SimpleNamespace(
        text="检索文本", downgrades=0, diagnostics=[{"kb_id": ids["kb"], "status": "ok"}],
        refs=[
            SimpleNamespace(
                id=ids["chunk"], chunk_id=ids["chunk"], kb_id=ids["kb"],
                document_id=ids["document"], page=7, heading="技术方案",
                source="企业资料.pdf", snippet=snippet, score=0.93,
                rerank_score=0.88, trust_tier="trusted", degraded=False,
            )
        ],
    )


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    return TestClient(create_app())


def _register(username: str):
    client = _client()
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "phase3-pass-1", "display_name": username,
              "accept_terms": True},
    )
    assert response.status_code == 201, response.text
    return client


def _api_project(client, name: str) -> str:
    created = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _set_credits(username: str, amount: int) -> None:
    from bidcraft.models import User

    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == username))
        user.credits = amount
        db.flush()


# ---------------------------------------------------------- 检索引用归一化


def test_normalize_retrieval_reads_attributes_and_falls_back_to_tuple():
    ref = SimpleNamespace(
        id="chunk-1", chunk_id="chunk-1", document_id="doc-1", page=2,
        heading="标题", source="来源.pdf", snippet="原文", score=0.8,
        rerank_score=0.75, trust_tier="trusted", degraded=True,
    )
    context = SimpleNamespace(
        text="文本", downgrades=2, refs=[ref], diagnostics=[{"level": "warn"}]
    )
    text, downgrades, refs, diagnostics = normalize_retrieval(context)
    assert (text, downgrades) == ("文本", 2)
    assert refs[0]["chunk_id"] == "chunk-1"
    assert refs[0]["document_id"] == "doc-1"
    assert refs[0]["page"] == 2
    assert refs[0]["score"] == 0.8
    assert refs[0]["rerank_score"] == 0.75
    assert refs[0]["trust_tier"] == "trusted"
    assert refs[0]["degraded"] is True
    assert diagnostics == [{"level": "warn"}]
    assert retrieval_ref_payload(ref)["trace"].startswith("文档 doc-1")
    assert normalize_retrieval(("旧文本", 3)) == ("旧文本", 3, [], [])
    assert normalize_retrieval("纯文本") == ("纯文本", 0, [], [])


@pytest.mark.usefixtures("db")
def test_plan_uses_provider_refs_only_without_similarity_fallback():
    with session_scope() as db:
        project = _new_project(db, "引用来源项目")
        ids = _seed(db, project.id)
        other = KbChunk(kb_id=ids["kb"], text="不应被猜中的资料", heading="其他章节")
        db.add(other)
        db.flush()
        node = db.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(
            db, project, node, created_by="tester",
            knowledge_provider=lambda _q: _context(ids, snippet="真实资料内容"),
        )
        kept = [row["id"] for row in plan.knowledge_refs]
        assert kept == [ids["chunk"]]
        assert other.id not in kept
        row = plan.knowledge_refs[0]
        assert row["page"] == 7
        assert row["score"] == 0.93
        assert row["rerank_score"] == 0.88
        assert row["trust_tier"] == "trusted"
        assert "页码 7" in row["trace"]
        text = plan_context_text(plan)
        assert ids["chunk"] in text and "页码 7" in text and "可信度 trusted" in text
        # 纯文本 provider 不再凭相似度凭空生成知识 ID。
        plan2 = generate_chapter_plan(
            db, project, node, created_by="tester",
            regenerate=True, knowledge_provider=lambda _q: "只有文本没有引用",
        )
        assert plan2.knowledge_refs == []


# ------------------------------------------------------- 项目作用域传递


@pytest.mark.usefixtures("db")
def test_project_and_package_scope_reach_all_generation_provider_paths(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v032-phase3-scope"
    client = _register(username)
    ref = _api_project(client, "作用域传递项目")
    with session_scope() as db:
        ids = _seed(db, ref)
    _set_credits(username, 100)

    calls: list[tuple[str | None, str | None]] = []

    def factory(db, user_id=None, *, project_id=None, package_id=None):
        calls.append((project_id, package_id))
        return lambda _q: ("知识文本", 0)

    report = SimpleNamespace(
        written=[ids["node"]], fallback=[], failed=[], suspicious={}, suspicious_count=0,
        knowledge_downgrades=0, verification={}, usage={}, unused_plan_refs={},
    )
    monkeypatch.setattr(routes_projects, "_knowledge_provider", factory)
    monkeypatch.setattr(routes_projects, "_generation_client",
                        lambda *_a, **_k: SimpleNamespace(available=True))
    monkeypatch.setattr(routes_projects, "generate_document", lambda *_a, **_k: report)
    monkeypatch.setattr(routes_projects.jobqueue, "submit", lambda *_a, **_k: None)

    node_id = ids["node"]
    assert client.post(f"/projects/{ref}/chapters/{node_id}/plan", json={}).status_code == 200
    assert client.post(
        f"/projects/{ref}/chapters/{node_id}/plan/confirm", json={"note": "确认"}
    ).status_code == 200
    assert client.post(f"/projects/{ref}/chapters/{node_id}/generate").status_code == 200
    assert client.post(f"/projects/{ref}/write").status_code == 200
    assert client.post(f"/projects/{ref}/generate", json={}).status_code == 202
    assert calls, "provider factory must be invoked"
    assert all(item == (ref, "pkg-1") for item in calls), calls


# ---------------------------------------------------------- worker 规划围栏


@pytest.mark.usefixtures("db")
def test_worker_rejects_unconfirmed_recorded_plan_unless_auto():
    with session_scope() as db:
        project = _new_project(db, "worker 围栏项目")
        ids = _seed(db, project.id)
        node = db.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db, project, node, created_by="tester")
        plan_id = plan.id
        recorded = {node.id: {"planning_id": plan_id, "planning_status": "DRAFT"}}
    with session_scope() as db:
        with pytest.raises(ValueError, match="尚未确认"):
            _resolve_worker_plan(db, project.id, "pkg-1", node.id, recorded[node.id], "review")
        auto_plan = _resolve_worker_plan(
            db, project.id, "pkg-1", node.id, recorded[node.id], "auto"
        )
        assert auto_plan is not None and auto_plan.id == plan_id
        auto_plan.status = "CONFIRMED"
        db.flush()
        db.expire_all()
        confirmed = _resolve_worker_plan(
            db, project.id, "pkg-1", node.id, recorded[node.id], "review"
        )
        assert confirmed is not None and confirmed.id == plan_id


@pytest.mark.usefixtures("db")
def test_async_commit_refreshes_provenance_inside_fenced_transaction():
    with session_scope() as db:
        project = _new_project(db, "异步溯源项目")
        node = OutlineNode(project_id=project.id, heading="技术方案", target_words=1200)
        db.add(node)
        db.flush()
        db.add(Section(node_id=node.id, body="", status="drafted", words=0))
        db.add(Fact(project_id=project.id, key="project.name", label="名称",
                    value="示例项目", status="confirmed", required=True))
        db.flush()
        plan = ChapterPlan(
            project_id=project.id, node_id=node.id, version=1, status="CONFIRMED",
            objective="目标", strategy=[],
        )
        db.add(plan)
        db.flush()
        job = Job(kind="generate", status="running", claim_token=TOKEN,
                  lease_expires_at=utc_now() + timedelta(seconds=300), attempts=1)
        db.add(job)
        db.flush()
        job_id, project_id, node_id = job.id, project.id, node.id
        plan_id = plan.id
    with session_scope() as db:
        jobqueue.acquire_generation_lease(
            db, project_id, job_id=job_id, owner_id="", ttl_seconds=300
        )
        lease_token = str(
            db.scalar(
                select(jobqueue.GenerationLease.token).where(
                    jobqueue.GenerationLease.project_id == project_id
                )
            )
        )
    with claim_context(TOKEN):
        commit_generation_chapter(
            job_id, project_id, lease_token,
            {"node_id": node_id, "section_id": "", "words": 8, "chapter_plan_id": plan_id},
            "示例项目正文已写入", "model",
        )
    with session_scope() as db:
        citations = list(
            db.scalars(select(FactCitation).where(FactCitation.project_id == project_id))
        )
        assert len(citations) == 1
        assert citations[0].fact_key == "project.name"
        assert citations[0].value == "示例项目"
        assert db.get(ChapterPlan, plan_id).status == "USED_FOR_GENERATION"


# ---------------------------------------------------------- 导出过期门禁


@pytest.mark.usefixtures("db")
def test_export_gate_blocks_stale_citations_and_force_audit_records_evidence():
    with session_scope() as db:
        project = _new_project(db, "过期引用导出项目")
        node = OutlineNode(project_id=project.id, heading="技术方案", position=1)
        db.add(node)
        db.flush()
        db.add(Section(node_id=node.id, body="旧值正文", status="drafted", words=4,
                       review_status="confirmed"))
        fact = Fact(project_id=project.id, key="project.name", label="名称",
                    value="旧值", status="confirmed", required=True)
        db.add(fact)
        db.flush()
        provenance.refresh_node(db, project.id, node.id, "旧值正文")
        fact.value = "新值"
        db.flush()
        project_id = project.id
    with session_scope() as db:
        with pytest.raises(ExportGateError) as error:
            enforce_export_gate(db, project_id, "docx")
        assert error.value.status == 409
        assert "已变更" in str(error.value)
    with session_scope() as db:
        assert enforce_export_gate(
            db, project_id, "docx", force=True, force_reason="业主书面确认继续"
        ) == 0
        audit_row = db.scalar(
            select(AuditLog).where(
                AuditLog.project_id == project_id, AuditLog.action == "export.force"
            )
        )
        assert audit_row is not None
        stale = audit_row.detail["stale_sections"]
        assert stale[0]["heading"] == "技术方案"
        assert stale[0]["evidence"][0]["value"] == "旧值"
        assert stale[0]["evidence"][0]["current_value"] == "新值"


# ---------------------------------------------------------- 检索预览路由


@pytest.mark.usefixtures("db")
def test_retrieval_preview_is_viewer_readable_and_traced(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    owner = _register("v032-phase3-owner")
    viewer = _register("v032-phase3-viewer")
    ref = _api_project(owner, "检索预览项目")
    added = owner.post(
        f"/projects/{ref}/members",
        json={"username": "v032-phase3-viewer", "role": "viewer"},
    )
    assert added.status_code in {200, 201}, added.text
    with session_scope() as db:
        ids = _seed(db, ref)

    seen: list[tuple[str | None, str | None]] = []

    def factory(db, user_id=None, *, project_id=None, package_id=None):
        seen.append((project_id, package_id))
        return lambda _q: _context(ids)

    monkeypatch.setattr(routes_projects, "_knowledge_provider", factory)
    response = viewer.get(
        f"/projects/{ref}/retrieval/preview", params={"q": "技术方案", "limit": 2}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert seen == [(ref, "pkg-1")]
    assert payload["project_id"] == ref
    assert len(payload["passages"]) == 1
    passage = payload["passages"][0]
    assert passage["chunk_id"] == ids["chunk"]
    assert passage["document_id"] == ids["document"]
    assert passage["page"] == 7
    assert passage["heading"] == "技术方案"
    assert passage["source"] == "企业资料.pdf"
    assert passage["text"] == "真实资料内容"
    assert passage["score"] == 0.93 and passage["rerank_score"] == 0.88
    assert passage["trust_tier"] == "trusted" and passage["degraded"] is False
    assert "页码 7" in passage["trace"]
    assert payload["diagnostics"] == [{"kb_id": ids["kb"], "status": "ok"}]
    with session_scope() as db:
        audit_row = db.scalar(
            select(AuditLog).where(
                AuditLog.project_id == ref, AuditLog.action == "retrieval.preview"
            )
        )
        assert audit_row is not None and audit_row.detail["count"] == 1


# ------------------------------------------------- 写后核对与数字白名单


@pytest.mark.usefixtures("db")
def test_suspicious_whitelist_narrows_to_plan_refs_and_unused_refs_are_reported():
    with session_scope() as db:
        project = _new_project(db, "写后核对项目")
        ids = _seed(db, project.id)
        second = Fact(project_id=project.id, key="budget.amount", label="预算",
                      value="87654321", status="confirmed", required=False)
        db.add(second)
        db.flush()
        node = db.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(
            db, project, node, created_by="tester",
            knowledge_provider=lambda _q: _context(ids),
        )
        plan.fact_refs = [row for row in plan.fact_refs if row.get("key") == "project.name"]
        db.flush()
        ledger = load_ledger(db, project.id)
    body = "项目名称示例项目，预算 87654321 元。"
    flagged = suspicious_numbers(body, ledger)
    assert "87654321" not in flagged  # 旧行为：只要在台账中就免标记
    narrowed = suspicious_numbers(body, ledger, plan)
    assert "87654321" in narrowed  # 新行为：不在规划引用中的台账数字仍需复核
    assert "123456" in suspicious_numbers("编号 123456", ledger, plan)
    unused = unused_plan_refs(body, plan)
    assert ids["chunk"] in unused["knowledge"]
    assert ids["score"] in unused["scores"]


@pytest.mark.usefixtures("db")
def test_retrieval_preview_preserves_unknown_trust_signals(monkeypatch):
    """未知可信度/降级状态必须是 null，不能伪装成已验证或未降级。"""
    from bidcraft.api.routes import projects as routes_projects

    owner = _register("v032-phase3-unknown-trust")
    ref = _api_project(owner, "未知可信度预览项目")
    with session_scope() as db:
        ids = _seed(db, ref)

    def factory(db, user_id=None, *, project_id=None, package_id=None):
        return lambda _q: SimpleNamespace(
            text="检索文本", downgrades=0, diagnostics=[],
            refs=[SimpleNamespace(
                id=ids["chunk"], chunk_id=ids["chunk"], document_id=ids["document"],
                page=7, heading="技术方案", source="企业资料.pdf",
                snippet="真实资料内容", score=0.93, rerank_score=None,
            )],
        )

    monkeypatch.setattr(routes_projects, "_knowledge_provider", factory)
    response = owner.get(
        f"/projects/{ref}/retrieval/preview", params={"q": "技术方案", "limit": 2}
    )
    assert response.status_code == 200, response.text
    passage = response.json()["passages"][0]
    assert passage["trust_tier"] is None
    assert passage["degraded"] is None
