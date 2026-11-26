"""v0.33.1 Phase 4：企业知识库边界与工具轨迹脱敏回归。"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from bidcraft import boq, knowledge
from bidcraft.db import session_scope
from bidcraft.generation.chapter_plan import _allowed_kb_ids, scoped_knowledge_provider
from bidcraft.generation.tool_loop import run_tool_loop
from bidcraft.knowledge import tokens_for
from bidcraft.models import (
    BoqQuoteVersion,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    Project,
    ProjectMember,
    utcnow,
)

TOOLS = [{"name": "read", "description": "读取", "parameters": {}}]


def _enterprise_kb(db):
    kb = KnowledgeBase(
        name=f"ent-kb-{uuid.uuid4().hex}",
        kind="local",
        enabled=True,
        options={"enterprise_id": "enterprise-other"},
    )
    db.add(kb)
    db.flush()
    doc = KbDocument(
        kb_id=kb.id,
        source="enterprise-secret.txt",
        sha256=hashlib.sha256(b"only-in-enterprise").hexdigest(),
        status="ready",
        review_status="published",
    )
    db.add(doc)
    db.flush()
    chunk = KbChunk(
        id=str(uuid.uuid4()),
        kb_id=kb.id,
        document_id=doc.id,
        source=doc.source,
        ordinal=0,
        text="only-in-enterprise 企业专属哨兵值",
        tokens=" ".join(tokens_for("only-in-enterprise 企业专属哨兵值")),
        heading="企业专属",
        page_no=1,
        chunk_index=0,
        hash=hashlib.sha256(b"only-in-enterprise").hexdigest(),
    )
    db.add(chunk)
    db.flush()
    return kb, chunk


def test_search_rejects_enterprise_kb_without_explicit_authorization(db):
    with session_scope() as session:
        _kb, chunk = _enterprise_kb(session)
    with session_scope() as session:
        result = knowledge.search(session, "企业专属哨兵值", with_diagnostics=True)
        assert all(getattr(item, "chunk_id", None) != chunk.id for item in result)
        assert any("企业知识库" in str(item) for item in result.diagnostics["skipped"])


def test_allowed_kb_ids_excludes_enterprise_kb_without_membership(db):
    with session_scope() as session:
        kb, _chunk = _enterprise_kb(session)
        allowed = _allowed_kb_ids(session, user_id=None)
    assert kb.id not in allowed


def test_pending_inbox_ignores_invalid_member_role(db):
    with session_scope() as session:
        project_id = str(uuid.uuid4())
        session.add(
            Project(
                id=project_id,
                name="异常成员项目",
                owner_id="other-owner",
                meta={"approval_chain": [{"approvers": ["u-bad-role"], "mode": "all"}]},
            )
        )
        session.add(
            ProjectMember(project_id=project_id, user_id="u-bad-role", role="pending")
        )
        session.add(
            BoqQuoteVersion(
                project_id=project_id,
                name="v1",
                status="submitted",
                submitted_by="提交人",
                submitted_at=utcnow(),
                summary={"total_amount": 10.0},
            )
        )
        session.flush()
        items = boq.pending_for_user(session, {"id": "u-bad-role", "role": "manager"})
    assert items == []


def test_scoped_knowledge_provider_rejects_legacy_factory():
    def legacy_factory(_db, _user_id):
        return "全局知识"

    with pytest.raises(ValueError, match="作用域"):
        scoped_knowledge_provider(legacy_factory, None, "user-1", "project-1", None)


def test_nested_json_tool_result_is_redacted_in_trace():
    secret = "nested-" "secret123456"

    class Call:
        name = "read"
        id = "c1"

    class Client:
        def __init__(self):
            self.index = 0

        def complete_with_tools(self, _messages, _tools, **_kwargs):
            self.index += 1
            if self.index == 1:
                return type("R", (), {"content": "", "tool_calls": [Call()], "finish_reason": "tool_calls"})()
            return type("R", (), {"content": "完成", "tool_calls": [], "finish_reason": "stop"})()

    result = run_tool_loop(
        Client(),
        messages=[{"role": "user", "content": "核对"}],
        tools=TOOLS,
        executor=lambda _name, _args: {
            "id": "p1",
            "payload": json.dumps({"password": secret}),
        },
    )
    trace = result.tool_calls[0]
    assert secret not in json.dumps(trace, ensure_ascii=False)
    assert "***REDACTED***" in trace["result"]
