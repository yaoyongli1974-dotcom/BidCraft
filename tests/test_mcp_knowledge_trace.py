from __future__ import annotations

from bidcraft import mcp
from bidcraft.knowledge import Passage


def test_tool_search_knowledge_exposes_document_trace(monkeypatch):
    passage = Passage(
        kb="企业知识库",
        source="施工方案.pdf",
        text="综合布线施工要求",
        score=0.9,
        document_id="doc-1",
        page=2,
        heading="布线施工",
        kb_id="kb-1",
    )
    monkeypatch.setattr(mcp.knowledge, "search", lambda db, query, limit: [passage])

    payload = mcp._tool_search_knowledge(None, {"query": "综合布线"})

    assert payload[0]["kb_id"] == "kb-1"
    assert payload[0]["document_id"] == "doc-1"
    assert payload[0]["page"] == 2
    assert payload[0]["heading"] == "布线施工"
    assert payload[0]["text"] == "综合布线施工要求"
