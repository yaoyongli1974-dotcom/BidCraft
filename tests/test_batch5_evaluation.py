"""Batch 5 评估守护：多包件商务完整性按最差值聚合、缺失项取并集。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from bidcraft.db import session_scope
from bidcraft.evaluation.commercial import collect_commercial_completeness
from bidcraft.models import BidPackage, CommercialDoc, Project, utcnow

pytestmark = pytest.mark.usefixtures("db")


def _doc(db, project_id, *, package_id, missing, updated_at, status="draft"):
    doc = CommercialDoc(
        project_id=project_id,
        package_id=package_id,
        name=f"商务标-{package_id}",
        status=status,
        sections={},
        validation={
            "quote": {},
            "completeness": {
                "ok": not missing,
                "missing": missing,
                "checked": 16,
            },
        },
        updated_at=updated_at,
    )
    db.add(doc)
    db.flush()
    return doc


def test_multi_package_completeness_uses_worst_ratio_and_union_of_missing():
    with session_scope() as db:
        project = Project(name="多包件评估")
        db.add(project)
        db.flush()
        first = BidPackage(project_id=project.id, name="包件一", position=1)
        second = BidPackage(project_id=project.id, name="包件二", position=2)
        db.add_all([first, second])
        db.flush()

        missing_buyer = [
            {"section": "bid_letter", "field": "buyer", "label": "招标人"}
        ]
        missing_amount = [
            {"section": "bid_letter", "field": "bid_amount", "label": "投标报价"}
        ]
        first_latest = _doc(
            db,
            project.id,
            package_id=first.id,
            missing=missing_buyer,
            updated_at=utcnow(),
            status="approved",
        )
        stale_bad = _doc(
            db,
            project.id,
            package_id=first.id,
            missing=missing_buyer + missing_amount,
            updated_at=utcnow() - timedelta(days=3),
        )
        worse = _doc(
            db,
            project.id,
            package_id=second.id,
            missing=missing_amount,
            updated_at=utcnow() - timedelta(days=1),
            status="submitted",
        )
        info = collect_commercial_completeness(db, project.id)

    assert info is not None
    assert info["ok"] is False
    assert info["docs"] == 2
    assert info["ratio"] == round(14 / 16, 4)
    assert info["checked"] == 16
    assert {item["field"] for item in info["missing"]} == {"buyer", "bid_amount"}
    assert set(info["doc_ids"]) == {first_latest.id, worse.id}
    assert stale_bad.id not in info["doc_ids"]
    assert set(info["package_ids"]) == {first.id, second.id}
    assert {entry["package_id"] for entry in info["by_package"]} == {first.id, second.id}
    assert info["source"] == "commercial_doc"
