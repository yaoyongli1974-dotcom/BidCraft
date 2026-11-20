
import datetime as dt

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
    # other 不是免检通道：没有可追溯出处时降级
    other = material_policy.assess({"kind": "other"})
    assert not other.ok and "官方出处" in other.reason
    assert material_policy.assess({"kind": "other", "source_url": "https://x"}).ok


def test_assess_unknown_or_missing_kind_fails_closed():
    """拼错/缺失的 kind 不得落回 other（required 为空会整段放行）。"""
    typo = material_policy.assess({"kind": "qualifcation"})
    assert not typo.ok and typo.kind == "qualifcation"
    assert "未知素材类型" in typo.reason

    missing = material_policy.assess({"cert_no": "D1", "valid_to": "2099-01-01"})
    assert not missing.ok
    assert "缺少素材类型" in missing.reason

    # 覆盖包被隐藏后，旧元数据的自定义 kind 同样降级
    hidden = material_policy.assess(
        {"kind": "custom_old"},
        policy={"other": {"label": "其他素材", "required": []}},
    )
    assert not hidden.ok and "未知素材类型" in hidden.reason


BJ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


def _freeze(monkeypatch, moment: dt.datetime) -> None:
    monkeypatch.setattr(material_policy, "_now", lambda: moment)


def test_today_uses_beijing_date(monkeypatch):
    # 北京时间 00:30 对应 UTC 前一日 16:30，按 UTC 取日期会晚一天
    _freeze(monkeypatch, dt.datetime(2026, 11, 20, 0, 30, tzinfo=BJ))
    assert material_policy._today() == dt.date(2026, 11, 20)


def test_assess_boundary_beijing_early_morning(monkeypatch):
    # 证书有效期至昨天：北京时间今晨 00:30 必须判过期
    _freeze(monkeypatch, dt.datetime(2026, 11, 20, 0, 30, tzinfo=BJ))
    verdict = material_policy.assess(
        {"kind": "qualification", "cert_no": "D1", "valid_to": "2026-11-19"}
    )
    assert not verdict.ok and verdict.expired
    assert "2026-11-19" in verdict.reason

    # 当日到期当天仍有效（valid_to 与本地日期同日）
    same_day = material_policy.assess(
        {"kind": "qualification", "cert_no": "D1", "valid_to": "2026-11-20"}
    )
    assert same_day.ok and not same_day.expired


def test_assess_boundary_before_local_eight_am(monkeypatch):
    # 07:59 仍属北京 20 日（UTC 还是 19 日 23:59）
    _freeze(monkeypatch, dt.datetime(2026, 11, 20, 7, 59, tzinfo=BJ))
    verdict = material_policy.assess(
        {
            "kind": "personnel",
            "cert_no": "P1",
            "social_months": "6",
            "locked": "否",
            "valid_to": "2026-11-19",
        }
    )
    assert not verdict.ok and verdict.expired


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
        # 缺 kind 不再静默落成 other
        for payload in ({}, {"kind": ""}, {"kind": "   "}):
            with pytest.raises(material_policy.KnowledgeError) as excinfo:
                material_policy.set_meta(session, kb, "c.md", payload)
            assert "素材类型必填" in str(excinfo.value)

        record2 = material_policy.set_meta(session, kb, "a.md", {"kind": "other", "title": "改名"})
        assert record2["title"] == "改名"
        assert len(material_policy.doc_map(kb)) == 1


def test_context_block_downgrades_unknown_kind(monkeypatch):
    """读取侧失败关闭：未知 kind 的片段只出题录，不注入正文。"""
    monkeypatch.setattr(
        material_policy,
        "find_meta",
        lambda db, source: (None, {"kind": "qualifcation", "title": "资质X"}),
    )
    block, downgraded = material_policy.context_block(
        None, [Passage(kb="k", source="/kb/x.md", text="某证书有效期已过正文", score=0.9)]
    )
    assert downgraded == 1
    assert "某证书有效期已过正文" not in block
    assert "[题录] 资质X" in block and "未知素材类型" in block

def test_context_block_downgrades_unattributed_other(monkeypatch):
    """语义层失败关闭：other 没有出处也不能整段注入。"""
    monkeypatch.setattr(
        material_policy,
        "find_meta",
        lambda db, source: (None, {"kind": "other", "title": "某扫描件"}),
    )
    block, downgraded = material_policy.context_block(
        None, [Passage(kb="k", source="/kb/scan.pdf", text="一页资质证书扫描件正文", score=0.9)]
    )
    assert downgraded == 1
    assert "一页资质证书扫描件正文" not in block
    assert "[题录] 某扫描件" in block and "未注入正文" in block

    monkeypatch.setattr(
        material_policy,
        "find_meta",
        lambda db, source: (None, {"kind": "other", "title": "行业白皮书", "source_url": "https://x"}),
    )
    block2, downgraded2 = material_policy.context_block(
        None, [Passage(kb="k", source="/kb/paper.md", text="行业背景正文", score=0.9)]
    )
    assert downgraded2 == 0 and "行业背景正文" in block2
