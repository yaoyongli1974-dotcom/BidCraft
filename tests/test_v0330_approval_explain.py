"""v0.33.0 RC-09：审批规则可解释性。

``_stage_explain`` 是阶段生效判定的唯一来源；单元矩阵覆盖全部 7 个操作符、
未知上下文、AND 与无条件阶段。集成部分验证 ``version_payload`` 与两类
``pending_for_user``（报价版本 / 商务标）真的带回命中与缺失数据，并验证
商务标审批读取的是落库 ``validation.quote`` 摘要（priced ≥ N）。
"""

from __future__ import annotations

from bidcraft import boq, commercial
from bidcraft.db import session_scope
from bidcraft.models import BoqQuoteVersion, Project, ProjectMember

EXPLAIN_KEYS = {"applicable", "conditions", "missing_data"}
CONDITION_KEYS = {"field", "op", "value", "actual", "hit", "missing"}


def _chain(*entries: dict) -> list[dict]:
    return boq.normalize_chain(list(entries))


def _explain(stage: dict, total=None, package=None, currency=None, *, summary=None, known=None):
    if known is None:
        known = isinstance(total, (int, float)) and not isinstance(total, bool)
    return boq._stage_explain(
        stage, total, package, currency, amount_known=known, summary=summary
    )


# --------------------------------------------------------------- 纯函数矩阵


def test_explain_shape_and_no_conditions():
    stage = _chain({"id": "u1"})[0]
    result = _explain(stage, 100, "pkg", "CNY")
    assert set(result) == EXPLAIN_KEYS
    assert result["applicable"] is True
    assert result["conditions"] == []
    assert result["missing_data"] == []


def test_explain_all_seven_ops_with_real_actual_values():
    cases = [
        ({"field": "priced", "op": "gte", "value": 8}, {"priced": 10}, True, False),
        ({"field": "priced", "op": "gte", "value": 8}, {"priced": 7}, False, False),
        ({"field": "priced", "op": "lte", "value": 8}, {"priced": 7}, True, False),
        ({"field": "priced", "op": "lte", "value": 8}, {"priced": 9}, False, False),
        ({"field": "package_id", "op": "eq", "value": "pkg-a"}, {}, True, False),
        ({"field": "package_id", "op": "eq", "value": "pkg-b"}, {}, False, False),
        ({"field": "package_id", "op": "ne", "value": "pkg-b"}, {}, True, False),
        ({"field": "package_id", "op": "ne", "value": "pkg-a"}, {}, False, False),
        ({"field": "currency", "op": "in", "value": ["CNY", "USD"]}, {}, True, False),
        ({"field": "currency", "op": "in", "value": ["USD"]}, {}, False, False),
        ({"field": "currency", "op": "not_in", "value": ["USD"]}, {}, True, False),
        ({"field": "currency", "op": "not_in", "value": ["CNY"]}, {}, False, False),
        ({"field": "package_id", "op": "contains", "value": "-a"}, {}, True, False),
        ({"field": "package_id", "op": "contains", "value": "-z"}, {}, False, False),
    ]
    ops = {op for _rule, _summary, _hit, _missing in cases for op in [_rule["op"]]}
    assert ops == set(boq.STAGE_RULE_OPS)
    for rule, summary, expected_hit, expected_missing in cases:
        stage = _chain({"id": "u1", "rules": [rule]})[0]
        result = _explain(stage, 100, "pkg-a", "CNY", summary=summary)
        assert len(result["conditions"]) == 1
        condition = result["conditions"][0]
        assert set(condition) == CONDITION_KEYS
        assert condition["field"] == rule["field"]
        assert condition["op"] == rule["op"]
        assert condition["hit"] is expected_hit, rule
        assert condition["missing"] is expected_missing, rule
        assert result["applicable"] is expected_hit, rule


def test_explain_unknown_values_are_conservative_and_listed():
    stage = _chain(
        {
            "id": "u1",
            "rules": [
                {"field": "priced", "op": "gte", "value": 8},
                {"field": "match_rate", "op": "lte", "value": 0.5},
            ],
        }
    )[0]
    result = _explain(stage, 100)
    assert result["applicable"] is True
    assert [condition["missing"] for condition in result["conditions"]] == [True, True]
    assert all(condition["hit"] for condition in result["conditions"])
    assert result["missing_data"] == ["priced", "match_rate"]


def test_explain_amount_package_currency_conditions_and_missing_data():
    stage = _chain(
        {
            "id": "u1",
            "min_amount": 1000,
            "packages": ["pkg-a"],
            "currencies": ["CNY"],
        }
    )[0]
    hit = _explain(stage, 2000, "pkg-a", "CNY")
    assert hit["applicable"] is True
    assert hit["conditions"] == [
        {"field": "min_amount", "op": "gte", "value": 1000, "actual": 2000, "hit": True, "missing": False},
        {"field": "package_id", "op": "in", "value": ["pkg-a"], "actual": "pkg-a", "hit": True, "missing": False},
        {"field": "currency", "op": "in", "value": ["CNY"], "actual": "CNY", "hit": True, "missing": False},
    ]
    assert hit["missing_data"] == []

    missed = _explain(stage, 100, "pkg-b", "USD")
    assert missed["applicable"] is False
    assert [condition["hit"] for condition in missed["conditions"]] == [False, False, False]

    # 金额未知 → 门槛保守命中，但明确标记为缺失数据。
    unknown = _explain(stage, None, "pkg-a", "CNY", known=False)
    assert unknown["conditions"][0]["hit"] is True
    assert unknown["conditions"][0]["missing"] is True
    assert unknown["missing_data"] == ["min_amount"]


def test_explain_and_semantics_and_delegation(monkeypatch):
    stage = _chain(
        {
            "id": "u1",
            "rules": [
                {"field": "priced", "op": "gte", "value": 8},
                {"field": "unpriced", "op": "lte", "value": 2},
            ],
        }
    )[0]
    ok = _explain(stage, 100, "pkg", "CNY", summary={"priced": 10, "unpriced": 1})
    assert ok["applicable"] is True
    bad = _explain(stage, 100, "pkg", "CNY", summary={"priced": 7, "unpriced": 1})
    assert bad["applicable"] is False
    assert [condition["hit"] for condition in bad["conditions"]] == [False, True]

    calls: list[tuple] = []

    def fake_explain(*args, **kwargs):
        calls.append((args, kwargs))
        return {"applicable": False, "conditions": [], "missing_data": []}

    monkeypatch.setattr(boq, "_stage_explain", fake_explain)
    assert boq._stage_applies(stage, 100, "pkg", "CNY", amount_known=True, summary={}) is False
    assert calls
    assert calls[0][0][1:] == (100, "pkg", "CNY")
    assert calls[0][1] == {"amount_known": True, "summary": {}}


# --------------------------------------------------------------- 集成回显


def _seed(
    session, *, name: str, summary: dict, chain: list[dict], approvals: list | None = None
) -> tuple[Project, BoqQuoteVersion]:
    project = Project(
        id=f"p-explain-{name}",
        name="解释项目",
        owner_id="u-explain-owner",
        meta={"approval_chain": chain},
    )
    version = BoqQuoteVersion(
        id=f"v-explain-{name}",
        project_id=project.id,
        name=name,
        status="submitted",
        summary=summary,
        approvals=approvals or [],
    )
    session.add_all([project, version])
    session.flush()
    return project, version


def test_version_payload_exposes_real_hit_and_missing_data(db):
    chain = _chain(
        {
            "approvers": [{"id": "u-explain-1", "name": "规则审批"}],
            "rules": [{"field": "priced", "op": "gte", "value": 8}],
        },
        {"approvers": [{"id": "u-explain-2", "name": "兜底审批"}]},
    )
    with session_scope() as session:
        project, version = _seed(
            session,
            name="payload",
            summary={"total_amount": 100, "priced": 7},
            chain=chain,
        )
        payload = boq.version_payload(version, project=project)
    assert payload["approval_stage_count"] == 1
    explanations = payload["approval_explain"]
    assert len(explanations) == len(chain)
    assert explanations[0]["index"] == 0
    assert explanations[0]["applicable"] is False
    condition = explanations[0]["conditions"][0]
    assert condition["field"] == "priced"
    assert condition["op"] == "gte"
    assert condition["value"] == 8
    assert condition["actual"] == 7
    assert condition["hit"] is False
    assert condition["missing"] is False
    assert explanations[0]["missing_data"] == []
    assert explanations[1]["applicable"] is True


def test_boq_pending_exposes_current_stage_explanation(db):
    # 规则挂在第二级：报价摘要缺 priced 时保守命中，该级成为当前待审级。
    chain = _chain(
        {"approvers": [{"id": "u-explain-1", "name": "前置审批"}]},
        {
            "approvers": [{"id": "u-explain-2", "name": "规则审批"}],
            "rules": [{"field": "priced", "op": "gte", "value": 8}],
        },
    )
    with session_scope() as session:
        _, version = _seed(
            session,
            name="pending",
            summary={"total_amount": 100},
            chain=chain,
            approvals=[{"decision": "approve", "stage": 0, "user_id": "u-explain-1"}],
        )
        session.add(
            ProjectMember(project_id=version.project_id, user_id="u-explain-2", role="editor")
        )
        session.flush()
        items = boq.pending_for_user(session, {"id": "u-explain-2", "role": "manager"})
    item = {entry["version_id"]: entry for entry in items}[version.id]
    explain = item["approval_explain"]
    assert explain["index"] == 1
    assert explain["applicable"] is True
    assert explain["conditions"][0]["field"] == "priced"
    assert explain["conditions"][0]["op"] == "gte"
    assert explain["conditions"][0]["value"] == 8
    assert explain["conditions"][0]["actual"] is None
    assert explain["conditions"][0]["missing"] is True
    assert explain["conditions"][0]["hit"] is True
    assert explain["missing_data"] == ["priced"]


def test_commercial_pending_and_review_use_stored_quote_summary(db):
    """priced ≥ 8 的规则落在 validation.quote 上；商务标必须按它选当前级。"""

    from bidcraft.models import CommercialDoc, utcnow

    with session_scope() as session:
        project = Project(
            id="p-explain-commercial",
            name="商务解释项目",
            owner_id="u-explain-owner",
            meta={
                "approval_chain": _chain(
                    {
                        "approvers": [{"id": "u-explain-1", "name": "规则审批"}],
                        "rules": [{"field": "priced", "op": "gte", "value": 8}],
                    },
                    {"approvers": [{"id": "u-explain-2", "name": "兜底审批"}]},
                )
            },
        )
        session.add(project)
        for user_id in ("u-explain-1", "u-explain-2"):
            session.add(
                ProjectMember(project_id=project.id, user_id=user_id, role="editor")
            )
        doc = CommercialDoc(
            id="c-explain-1",
            project_id=project.id,
            name="商务标",
            status="submitted",
            submitted_by="提交人",
            submitted_at=utcnow(),
            validation={
                "quote": {
                    "total_amount": 100,
                    "priced": 7,
                    "currency": "CNY",
                    "package_id": None,
                },
                "approvals": [],
            },
        )
        session.add(doc)
        session.flush()
        items = commercial.pending_for_user(session, {"id": "u-explain-2", "role": "manager"})
    item = {entry["version_id"]: entry for entry in items}[doc.id]
    explain = item["approval_explain"]
    # priced=7 → 规则级未命中，当前待审级切到原链第 1 级（兜底级）。
    assert explain["index"] == 1
    assert explain["applicable"] is True
    assert explain["conditions"] == []
    assert explain["missing_data"] == []

    # 同一规则命中（priced=10）时，任务落到规则审批人，说明带回真实命中值。
    with session_scope() as session:
        doc = session.get(CommercialDoc, doc.id)
        doc.validation["quote"]["priced"] = 10
        session.flush()
        first = commercial.pending_for_user(session, {"id": "u-explain-1", "role": "manager"})
        second = commercial.pending_for_user(session, {"id": "u-explain-2", "role": "manager"})
    first_item = {entry["version_id"]: entry for entry in first}[doc.id]
    assert doc.id not in {entry["version_id"] for entry in second}
    explain_hit = first_item["approval_explain"]
    assert explain_hit["index"] == 0
    assert explain_hit["conditions"][0]["actual"] == 10
    assert explain_hit["conditions"][0]["hit"] is True
    assert explain_hit["conditions"][0]["missing"] is False
