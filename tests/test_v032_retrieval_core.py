"""v0.32 Phase 3 检索核心：块级溯源、RRF 融合、范围/诊断、分档注入与 provider 契约。

全部用例走真库或纯函数；上游失败用 monkeypatch 模拟，不用 mock 充当核心证据。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from bidcraft import knowledge, material_policy, mcp
from bidcraft.knowledge import Passage, tokens_for
from bidcraft.services import job_support


def _chunk_kb(session, *, name="块库", options=None, text="综合布线施工要求"):
    from bidcraft.models import KbChunk, KbDocument, KnowledgeBase

    kb = KnowledgeBase(
        name=name,
        kind="local",
        provider="local",
        enabled=True,
        location="/tmp/kb-v032",
        options=options or {},
    )
    session.add(kb)
    session.flush()
    doc = KbDocument(kb_id=kb.id, source="plan.md", sha256="sha", status="ready", kind="other")
    session.add(doc)
    session.flush()
    chunk = KbChunk(
        kb_id=kb.id,
        document_id=doc.id,
        source="plan.md",
        ordinal=0,
        text=text,
        tokens=" ".join(tokens_for(text)),
        heading="布线施工",
        page_no=2,
        chunk_index=7,
    )
    session.add(chunk)
    session.flush()
    return kb, doc, chunk


def test_passage_chunk_trace_and_mcp_payload(db, monkeypatch):
    from bidcraft.db import session_scope

    with session_scope() as session:
        kb, _doc, chunk = _chunk_kb(session)
        hits = knowledge.search(session, "综合布线", limit=5, kb_ids=[kb.id])
        assert hits and hits[0].chunk_id == chunk.id
        assert hits[0].chunk_index == 7
        label = knowledge.trace_label(hits[0])
        assert f"块 {chunk.id}#7" in label

        monkeypatch.setattr(mcp.knowledge, "search", lambda db_, query, limit: hits)
        payload = mcp._tool_search_knowledge(None, {"query": "综合布线"})
        assert payload[0]["chunk_id"] == chunk.id
        assert payload[0]["chunk_index"] == 7
        assert payload[0]["document_id"] == chunk.document_id
        assert "chunk_id" not in payload[0] or payload[0]["chunk_id"] != "fake-chunk-id"


def test_rerank_hybrid_is_pure_and_stable(db):
    first = Passage(kb="A", source="a", text="A", score=0.1, kb_id="a", freshness=1.0)
    second = Passage(kb="A", source="b", text="B", score=0.2, kb_id="a")
    third = Passage(kb="B", source="c", text="C", score=0.99, kb_id="b")
    original = [first.score, second.score, third.score]

    ranked = knowledge.rerank_hybrid([first, second, third])
    assert [item.text for item in ranked] == ["B", "C", "A"]
    assert sorted(item.score for item in ranked) == sorted(original)
    assert all(item.rerank_score is not None for item in ranked)
    assert [first.score, second.score, third.score] == original  # 输入对象未被改写

    multi = Passage(
        kb="A",
        source="d",
        text="D",
        score=0.5,
        kb_id="a",
        score_components={"bigram": 0.9, "cosine": 0.8},
    )
    fused = knowledge.rerank_hybrid([first, multi])
    assert fused[0].text == "D"  # 两条分量榜各自贡献 RRF
    assert len(knowledge.rerank_hybrid([first, multi], limit=1)) == 1
    assert knowledge.rerank_hybrid([]) == []


def test_search_scope_fail_closed_and_diagnostics(db, monkeypatch):
    from bidcraft.db import session_scope

    with session_scope() as session:
        scoped, _, _ = _chunk_kb(
            session,
            name="范围库",
            options={"project_id": "P1", "package_id": "B1"},
        )
        broken = knowledge.KnowledgeBase(
            name="故障库", kind="api", provider="generic", enabled=True,
            location="http://broken",
        )
        session.add(broken)
        session.flush()

        def _explode(*_args, **_kwargs):
            raise knowledge.KnowledgeError("上游 500")

        monkeypatch.setattr(knowledge, "search_api", _explode)

        missing_scope = knowledge.search(
            session, "综合布线", limit=5, kb_ids=[scoped.id, broken.id], with_diagnostics=True
        )
        assert isinstance(missing_scope, knowledge.SearchResult)
        assert not missing_scope
        assert any(item["kb_id"] == scoped.id and "未提供" in item["reason"]
                   for item in missing_scope.diagnostics["skipped"])
        assert [item["kb_id"] for item in missing_scope.diagnostics["failed"]] == [broken.id]

        mismatched = knowledge.search(
            session, "综合布线", limit=5, kb_ids=[scoped.id],
            project_id="P2", package_id="B1", with_diagnostics=True,
        )
        assert not mismatched
        assert any("不匹配" in item["reason"] for item in mismatched.diagnostics["skipped"])

        matched = knowledge.search(
            session, "综合布线", limit=5, kb_ids=[scoped.id],
            project_id="P1", package_id="B1", with_diagnostics=True,
        )
        assert {item.chunk_id for item in matched} == _chunk_id_for(session, scoped)
        assert matched.diagnostics["searched"][0]["kb_id"] == scoped.id

        plain = knowledge.search(session, "综合布线", limit=5, kb_ids=[scoped.id],
                                 project_id="P1", package_id="B1")
        assert type(plain) is list
        assert plain


def _chunk_id_for(session, kb):
    from sqlalchemy import select

    from bidcraft.models import KbChunk

    return set(session.scalars(select(KbChunk.id).where(KbChunk.kb_id == kb.id)))


def test_material_tiers_graduated_injection(monkeypatch):
    metas = {
        "full.md": {"kind": "other", "title": "行业背景", "source_url": "https://x"},
        "excerpt.md": {
            "kind": "qualification", "title": "证书", "cert_no": "D-1",
            "source_url": "https://x",
        },
        "title.md": {"kind": "performance", "title": "业绩"},
    }
    monkeypatch.setattr(
        material_policy, "find_meta", lambda db, source, **kwargs: (None, metas.get(source, {}))
    )
    long_text = "EXCERPT_SENTINEL" + "乙" * 300
    passages = [
        Passage(kb="k", source="full.md", text="FULL_BODY", score=0.9),
        Passage(kb="k", source="excerpt.md", text=long_text, score=0.8),
        Passage(kb="k", source="title.md", text="TITLE_BODY", score=0.7),
        Passage(kb="k", source="", text="SUPPRESSED_BODY", score=0.6),
    ]
    verdicts = material_policy.passage_verdicts(None, passages)
    assert [item.tier for item in verdicts] == ["full", "excerpt", "title_only", "suppressed"]
    assert [item.downgraded for item in verdicts] == [False, True, True, True]
    assert set(material_policy.TIERS) == {"full", "excerpt", "title_only", "suppressed"}

    block, downgraded = material_policy.context_block(None, passages)
    assert downgraded == 3
    assert "FULL_BODY" in block
    assert "EXCERPT_SENTINEL" in block and long_text not in block
    assert "TITLE_BODY" not in block and "[题录] 业绩" in block
    assert "SUPPRESSED_BODY" not in block and "未命名素材" not in block


def test_api_passages_map_real_provider_ids(db, monkeypatch):
    from bidcraft.db import session_scope
    from bidcraft.models import KnowledgeBase

    with session_scope() as session:
        kb = KnowledgeBase(
            name="企业知识库", kind="api", provider="ragflow", enabled=True,
            location="http://retrieval.example", dataset_id="ds",
        )
        session.add(kb)
        session.flush()

        def _ragflow(_url, **_kwargs):
            response = httpx.Response(
                200,
                json={"data": {"chunks": [
                    {
                        "id": "seg-1", "document_id": "doc-9", "document_keyword": "方案.pdf",
                        "title": "施工方案", "page": 2, "content": "真实正文", "similarity": 0.8,
                    },
                    {"id": "seg-2", "content": "无出处正文", "similarity": 0.7},
                ]}},
            )
            response.request = httpx.Request("POST", _url)
            return response

        monkeypatch.setattr(knowledge.httpx, "post", _ragflow)
        hits = knowledge.search_api(session, kb, "方案", 5)
        assert hits[0].chunk_id == "seg-1"
        assert hits[0].document_id == "doc-9"
        assert hits[0].source == "方案.pdf"
        assert hits[0].page == 2 and hits[0].heading == "施工方案"
        assert hits[1].source == "" and hits[1].document_id is None
        assert all(hit.source != kb.name for hit in hits)

        block, downgraded = material_policy.context_block(session, hits)
        assert downgraded == 2
        assert "方案.pdf" in block and "无出处正文" not in block

        kb.provider = "dify"

        def _dify(_url, **_kwargs):
            response = httpx.Response(
                200,
                json={"records": [{
                    "segment": {
                        "id": "s3", "content": "Dify正文",
                        "document": {"id": "d3", "name": "dify.pdf"}, "position": 4,
                    },
                    "score": 0.6,
                }]},
            )
            response.request = httpx.Request("POST", _url)
            return response

        monkeypatch.setattr(knowledge.httpx, "post", _dify)
        dify_hits = knowledge.search_api(session, kb, "方案", 5)
        assert dify_hits[0].chunk_id == "s3"
        assert dify_hits[0].document_id == "d3"
        assert dify_hits[0].chunk_index == 4
        assert dify_hits[0].source == "dify.pdf"


def test_job_support_knowledge_context_contract(db, monkeypatch):
    captured: dict = {}
    passage = Passage(
        kb="库", source="doc.pdf", text="真实正文", score=0.8,
        kb_id="kb-1", document_id="doc-1", chunk_id="seg-1", chunk_index=3,
        page=1, heading="章节", rerank_score=0.42,
    )

    def _search(_db, _query, **kwargs):
        captured.update(kwargs)
        return knowledge.SearchResult(
            [passage], diagnostics={"failed": [{"kb_id": "bad", "error": "upstream"}]}
        )

    monkeypatch.setattr(job_support.knowledge, "search", _search)
    monkeypatch.setattr(
        material_policy, "find_meta",
        lambda db_, source, **kwargs: (
            None,
            {"kind": "other", "title": "文档", "source_url": "https://x"},
        ),
    )

    ctx = job_support.knowledge_context(db, "检索", project_id="P", package_id="B")
    assert captured["project_id"] == "P" and captured["package_id"] == "B"
    assert captured["with_diagnostics"] is True
    assert isinstance(ctx, tuple) and ctx == (ctx.text, ctx.downgrades)
    assert list(ctx) == [ctx.text, ctx.downgrades]
    assert ctx.downgrades == 0
    assert ctx.diagnostics["failed"][0]["kb_id"] == "bad"
    ref = ctx.refs[0]
    assert ref["id"] == "seg-1" and ref["chunk_id"] == "seg-1"
    assert ref["kb_id"] == "kb-1" and ref["document_id"] == "doc-1"
    assert ref["page"] == 1 and ref["heading"] == "章节"
    assert ref["rerank_score"] == 0.42
    assert ref["trust_tier"] == "full" and ref["degraded"] is False

    provider_ctx = job_support._knowledge_provider(
        db, project_id="P", package_id="B"
    )("检索")
    assert isinstance(provider_ctx, job_support.KnowledgeContext)
    assert provider_ctx.text == ctx.text


def test_knowledge_agent_evidence_traces(db, monkeypatch):
    from bidcraft.orchestration.agents.knowledge import KnowledgeAgent

    passage = Passage(
        kb="库", source="doc.pdf", text="资料正文", score=0.7,
        kb_id="kb-1", document_id="doc-1", chunk_id="seg-9", chunk_index=2,
        page=4, heading="实施方法",
    )
    monkeypatch.setattr(knowledge, "search", lambda *args, **kwargs: [passage])
    monkeypatch.setattr(
        material_policy, "find_meta",
        lambda db_, source, **kwargs: (None, {"kind": "other", "title": "白皮书"}),
    )
    output = KnowledgeAgent().execute(
        db, SimpleNamespace(name="项目"), {"payload": {"query": "检索"}}
    )
    item = output.result["passages"][0]
    assert item["document_id"] == "doc-1" and item["page"] == 4
    assert item["heading"] == "实施方法" and item["chunk_id"] == "seg-9"
    assert item["chunk_index"] == 2
    assert item["trust_tier"] == "title_only" and item["degraded"] is True
    assert output.evidence[0] == item


def test_cli_provider_never_uses_raw_context_block(db, monkeypatch):
    from bidcraft import cli

    monkeypatch.setattr(job_support.knowledge, "search", lambda *args, **kwargs: [])

    def _raw(*_args, **_kwargs):
        raise AssertionError("生成链不得直接调用未分级的 knowledge.context_block")

    monkeypatch.setattr(knowledge, "context_block", _raw)
    text, downgrades = cli._knowledge_provider(db)("检索")
    assert text == "" and downgrades == 0


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_rerank_hybrid_limit(db, limit):
    passages = [
        Passage(kb="A", source=str(index), text=str(index), score=index / 10, kb_id="a")
        for index in range(1, 4)
    ]
    assert len(knowledge.rerank_hybrid(passages, limit=limit)) == limit
