
import pytest

from bidcraft import material_policy
from bidcraft.knowledge import Passage


def test_load_policy_reads_yaml_and_defaults():
    policy = material_policy.load_policy()
    assert set(policy) >= {"qualification", "performance", "personnel", "standard", "other"}
    assert policy["qualification"]["required"] == ["cert_no", "valid_to"]
    assert "contract_no" in policy["performance"]["required"]
    assert material_policy.kinds(policy) == sorted(policy)


def test_assess_ok_missing_expired():
    ok = material_policy.assess(
        {"kind": "qualification", "cert_no": "D1", "valid_to": "2099-01-01"}
    )
    assert ok.ok and ok.reason == ""
    missing = material_policy.assess({"kind": "qualification", "cert_no": "D1"})
    assert not missing.ok and "有效期至" in missing.reason
    expired = material_policy.assess(
        {"kind": "qualification", "cert_no": "D1", "valid_to": "2000-01-01"}
    )
    assert not expired.ok and expired.expired and "已过期" in expired.reason
    other = material_policy.assess({"kind": "other"})
    assert other.ok


def test_assess_unknown_kind_falls_back_to_other():
    verdict = material_policy.assess({"kind": "nonsense"})
    assert verdict.ok and verdict.kind == "nonsense"


def _kb_with_docs(docs):
    class _KB:
        location = "/kb"

        def __init__(self, options):
            self.options = options

    return _KB({"docs": docs})


def test_context_block_downgrades_untrusted():
    class _Row:
        pass

    row = _Row()
    row.location = "/kb"
    row.options = {
        "docs": {
            "a.md": {"kind": "qualification", "title": "资质A", "cert_no": "D1", "valid_to": "2099-01-01"},
            "b.md": {"kind": "performance", "title": "业绩B"},
        }
    }

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def __iter__(self):
            return iter(self.rows)

    class _DB:
        def scalars(self, _statement):
            return _Query([row])

    passages = [
        Passage(kb="k", source="/kb/a.md", text="有效正文A", score=0.9),
        Passage(kb="k", source="/kb/b.md", text="疑似编造正文B", score=0.8),
        Passage(kb="k", source="/kb/c.md", text="未登记正文C", score=0.7),
    ]
    block, downgraded = material_policy.context_block(_DB(), passages)
    assert downgraded == 2
    assert "有效正文A" in block
    assert "疑似编造正文B" not in block
    assert "[题录] 业绩B" in block and "未登记素材元数据" in block
    assert "[题录] c.md" in block


def test_context_block_empty():
    block, downgraded = material_policy.context_block(None, [])
    assert block == "" and downgraded == 0


def test_citation_includes_meta():
    text = material_policy.citation(
        "/kb/a.md",
        {"title": "资质A", "valid_to": "2026-01-01", "source_url": "https://x"},
        "缺少证书编号",
    )
    assert "资质A" in text and "2026-01-01" in text and "https://x" in text


def test_set_meta_validates_and_persists(db):
    from bidcraft.db import session_scope
    from bidcraft.models import KnowledgeBase

    with session_scope() as session:
        kb = KnowledgeBase(name="素材库", kind="local", provider="local", location="/tmp/kb")
        session.add(kb)
        session.flush()
        kb_id = kb.id
        record = material_policy.set_meta(
            session,
        kb,
        "a.md",
            {"kind": "performance", "title": "某项目", "contract_no": "HT-1",
             "owner": "业主", "amount": "100万", "acceptance": "合格"},
        )
        assert record["contract_no"] == "HT-1"
        assert material_policy.assess(record).ok
        stored = session.get(KnowledgeBase, kb_id)
        assert material_policy.doc_map(stored)["a.md"]["owner"] == "业主"

        for payload in (
            {"kind": "nonsense"},
            {"kind": "other", "valid_to": "2026/01/01"},
        ):
            with pytest.raises(material_policy.KnowledgeError):
                material_policy.set_meta(session, kb, "b.md", payload)
        with pytest.raises(material_policy.KnowledgeError):
            material_policy.set_meta(session, kb, "  ", {"kind": "other"})

        record2 = material_policy.set_meta(session, kb, "a.md", {"kind": "other", "title": "改名"})
        assert record2["title"] == "改名"
        assert len(material_policy.doc_map(kb)) == 1
