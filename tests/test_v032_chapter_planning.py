"""v0.32 Phase 1 Chapter Planning Engine：真实 DB 回归。

全部测试使用仓库现有 ``db`` fixture（SQLite 真库 + Alembic head），不做网络
调用；模型客户端均以 stub / 回退路径替代，核心 DB 行为全部为真实读写。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import inspect, select  # noqa: E402

from bidcraft.db import get_engine, session_scope  # noqa: E402
from bidcraft.generation.chapter_plan import (  # noqa: E402
    CONFIRMED,
    DRAFT,
    PROMPT_VERSION,
    REGENERATING,
    REJECTED,
    UPDATED,
    USED_FOR_GENERATION,
    build_plan_context,
    confirm_chapter_plan,
    generate_chapter_plan,
    load_active_plan,
    load_plan_history,
    mark_plan_used,
    plan_context_text,
    update_chapter_plan,
    validate_chapter_plan,
)
from bidcraft.models import (  # noqa: E402
    AuditLog,
    ChapterPlan,
    Fact,
    Job,
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


def _register(username: str) -> object:
    client = _client()
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "password": "v032-pass-1",
            "display_name": username,
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    return client


def _project(client, name: str) -> str:
    created = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _set_credits(username: str, amount: int) -> None:
    from bidcraft.models import User

    with session_scope() as db:
        from sqlalchemy import select as _select

        user = db.scalar(_select(User).where(User.username == username))
        user.credits = amount
        db.flush()


def _seed(db, project_id: str, *, heading: str = "技术方案") -> dict[str, str]:
    """构造真实评分项/要求/事实/知识/节点，返回它们的真实 ID。"""

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
    kb = KnowledgeBase(name=f"kb-{uuid.uuid4().hex}", kind="local", enabled=True)
    db.add(kb)
    db.flush()
    document = KbDocument(kb_id=kb.id, source="企业技术资料.pdf", status="ready")
    db.add(document)
    db.flush()
    chunk = KbChunk(
        kb_id=kb.id,
        document_id=document.id,
        heading="技术方案",
        text="技术方案需要逐条响应评分标准",
        source=document.source,
    )
    db.add(chunk)
    db.flush()
    project = db.get(Project, project_id)
    project.meta = {**(project.meta or {}), "package_id": "pkg-1"}
    db.flush()
    return {
        "node": node.id,
        "score": score.id,
        "requirement": requirement.id,
        "fact": fact.id,
        "kb": kb.id,
        "document": document.id,
        "chunk": chunk.id,
    }


def _new_project(db, name: str) -> Project:
    project = Project(id=f"p-{uuid.uuid4().hex}", name=name, package="weakcurrent-cabling")
    db.add(project)
    db.flush()
    return project


def _retrieval_context(ids: dict[str, str], *, text: str = "技术方案需要逐条响应评分标准"):
    """模拟检索侧 KnowledgeContext：真实 passage 引用 + 诊断。"""

    return SimpleNamespace(
        text=text,
        downgrades=1,
        refs=[
            {
                "id": ids["chunk"],
                "chunk_id": ids["chunk"],
                "kb_id": ids["kb"],
                "document_id": ids["document"],
                "page": 3,
                "heading": "技术方案",
                "source": "企业技术资料.pdf",
                "snippet": "技术方案需要逐条响应评分标准",
                "score": 0.91,
                "rerank_score": 0.87,
                "trust_tier": " trusted ",
                "degraded": False,
            }
        ],
        diagnostics=[{"kb_id": ids["kb"], "status": "ok"}],
    )


def _report(heading: str = "技术方案"):
    return SimpleNamespace(
        written=[heading], fallback=[], failed=[], suspicious={}, suspicious_count=0,
        knowledge_downgrades=0, verification={}, usage={},
    )


# ------------------------------------------------------------ 核心 DB 行为


def test_generate_plan_accepts_only_real_ids(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "真实 ID 项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(
            db_session,
            project,
            node,
            "突出实施路径",
            "tester",
            knowledge_provider=lambda _q: _retrieval_context(ids),
        )
        assert plan.version == 1
        assert plan.status == DRAFT
        assert plan.prompt_version == PROMPT_VERSION
        assert ids["score"] in [row["id"] for row in plan.score_points]
        assert ids["requirement"] in [row["id"] for row in plan.requirements]
        assert ids["fact"] in [row["id"] for row in plan.fact_refs]
        assert ids["chunk"] in [row["id"] for row in plan.knowledge_refs]
        knowledge_row = next(
            row for row in plan.knowledge_refs if row["id"] == ids["chunk"]
        )
        assert knowledge_row["chunk_id"] == ids["chunk"]
        assert knowledge_row["document_id"] == ids["document"]
        assert knowledge_row["page"] == 3
        assert knowledge_row["heading"] == "技术方案"
        assert knowledge_row["score"] == 0.91
        assert knowledge_row["rerank_score"] == 0.87
        assert knowledge_row["trust_tier"] == " trusted "
        assert knowledge_row["degraded"] is False
        assert ids["document"] in knowledge_row["trace"]
        assert "页码 3" in knowledge_row["trace"]
        assert plan.validation_errors == []
        assert "fallback" in plan.raw_response


def test_model_output_invalid_ids_are_dropped_and_recorded(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "模型伪造 ID 项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        output = json.dumps(
            {
                "objective": "模型给出的目标",
                "score_points": [
                    {"id": "fake-score", "name": "伪造评分"},
                    {"id": ids["score"], "name": "技术方案"},
                ],
                "requirements": [{"id": "fake-requirement", "text": "伪造条款"}],
                "fact_refs": [{"id": "fake-fact", "key": "fake.key"}],
                "knowledge_refs": [{"id": "fake-knowledge"}],
                "strategy": ["模型策略"],
            },
            ensure_ascii=False,
        )
        plan = generate_chapter_plan(
            db_session, project, node, created_by="tester", model_output=output
        )
        assert plan.objective == "模型给出的目标"
        assert "模型策略" in plan.strategy
        assert [row["id"] for row in plan.score_points] == [ids["score"]]
        assert plan.requirements == []
        assert plan.fact_refs == []
        assert plan.knowledge_refs == []
        assert any("剔除" in error for error in plan.validation_errors)
        assert "fake-score" in plan.raw_response
        assert "deterministic-fallback" not in plan.model_id


def test_validation_repairs_forged_node_id_in_descriptive_field(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "伪造章节 ID 项目")
        ids = _seed(db_session, project.id)
        other = _new_project(db_session, "另一个项目")
        foreign_node = OutlineNode(project_id=other.id, heading="外部章节")
        db_session.add(foreign_node)
        db_session.flush()
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db_session, project, node, created_by="tester")
        plan.strategy = [{"text": "引用外部章节", "node_id": foreign_node.id}]
        errors = validate_chapter_plan(db_session, plan)
        assert all(
            "node_id" not in row for row in plan.strategy if isinstance(row, dict)
        )
        assert any("章节" in error for error in errors)
        assert any("剔除" in error for error in errors)


def test_confirm_state_machine_and_idempotent_mark_used(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "状态机项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db_session, project, node, created_by="tester")
        confirm_chapter_plan(db_session, plan, "reviewer", "确认无误")
        assert plan.status == CONFIRMED
        assert plan.confirmed_by == "reviewer"
        assert plan.confirmed_at is not None
        mark_plan_used(db_session, plan)
        assert plan.status == USED_FOR_GENERATION
        assert plan.used_at is not None
        first_used = plan.used_at
        mark_plan_used(db_session, plan)
        assert plan.used_at == first_used
        with pytest.raises(ValueError):
            confirm_chapter_plan(db_session, plan)


def test_rejected_plan_cannot_be_confirmed(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "拒绝项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db_session, project, node, created_by="tester")
        plan.node_id = "node-does-not-exist"
        validate_chapter_plan(db_session, plan)
        assert plan.status == REJECTED
        with pytest.raises(ValueError):
            confirm_chapter_plan(db_session, plan)


def test_update_adds_version_and_invalidates_previous(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "更新版本项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        old = generate_chapter_plan(db_session, project, node, created_by="tester")
        confirm_chapter_plan(db_session, old, "reviewer")
        new = update_chapter_plan(
            db_session, project, node, {"strategy": ["新策略"]}, "editor"
        )
        assert new.version == 2
        assert new.status == UPDATED
        assert "新策略" in new.strategy
        assert db_session.get(ChapterPlan, old.id).status == REGENERATING
        assert load_active_plan(db_session, project.id, node.id).id == new.id
        assert [row.version for row in load_plan_history(db_session, project.id, node.id)] == [2, 1]


def test_regenerate_selects_latest_and_excludes_rejected(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "重新生成项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        first = generate_chapter_plan(db_session, project, node, created_by="tester")
        second = generate_chapter_plan(
            db_session, project, node, created_by="tester", regenerate=True
        )
        assert second.version == 2
        assert load_active_plan(db_session, project.id, node.id).id == second.id
        assert db_session.get(ChapterPlan, first.id).status == REGENERATING
        second.status = REJECTED
        db_session.flush()
        assert load_active_plan(db_session, project.id, node.id) is None


def test_plan_history_and_cross_scope_rejection(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "标段一项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db_session, project, node, created_by="tester")
        assert load_active_plan(db_session, "other-project", node.id) is None
        assert load_active_plan(db_session, project.id, node.id, "pkg-2") is None
        assert load_active_plan(db_session, project.id, node.id, "pkg-1").id == plan.id
        foreign_project = _new_project(db_session, "标段二项目")
        with pytest.raises(LookupError):
            generate_chapter_plan(db_session, foreign_project, node)
        history = load_plan_history(db_session, project.id, node.id)
        assert len(history) == 1
        assert history[0].package_id == "pkg-1"


def test_migration_created_plan_table_with_unique_and_indexes(db):
    engine = get_engine()
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        columns = {row["name"] for row in inspect(connection).get_columns("chapter_plans")}
        indexes = {
            row["name"] for row in inspect(connection).get_indexes("chapter_plans")
            if row.get("name")
        }
    assert "chapter_plans" in tables
    assert {"version", "status", "strategy", "validation_errors"} <= columns
    assert "ix_chapter_plans_project_node_status" in indexes
    assert "ix_chapter_plans_project_package" in indexes


def test_plan_context_text_contains_strategy_and_real_ids(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "提示词项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        generate_chapter_plan(db_session, project, node, created_by="tester")
        update_chapter_plan(db_session, project, node, {"strategy": ["先列评分响应矩阵"]}, "editor")
        active = load_active_plan(db_session, project.id, node.id)
        text = plan_context_text(active)
        assert "先列评分响应矩阵" in text
        assert ids["score"] in text
        assert "写作策略" in text


def test_prompt_version_is_exact_v3_for_advisory_sections(db):
    # 规划提示词新增历史调整/经验段属输出格式变化，版本必须可追溯。
    assert PROMPT_VERSION == "chapter_plan_v4"


def test_plan_context_renders_adjustments_and_lessons_as_advisory(db):
    lessons = (
        "【未审核建议经验（仅供复核参考，非核实事实；不会自动写入知识库或企业资产）】\n"
        "- [章节人工修订 id=rev-advisory source=section_revision] 历史复核意见仅供参考"
    )
    with session_scope() as db_session:
        project = _new_project(db_session, "建议经验提示词项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        generate_chapter_plan(db_session, project, node, created_by="tester")
        update_chapter_plan(
            db_session, project, node, {"strategy": ["补充策略"], "note": "人工调整备注"}, "editor"
        )
        active = load_active_plan(db_session, project.id, node.id)
        assert active is not None

        context = build_plan_context(db_session, project.id, node, lessons_text=lessons)
        assert context["lessons_text"] == lessons
        text = plan_context_text(active, lessons_text=lessons)
        assert "【历史人工调整（未审核建议经验，仅供参考，非招标事实）】" in text
        assert "人工调整备注" in text
        assert "【未审核建议经验（非核实事实" in text
        assert "历史复核意见仅供参考" in text
        assert "不得写入正文作为事实" in text


def test_chapter_messages_embed_plan_strategy(db):
    from bidcraft.generation.prompts import build_chapter_context, chapter_messages
    from bidcraft.ledger import render_ledger
    from bidcraft.rules.loader import load_package
    from bidcraft.schemas import FactLedger

    with session_scope() as db_session:
        project = _new_project(db_session, "正文策略项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        generate_chapter_plan(db_session, project, node, created_by="tester")
        update_chapter_plan(db_session, project, node, {"strategy": ["逐条列出参数响应值"]}, "editor")
        active = load_active_plan(db_session, project.id, node.id)
        plan_text = plan_context_text(active)
        kwargs = {
            "project_name": project.name,
            "heading": node.heading,
            "intent": node.intent,
            "target_words": node.target_words,
            "ledger_text": render_ledger(FactLedger(package=project.package)),
            "outline_text": "- 技术方案",
            "previous_summary": "",
            "package_title": load_package(project.package).title,
        }
        prompt = build_chapter_context(**kwargs, plan_text=plan_text)
        messages = chapter_messages(**kwargs, chapter_plan=active)
        assert "逐条列出参数响应值" in prompt
        assert "逐条列出参数响应值" in messages[1]["content"]
        assert "【章节规划" in prompt


# ---------------------------------------------------------------- API 路由


@pytest.mark.usefixtures("db")
def test_api_generate_get_confirm_and_history():
    client = _register("v032-plan-api")
    ref = _project(client, "规划 API 项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
    node_id = ids["node"]
    base = f"/projects/{ref}/chapters/{node_id}/plan"

    created = client.post(base, json={"user_instruction": "突出实施路径"})
    assert created.status_code == 200, created.text
    first = created.json()["plan"]
    assert first["version"] == 1
    assert first["status"] == DRAFT
    assert first["prompt_version"] == PROMPT_VERSION

    current = client.get(base)
    assert current.status_code == 200
    assert current.json()["plan"]["id"] == first["id"]
    assert current.json()["mode"] == "review"

    confirmed = client.post(f"{base}/confirm", json={"note": "确认无误"})
    assert confirmed.status_code == 200
    assert confirmed.json()["plan"]["status"] == CONFIRMED

    regenerated = client.post(f"{base}/regenerate", json={})
    assert regenerated.status_code == 200
    assert regenerated.json()["plan"]["version"] == 2

    updated = client.put(
        base, json={"strategy": ["人工补充策略"], "note": "调整"}
    )
    assert updated.status_code == 200
    assert updated.json()["plan"]["version"] == 3
    assert updated.json()["plan"]["status"] == UPDATED

    history = client.get(f"{base}/history")
    assert history.status_code == 200
    assert [row["version"] for row in history.json()["plans"]] == [3, 2, 1]


@pytest.mark.usefixtures("db")
def test_api_generate_alias_and_wrapped_updates():
    """统一契约路径与 updates 包装形式均可用，不依赖前端 404 回退。"""

    client = _register("v032-plan-alias")
    ref = _project(client, "规划契约项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
    base = f"/projects/{ref}/chapters/{ids['node']}/plan"

    created = client.post(f"{base}/generate", json={"user_instruction": "强调工序"})
    assert created.status_code == 200, created.text
    assert created.json()["plan"]["version"] == 1

    updated = client.put(
        base,
        json={"updates": {"strategy": ["按流水段组织"], "quality_criteria": ["逐项验收"]}},
    )
    assert updated.status_code == 200, updated.text
    plan = updated.json()["plan"]
    assert plan["version"] == 2
    assert plan["strategy"] == ["按流水段组织"]
    assert plan["quality_criteria"] == ["逐项验收"]


@pytest.mark.usefixtures("db")
def test_api_plan_rejects_foreign_node_and_cross_project():
    client = _register("v032-plan-scope")
    ref = _project(client, "本项目")
    other_ref = _project(client, "另一个项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
    base = f"/projects/{other_ref}/chapters/{ids['node']}/plan"
    assert client.get(base).status_code == 404
    assert client.post(base, json={}).status_code == 404
    assert client.get(f"/projects/{ref}/chapters/not-a-node/plan").status_code == 404


@pytest.mark.usefixtures("db")
def test_review_mode_blocks_generation_without_confirmed_plan(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v032-review-block"
    client = _register(username)
    ref = _project(client, "复核门禁项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
    _set_credits(username, 50)
    monkeypatch.setattr(
        routes_projects, "_generation_client",
        lambda *_a, **_k: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(
        routes_projects, "generate_document", lambda *_a, **_k: _report()
    )
    base = f"/projects/{ref}/chapters/{ids['node']}/plan"
    assert client.post(base, json={}).status_code == 200
    blocked = client.post(f"/projects/{ref}/chapters/{ids['node']}/generate")
    assert blocked.status_code == 409
    assert "规划尚未确认" in blocked.json()["detail"]

    client.post(f"{base}/confirm", json={"note": "已确认"})
    allowed = client.post(f"/projects/{ref}/chapters/{ids['node']}/generate")
    assert allowed.status_code == 200, allowed.text


@pytest.mark.usefixtures("db")
def test_review_strict_mode_blocks_chapter_without_any_plan(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v032-strict-block"
    client = _register(username)
    ref = _project(client, "严格复核项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
        project = db_session.get(Project, ref)
        project.meta = {**(project.meta or {}), "chapter_planning_mode": "review_strict"}
        db_session.flush()
    _set_credits(username, 50)
    monkeypatch.setattr(
        routes_projects, "_generation_client",
        lambda *_a, **_k: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(
        routes_projects, "generate_document", lambda *_a, **_k: _report()
    )
    blocked = client.post(f"/projects/{ref}/chapters/{ids['node']}/generate")
    assert blocked.status_code == 409
    assert "规划尚未确认" in blocked.json()["detail"]


@pytest.mark.usefixtures("db")
def test_auto_mode_allows_generation_with_warning(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v032-auto-warn"
    client = _register(username)
    ref = _project(client, "自动模式项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
        project = db_session.get(Project, ref)
        project.meta = {**(project.meta or {}), "chapter_planning_mode": "auto"}
        db_session.flush()
    _set_credits(username, 50)
    monkeypatch.setattr(
        routes_projects, "_generation_client",
        lambda *_a, **_k: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(
        routes_projects, "generate_document", lambda *_a, **_k: _report()
    )
    with session_scope() as db_session:
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db_session, db_session.get(Project, ref), node)
        plan_id = plan.id

    response = client.post(f"/projects/{ref}/chapters/{ids['node']}/generate")
    assert response.status_code == 200, response.text
    with session_scope() as db_session:
        row = db_session.scalar(
            select(AuditLog)
            .where(AuditLog.project_id == ref, AuditLog.action == "chapter.generate")
            .order_by(AuditLog.id.desc())
        )
        assert row.detail["planning_id"] == plan_id
        assert row.detail["chapter_plan_warnings"]
        assert db_session.get(ChapterPlan, plan_id).status == USED_FOR_GENERATION


@pytest.mark.usefixtures("db")
def test_generation_records_plan_version_in_audit_and_marks_used(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v032-gen-meta"
    client = _register(username)
    ref = _project(client, "生成溯源项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
        project = db_session.get(Project, ref)
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db_session, project, node, created_by=username)
        confirm_chapter_plan(db_session, plan, username)
        plan_id, version = plan.id, plan.version
    _set_credits(username, 50)
    monkeypatch.setattr(
        routes_projects, "_generation_client",
        lambda *_a, **_k: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(
        routes_projects, "generate_document", lambda *_a, **_k: _report()
    )
    response = client.post(f"/projects/{ref}/chapters/{ids['node']}/generate")
    assert response.status_code == 200, response.text
    with session_scope() as db_session:
        audit_row = db_session.scalar(
            select(AuditLog)
            .where(AuditLog.project_id == ref, AuditLog.action == "chapter.generate")
            .order_by(AuditLog.id.desc())
        )
        detail = audit_row.detail
        assert detail["planning_id"] == plan_id
        assert detail["planning_version"] == version
        assert detail["planning_status"] == CONFIRMED
        assert ids["score"] in detail["scoring_reference_ids"]
        assert ids["fact"] in detail["fact_reference_ids"]
        assert detail["prompt_version"] == PROMPT_VERSION
        assert db_session.get(ChapterPlan, plan_id).status == USED_FOR_GENERATION


@pytest.mark.usefixtures("db")
def test_async_generation_job_carries_plan_metadata(monkeypatch):
    from bidcraft.api.routes import projects as routes_projects

    username = "v032-async-plan"
    client = _register(username)
    ref = _project(client, "异步规划项目")
    with session_scope() as db_session:
        ids = _seed(db_session, ref)
        project = db_session.get(Project, ref)
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(db_session, project, node, created_by=username)
        confirm_chapter_plan(db_session, plan, username)
        plan_id = plan.id
    _set_credits(username, 100)
    monkeypatch.setattr(
        routes_projects, "_generation_client",
        lambda *_a, **_k: SimpleNamespace(available=True),
    )
    submitted = {}

    def fake_submit(runner, args, **kwargs):
        submitted["runner"] = runner
        submitted["args"] = args

    monkeypatch.setattr(routes_projects.jobqueue, "submit", fake_submit)
    started = client.post(f"/projects/{ref}/generate", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    with session_scope() as db_session:
        job = db_session.get(Job, job_id)
        payload = job.payload or {}
        assert payload["planning_id"] == plan_id
        assert payload["plans"][ids["node"]]["planning_id"] == plan_id
        assert payload["planning_status"] == CONFIRMED
        assert payload["prompt_version"] == PROMPT_VERSION
    assert submitted["args"][0] == job_id


def test_model_output_refs_without_real_uuid_are_dropped(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "伪造 ID 治理项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        output = json.dumps(
            {
                "score_points": [{"name": "无 ID 评分"}, {"id": "not-a-uuid", "name": "伪造"}],
                "requirements": [{"id": str(uuid.uuid4()), "text": "虚构 UUID"}],
                "fact_refs": [{"key": "fake.key", "label": "伪造字段"}],
                "knowledge_refs": [{"chunk_id": str(uuid.uuid4()), "kb_id": ids["kb"]}],
                "strategy": ["真实策略"],
            },
            ensure_ascii=False,
        )
        plan = generate_chapter_plan(
            db_session, project, node, created_by="tester", model_output=output
        )
        assert plan.score_points == []
        assert plan.requirements == []
        assert plan.fact_refs == []
        assert plan.knowledge_refs == []
        assert len([e for e in plan.validation_errors if "剔除" in e]) >= 4


def test_personal_knowledge_from_other_user_never_enters_plan(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "个人库隔离项目")
        ids = _seed(db_session, project.id)
        own_kb = KnowledgeBase(
            name=f"own-kb-{uuid.uuid4().hex}", kind="local", enabled=True,
            options={"personal": True, "user_id": "u-owner"},
        )
        foreign_kb = KnowledgeBase(
            name=f"foreign-kb-{uuid.uuid4().hex}", kind="local", enabled=True,
            options={"personal": True, "user_id": "u-someone-else"},
        )
        db_session.add_all([own_kb, foreign_kb])
        db_session.flush()
        own_chunk = KbChunk(kb_id=own_kb.id, text="授权个人资料", heading="授权个人资料")
        foreign_chunk = KbChunk(kb_id=foreign_kb.id, text="他人个人资料", heading="他人个人资料")
        db_session.add_all([own_chunk, foreign_chunk])
        db_session.flush()
        context = SimpleNamespace(
            text="资料", downgrades=0, diagnostics=[],
            refs=[
                {
                    "id": own_chunk.id, "chunk_id": own_chunk.id, "kb_id": own_kb.id,
                    "snippet": "授权个人资料", "score": 0.7,
                },
                {
                    "id": foreign_chunk.id, "chunk_id": foreign_chunk.id,
                    "kb_id": foreign_kb.id, "snippet": "他人个人资料", "score": 0.9,
                },
            ],
        )
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(
            db_session, project, node, created_by="tester", user_id="u-owner",
            knowledge_provider=lambda _q: context,
        )
        kept = [row["id"] for row in plan.knowledge_refs]
        assert own_chunk.id in kept
        assert foreign_chunk.id not in kept
        assert any("授权知识库" in error for error in plan.validation_errors)


def test_plan_context_text_renders_knowledge_trace(db):
    with session_scope() as db_session:
        project = _new_project(db_session, "知识溯源提示词项目")
        ids = _seed(db_session, project.id)
        node = db_session.get(OutlineNode, ids["node"])
        plan = generate_chapter_plan(
            db_session, project, node, created_by="tester",
            knowledge_provider=lambda _q: _retrieval_context(ids),
        )
        text = plan_context_text(plan)
        assert ids["chunk"] in text
        assert "文档 " in text
        assert "页码 3" in text
        assert "可信度" in text
