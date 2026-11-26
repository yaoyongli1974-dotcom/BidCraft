"""v0.32.1：报价摘要数值规则在真实 DB 审批链路的集成验证。

纯函数测试覆盖归一化与 AND 求值；本文件验证这些规则确实影响
``version_payload`` / ``review_version`` / ``pending_for_user`` 的生效级，
而不是只在 helper 里成立。无 mock、无 schema 变更。
"""

from __future__ import annotations

from bidcraft import boq
from bidcraft.db import session_scope
from bidcraft.models import BoqQuoteVersion, Project, ProjectMember


def _chain() -> list[dict]:
    return [
        {
            "approvers": [{"id": "u-rule-1", "name": "规则级审批人"}],
            "mode": "all",
            "rules": [
                {"field": "boq_rows", "op": "gte", "value": 10},
                {"field": "priced", "op": "gte", "value": 8},
            ],
        },
        {
            "approvers": [{"id": "u-rule-2", "name": "兜底审批人"}],
            "mode": "any",
        },
    ]


def _seed(session, *, summary: dict, name: str) -> tuple[Project, BoqQuoteVersion]:
    project = Project(
        id=f"p-v032-rules-{name}",
        name="规则审批项目",
        owner_id="u-rule-owner",
        meta={"approval_chain": _chain()},
    )
    version = BoqQuoteVersion(
        id=f"v-v032-rules-{name}",
        project_id=project.id,
        name=name,
        status="submitted",
        submitted_by="提交人",
        summary=summary,
        approvals=[],
    )
    members = [
        ProjectMember(
            project_id=project.id,
            user_id=user_id,
            role="editor",
            created_at=version.created_at,
        )
        for user_id in ("u-rule-1", "u-rule-2")
    ]
    session.add_all([project, version, *members])
    session.flush()
    return project, version


def test_version_payload_uses_summary_rules_for_stage_count(db):
    with session_scope() as session:
        project, version = _seed(
            session,
            name="filtered",
            summary={"total_amount": 100, "boq_rows": 12, "priced": 7, "unpriced": 5},
        )
        payload = boq.version_payload(version, project=project)
    assert payload["approval_stage_count"] == 1
    assert payload["approval_stage"] == 0


def test_review_version_allows_current_approver_after_rule_filters_stage(db):
    with session_scope() as session:
        project, version = _seed(
            session,
            name="review",
            summary={"total_amount": 100, "boq_rows": 12, "priced": 7, "unpriced": 5},
        )
        result = boq.review_version(
            session,
            project,
            version.id,
            actor="兜底审批人",
            actor_id="u-rule-2",
            decision="approve",
            note="规则级未命中，兜底审批",
        )
    assert result["status"] == "approved"
    approvals = result["approvals"]
    assert [record["stage"] for record in approvals] == [1]

    with session_scope() as session:
        wrong_project, wrong_version = _seed(
            session,
            name="review-wrong",
            summary={"total_amount": 100, "boq_rows": 12, "priced": 10, "unpriced": 2},
        )
        try:
            boq.review_version(
                session,
                wrong_project,
                wrong_version.id,
                actor="兜底审批人",
                actor_id="u-rule-2",
                decision="approve",
            )
        except boq.BoqForbiddenError:
            return
    raise AssertionError("规则命中后，非当前级审批人不得审批")


def test_pending_for_user_respects_summary_rules(db):
    with session_scope() as session:
        _, filtered = _seed(
            session,
            name="pending-filtered",
            summary={"total_amount": 100, "boq_rows": 12, "priced": 7},
        )
        _, active = _seed(
            session,
            name="pending-active",
            summary={"total_amount": 100, "boq_rows": 12, "priced": 10},
        )
        fallback_items = boq.pending_for_user(session, {"id": "u-rule-2", "role": "manager"})
        rule_items = boq.pending_for_user(session, {"id": "u-rule-1", "role": "manager"})

    fallback = {item["version_id"]: item for item in fallback_items}
    rule = {item["version_id"]: item for item in rule_items}
    assert fallback[filtered.id]["stage"] == 1
    assert fallback[filtered.id]["stage_count"] == 1
    assert active.id not in fallback  # 规则命中时当前级是 u-rule-1
    assert rule[active.id]["stage"] == 1
    assert rule[active.id]["stage_count"] == 2
