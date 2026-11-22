"""企业共享资产隔离、注销与导出（Lane A）。"""

from __future__ import annotations

import json
import zipfile

import pytest

from bidcraft.config import settings

pytest.importorskip("fastapi")


def _add_user(db, username: str):
    from bidcraft.models import User
    from bidcraft.security import hash_password

    row = User(
        username=username,
        display_name=username,
        password_hash=hash_password("perm-pass-1"),
        role="manager",
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def ids(db):
    from bidcraft.db import session_scope

    with session_scope() as session:
        owner = _add_user(session, "perm-owner")
        member = _add_user(session, "perm-member")
        outsider = _add_user(session, "perm-outsider")
        return {"owner": owner.id, "member": member.id, "outsider": outsider.id}


def _seed_shared(db, ids):
    from bidcraft.enterprise import assets
    from bidcraft.personnel import create_enterprise_personnel
    from bidcraft.qualifications import create_qualification

    enterprise = assets.create_enterprise(db, {"id": ids["owner"]}, "共享资产企业")
    assets.add_member(db, {"id": ids["owner"]}, enterprise.id, "perm-member")
    shared = create_qualification(
        db,
        {"id": ids["owner"]},
        {
            "kind": "license",
            "name": "电子与智能化工程专业承包二级",
            "cert_no": "DZ-2026-001",
            "valid_to": "2030-12-31",
        },
        enterprise_id=enterprise.id,
    )
    private = create_qualification(
        db,
        {"id": ids["owner"]},
        {"kind": "license", "name": "owner 私有资质", "valid_to": "2030-12-31"},
    )
    person = create_enterprise_personnel(
        db,
        {"id": ids["owner"]},
        enterprise.id,
        {"name": "张三", "title": "项目经理", "cert_no": "建造师-001"},
    )
    return enterprise, shared, private, person


def test_shared_qualification_and_personnel_visible_only_to_members(db, ids):
    from bidcraft import personnel, qualifications
    from bidcraft.db import session_scope

    with session_scope() as session:
        enterprise, shared, private, person = _seed_shared(session, ids)
        enterprise_id, shared_id, private_id, person_id = (
            enterprise.id,
            shared.id,
            private.id,
            person.id,
        )

    with session_scope() as session:
        member = {"id": ids["member"]}
        rows = qualifications.list_enterprise_qualifications(session, member)
        assert [row.id for row in rows] == [shared_id]
        assert qualifications.get_visible_qualification(session, member, shared_id).id == shared_id
        with pytest.raises(LookupError):
            qualifications.get_visible_qualification(session, member, private_id)

        people = personnel.list_enterprise_personnel(session, member)
        assert [row.id for row in people] == [person_id]
        assert personnel.find_visible_personnel(session, member, person_id).name == "张三"

        outsider = {"id": ids["outsider"]}
        assert qualifications.list_enterprise_qualifications(session, outsider) == []
        assert personnel.list_enterprise_personnel(session, outsider) == []
        with pytest.raises(LookupError):
            qualifications.list_enterprise_qualifications(
                session, outsider, enterprise_id
            )
        with pytest.raises(LookupError):
            personnel.list_enterprise_personnel(session, outsider, enterprise_id)


def test_member_cannot_write_shared_assets(db, ids):
    from bidcraft import personnel, qualifications
    from bidcraft.db import session_scope
    from bidcraft.qualifications import create_enterprise_qualification

    with session_scope() as session:
        enterprise, shared, _private, person = _seed_shared(session, ids)
        enterprise_id, shared_id, person_id = enterprise.id, shared.id, person.id

    with session_scope() as session:
        with pytest.raises(PermissionError):
            create_enterprise_qualification(
                session,
                {"id": ids["member"]},
                enterprise_id,
                {"kind": "license", "name": "member 想加的资质"},
            )
        with pytest.raises(PermissionError):
            personnel.create_enterprise_personnel(
                session,
                {"id": ids["member"]},
                enterprise_id,
                {"name": "李四"},
            )
        with pytest.raises(PermissionError):
            qualifications.update_enterprise_qualification(
                session, {"id": ids["member"]}, shared_id, {"name": "改名"}
            )
        with pytest.raises(PermissionError):
            personnel.update_enterprise_personnel(
                session, {"id": ids["member"]}, person_id, {"title": "改职务"}
            )


def test_member_account_delete_keeps_enterprise_and_shared_assets(db, ids):
    from bidcraft import compliance
    from bidcraft.db import session_scope
    from bidcraft.models import Enterprise, EnterpriseMember, Personnel, Qualification, User

    with session_scope() as session:
        enterprise, shared, _private, person = _seed_shared(session, ids)
        enterprise_id, shared_id, person_id = enterprise.id, shared.id, person.id

    with session_scope() as session:
        member = session.get(User, ids["member"])
        removed = compliance.delete_account(session, member)
        assert removed["enterprise_members"] == 1
        assert session.get(Enterprise, enterprise_id) is not None
        assert session.get(Qualification, shared_id) is not None
        assert session.get(Personnel, person_id) is not None
        rows = session.query(EnterpriseMember).filter_by(enterprise_id=enterprise_id).all()
        assert [row.user_id for row in rows] == [ids["owner"]]
        assert session.get(User, ids["member"]) is None


def test_owner_account_delete_transfers_enterprise_and_shared_assets(db, ids):
    from bidcraft import compliance
    from bidcraft.db import session_scope
    from bidcraft.models import Enterprise, Qualification, User

    with session_scope() as session:
        enterprise, shared, _private, _person = _seed_shared(session, ids)
        enterprise_id, shared_id = enterprise.id, shared.id

    with session_scope() as session:
        owner = session.get(User, ids["owner"])
        compliance.delete_account(session, owner)
        enterprise = session.get(Enterprise, enterprise_id)
        assert enterprise is not None
        assert enterprise.owner_id == ids["member"]
        qualification = session.get(Qualification, shared_id)
        assert qualification is not None
        assert qualification.owner_id == ids["member"]


def test_export_includes_enterprise_membership_and_shared_assets(db, ids):
    from bidcraft import compliance
    from bidcraft.db import session_scope
    from bidcraft.models import User

    with session_scope() as session:
        enterprise, shared, _private, person = _seed_shared(session, ids)
        enterprise_id, shared_id, person_id = enterprise.id, shared.id, person.id

    with session_scope() as session:
        member = session.get(User, ids["member"])
        path = compliance.export_account(session, member, settings.home)

    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["enterprises"] == [
        {"id": enterprise_id, "name": "共享资产企业", "role": "member"}
    ]
    assert [item["id"] for item in manifest["shared_qualifications"]] == [shared_id]
    assert [item["id"] for item in manifest["shared_personnel"]] == [person_id]
