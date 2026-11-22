"""企业资产匹配：过期不算有效、相似不自动满足、材料包汇总（Lane A）。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _add_user(db, username: str):
    from bidcraft.models import User
    from bidcraft.security import hash_password

    row = User(
        username=username,
        display_name=username,
        password_hash=hash_password("match-pass-1"),
        role="manager",
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def seeded(db):
    from bidcraft.db import session_scope
    from bidcraft.enterprise import assets
    from bidcraft.knowledge import ingest_document
    from bidcraft.models import Project, Requirement
    from bidcraft.personnel import create_enterprise_personnel
    from bidcraft.qualifications import create_qualification

    with session_scope() as session:
        owner = _add_user(session, "match-owner")
        member = _add_user(session, "match-member")
        enterprise = assets.create_enterprise(
            session, {"id": owner.id}, "匹配企业"
        )
        assets.add_member(session, {"id": owner.id}, enterprise.id, "match-member")
        valid = create_qualification(
            session,
            {"id": owner.id},
            {
                "kind": "license",
                "name": "电子与智能化工程专业承包二级",
                "cert_no": "DZ-2026-001",
                "valid_to": "2030-12-31",
            },
            enterprise_id=enterprise.id,
        )
        expired = create_qualification(
            session,
            {"id": owner.id},
            {
                "kind": "license",
                "name": "电子与智能化工程专业承包二级（旧证）",
                "cert_no": "DZ-2019-001",
                "valid_to": "2025-01-01",
            },
            enterprise_id=enterprise.id,
        )
        performance = create_qualification(
            session,
            {"id": owner.id},
            {
                "kind": "performance",
                "name": "某园区智能化工程业绩",
                "valid_to": "2030-12-31",
            },
            enterprise_id=enterprise.id,
        )
        locked = create_enterprise_personnel(
            session,
            {"id": owner.id},
            enterprise.id,
            {"name": "李四", "title": "项目经理", "locked": True},
        )
        available = create_enterprise_personnel(
            session,
            {"id": owner.id},
            enterprise.id,
            {"name": "王五", "title": "项目经理"},
        )
        kb = assets.enterprise_kb(session, enterprise, create=True)
        instrument = ingest_document(
            session,
            kb,
            "全站仪设备清单.md",
            "# 全站仪设备清单\n型号 TS-60，检定有效期至 2030-01-01。",
            kind="equipment",
            valid_to="2030-01-01",
        )
        project = Project(name="智能化项目", owner_id=owner.id)
        session.add(project)
        session.flush()
        session.add(
            Requirement(
                project_id=project.id,
                text="投标人须具备电子与智能化工程专业承包二级资质",
                mandatory=True,
            )
        )
        session.add(
            Requirement(
                project_id=project.id,
                text="近三年具有类似项目业绩",
                mandatory=True,
            )
        )
        return {
            "enterprise_id": enterprise.id,
            "owner": owner.id,
            "member": member.id,
            "project_id": project.id,
            "valid": valid.id,
            "expired": expired.id,
            "performance": performance.id,
            "locked": locked.id,
            "available": available.id,
            "instrument": instrument.id,
        }


def test_expired_qualification_never_auto_satisfies(db, seeded):
    from datetime import date

    from bidcraft.db import session_scope
    from bidcraft.enterprise import matching

    with session_scope() as session:
        result = matching.match_qualification(
            session,
            {"id": seeded["member"]},
            "须具备电子与智能化工程专业承包二级资质",
            enterprise_ids=[seeded["enterprise_id"]],
            today=date(2026, 1, 1),
        )
    by_id = {item["id"]: item for item in result["results"]}
    assert by_id[seeded["valid"]]["matched"] is True
    assert by_id[seeded["valid"]]["valid"] is True
    assert by_id[seeded["expired"]]["valid"] is False
    assert by_id[seeded["expired"]]["matched"] is False
    assert any("过期" in reason for reason in result["unmet"])


def test_similar_name_is_candidate_not_satisfied(db, seeded):
    from datetime import date

    from bidcraft.db import session_scope
    from bidcraft.enterprise import matching

    with session_scope() as session:
        result = matching.match_qualification(
            session,
            {"id": seeded["owner"]},
            "须具备电子与智能化工程专业承包一级资质",
            enterprise_ids=[seeded["enterprise_id"]],
            today=date(2026, 1, 1),
        )
    candidate = next(
        item for item in result["results"] if item["id"] == seeded["valid"]
    )
    assert candidate["match"] == "similar"
    assert candidate["matched"] is False
    assert result["satisfied"] is False


def test_personnel_locked_is_invalid(db, seeded):
    from bidcraft.db import session_scope
    from bidcraft.enterprise import matching

    with session_scope() as session:
        result = matching.match_personnel(
            session,
            {"id": seeded["member"]},
            "项目经理王五须持有注册建造师证书",
            enterprise_ids=[seeded["enterprise_id"]],
        )
    by_id = {item["id"]: item for item in result["results"]}
    assert by_id[seeded["available"]]["matched"] is True
    assert by_id[seeded["locked"]]["valid"] is False
    assert by_id[seeded["locked"]]["matched"] is False


def test_performance_and_equipment_document_matching(db, seeded):
    from bidcraft.db import session_scope
    from bidcraft.enterprise import matching

    with session_scope() as session:
        performance = matching.match_performance(
            session,
            {"id": seeded["member"]},
            "须提供某园区智能化工程业绩证明",
            enterprise_ids=[seeded["enterprise_id"]],
        )
        equipment = matching.match_equipment(
            session,
            {"id": seeded["member"]},
            "全站仪",
            enterprise_ids=[seeded["enterprise_id"]],
        )
    assert performance["matched"] is True
    assert performance["results"][0]["id"] == seeded["performance"]
    assert equipment["matched"] is True
    assert equipment["results"][0]["id"] == seeded["instrument"]


def test_project_asset_package_summarises_requirements(db, seeded):
    from bidcraft.db import session_scope
    from bidcraft.enterprise import matching
    from bidcraft.models import Project

    with session_scope() as session:
        project = session.get(Project, seeded["project_id"])
        package = matching.build_project_asset_package(
            session, {"id": seeded["member"]}, project
        )
    assert package["project_id"] == seeded["project_id"]
    assert package["summary"]["requirements"] == 2
    assert package["summary"]["satisfied"] >= 1
    assert {item["category"] for item in package["items"]} == {
        "qualification",
        "performance",
    }
    assert package["documents"]
    assert package["evidence"]
