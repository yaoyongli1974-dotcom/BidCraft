"""章节修订：保存快照、哈希、列表/详情、diff、restore（不覆盖历史）。"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "rev-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_user(admin, username: str) -> None:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert response.status_code == 201, response.text


def _seed_chapter(project_id: str, heading: str = "总体方案") -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading=heading, position=1)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="", status="pending", words=0, revision=0))
        session.flush()
        return node.id


def _save(project_id: str, node_id: str, body: str, actor: str, note: str = "") -> int:
    """模拟 save_chapter：先 bump revision，再落快照（与编排层接线一致）。"""

    from sqlalchemy import select

    from bidcraft.collaboration import snapshot_on_save
    from bidcraft.db import session_scope
    from bidcraft.generation.writer import count_words
    from bidcraft.models import Section

    with session_scope() as session:
        section = session.scalar(select(Section).where(Section.node_id == node_id))
        assert section is not None
        section.body = body
        section.words = count_words(body)
        section.status = "edited"
        section.revision = (section.revision or 0) + 1
        session.flush()
        row = snapshot_on_save(session, section, project_id, actor, note)
        assert row is not None
        return row.revision


@pytest.fixture()
def team(db):
    admin = _login(*ADMIN)
    for username in ("rv-owner", "rv-editor", "rv-viewer", "rv-outsider"):
        _make_user(admin, username)
    owner = _login("rv-owner")
    project = owner.post(
        "/projects", json={"name": "修订项目", "package": "weakcurrent-cabling"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    for username, role in (("rv-editor", "editor"), ("rv-viewer", "viewer")):
        assert owner.post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": role},
        ).status_code == 201
    return {
        "admin": admin,
        "owner": owner,
        "editor": _login("rv-editor"),
        "viewer": _login("rv-viewer"),
        "outsider": _login("rv-outsider"),
        "project_id": project_id,
        "node_id": _seed_chapter(project_id),
    }


def _rev_url(team, suffix: str = "") -> str:
    return f"/projects/{team['project_id']}/chapters/{team['node_id']}/revisions{suffix}"


def test_each_save_snapshots_revision_and_body_hash(team):
    body1 = "第一版正文：Cat6A 综合布线。"
    body2 = "第二版正文：Cat6A 综合布线，新增机柜。"
    assert _save(team["project_id"], team["node_id"], body1, "rv-owner", "初稿") == 1
    assert _save(team["project_id"], team["node_id"], body2, "rv-editor", "补充") == 2

    response = team["viewer"].get(_rev_url(team))
    assert response.status_code == 200, response.text
    rows = response.json()["revisions"]
    assert [row["revision"] for row in rows] == [2, 1]
    assert rows[0]["body"] == body2
    assert rows[0]["change_type"] == "edit"
    assert rows[0]["changed_by"] == "rv-editor"
    assert rows[0]["change_note"] == "补充"
    assert rows[0]["body_hash"] == hashlib.sha256(body2.encode("utf-8")).hexdigest()
    assert rows[1]["body_hash"] == hashlib.sha256(body1.encode("utf-8")).hexdigest()

    from sqlalchemy import func, select

    from bidcraft.db import session_scope
    from bidcraft.models import Section, SectionRevision

    with session_scope() as session:
        section = session.scalar(
            select(Section).where(Section.node_id == team["node_id"])
        )
        assert section is not None and section.revision == 2
        count = session.scalar(
            select(func.count())
            .select_from(SectionRevision)
            .where(SectionRevision.node_id == team["node_id"])
        )
        assert count == 2

    # 同 revision 重放幂等：不再生成第二条
    from bidcraft.collaboration import snapshot_on_save

    with session_scope() as session:
        section = session.scalar(
            select(Section).where(Section.node_id == team["node_id"])
        )
        assert snapshot_on_save(session, section, team["project_id"], "rv-owner", "") is None
        count = session.scalar(
            select(func.count())
            .select_from(SectionRevision)
            .where(SectionRevision.node_id == team["node_id"])
        )
        assert count == 2


def test_revision_detail_and_line_diff(team):
    _save(team["project_id"], team["node_id"], "第一行\n第二行", "rv-owner")
    _save(team["project_id"], team["node_id"], "第一行\n第二行改\n第三行", "rv-owner")

    detail = team["viewer"].get(_rev_url(team, "/1"))
    assert detail.status_code == 200
    assert detail.json()["body"] == "第一行\n第二行"
    assert detail.json()["revision"] == 1
    assert team["viewer"].get(_rev_url(team, "/99")).status_code == 404

    diff = team["viewer"].get(_rev_url(team, "/diff") + "?from=1&to=2")
    assert diff.status_code == 200, diff.text
    payload = diff.json()
    assert payload["from_revision"] == 1
    assert payload["to_revision"] == 2
    assert payload["changed"] is True
    assert payload["added_lines"] == 2
    assert payload["removed_lines"] == 1
    types = {(line["type"], line["text"]) for line in payload["lines"]}
    assert ("removed", "第二行") in types
    assert ("added", "第二行改") in types
    assert ("added", "第三行") in types
    assert ("equal", "第一行") in types

    assert (
        team["viewer"].get(_rev_url(team, "/diff") + "?from=1&to=99").status_code == 404
    )

    # 纯函数：字符串 / 行对象都可比较
    from bidcraft.collaboration import diff_section_revision

    unit = diff_section_revision("a\nb\n", "a\nc\n")
    assert unit["changed"] is True
    assert unit["added_lines"] == 1
    assert unit["removed_lines"] == 1
    assert diff_section_revision("same", "same")["changed"] is False


def test_restore_creates_new_revision_without_overwriting_history(team):
    old_body = "历史正文：原方案。"
    new_body = "当前正文：修改后的方案。"
    assert _save(team["project_id"], team["node_id"], old_body, "rv-owner") == 1
    assert _save(team["project_id"], team["node_id"], new_body, "rv-owner") == 2

    restored = team["editor"].post(
        _rev_url(team, "/restore"),
        json={"revision": 1, "note": "回滚到原方案"},
    )
    assert restored.status_code == 200, restored.text
    payload = restored.json()
    assert payload["revision"] == 3
    assert payload["restored_from"] == 1
    assert payload["change_type"] == "restore"
    assert payload["body"] == old_body
    assert payload["change_note"] == "回滚到原方案"
    assert payload["body_hash"] == hashlib.sha256(old_body.encode("utf-8")).hexdigest()

    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import Section, SectionRevision

    with session_scope() as session:
        section = session.scalar(
            select(Section).where(Section.node_id == team["node_id"])
        )
        assert section is not None
        assert section.revision == 3
        assert section.body == old_body
        rows = list(
            session.scalars(
                select(SectionRevision)
                .where(SectionRevision.node_id == team["node_id"])
                .order_by(SectionRevision.revision)
            )
        )
        assert [row.revision for row in rows] == [1, 2, 3]
        assert rows[1].body == new_body  # 历史版本 2 未被覆盖
        assert rows[2].change_type == "restore"
        assert rows[2].body == old_body

    # 权限：viewer 只读、外部人不可见、缺失版本 404
    assert team["viewer"].post(
        _rev_url(team, "/restore"), json={"revision": 1}
    ).status_code == 403
    assert team["outsider"].post(
        _rev_url(team, "/restore"), json={"revision": 1}
    ).status_code == 404
    assert team["editor"].post(
        _rev_url(team, "/restore"), json={"revision": 99}
    ).status_code == 404


def test_restore_is_blocked_for_confirmed_chapter(team):
    _save(team["project_id"], team["node_id"], "确认版正文。", "rv-owner")
    base = f"/projects/{team['project_id']}/chapters/{team['node_id']}"
    assert team["editor"].post(
        f"{base}/review", json={"action": "submit", "note": "请复核"}
    ).status_code == 200
    assert team["owner"].post(
        f"{base}/review", json={"action": "confirm", "note": "确认通过"}
    ).status_code == 200

    blocked = team["editor"].post(
        _rev_url(team, "/restore"), json={"revision": 1, "note": "尝试回滚"}
    )
    assert blocked.status_code == 409
    assert "撤回" in blocked.json()["detail"]

    # 按现有审核机制撤回后才能回滚
    assert team["owner"].post(
        f"{base}/review", json={"action": "revert", "note": "需要回滚重写"}
    ).status_code == 200
    allowed = team["editor"].post(
        _rev_url(team, "/restore"), json={"revision": 1, "note": "已撤回"}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["change_type"] == "restore"


def test_restore_refreshes_fact_citations(team):
    from sqlalchemy import select

    from bidcraft import provenance
    from bidcraft.db import session_scope
    from bidcraft.models import Fact, FactCitation, Section

    with session_scope() as session:
        session.add(
            Fact(
                project_id=team["project_id"],
                key="cabling.brand",
                label="布线品牌",
                value="Cat6A综合布线",
                status="confirmed",
            )
        )
        session.flush()

    body_with_fact = "本项目采用Cat6A综合布线系统。"
    body_without_fact = "本项目采用普通线缆系统。"
    _save(team["project_id"], team["node_id"], body_with_fact, "rv-owner")
    _save(team["project_id"], team["node_id"], body_without_fact, "rv-owner")

    with session_scope() as session:
        section = session.scalar(
            select(Section).where(Section.node_id == team["node_id"])
        )
        provenance.refresh_node(session, team["project_id"], team["node_id"], section.body)
        assert session.scalars(
            select(FactCitation).where(FactCitation.node_id == team["node_id"])
        ).all() == []

    # 回滚到含事实取值的版本 → 触发 refresh_node，引用重建
    restored = team["editor"].post(
        _rev_url(team, "/restore"), json={"revision": 1, "note": "恢复事实引用"}
    )
    assert restored.status_code == 200, restored.text
    with session_scope() as session:
        citations = session.scalars(
            select(FactCitation).where(FactCitation.node_id == team["node_id"])
        ).all()
        assert len(citations) == 1
        assert citations[0].fact_key == "cabling.brand"
        assert citations[0].value == "Cat6A综合布线"

    # 再回滚到不含取值的版本 → 引用被清除
    assert team["editor"].post(
        _rev_url(team, "/restore"), json={"revision": 2, "note": "再次回滚"}
    ).status_code == 200
    with session_scope() as session:
        assert session.scalars(
            select(FactCitation).where(FactCitation.node_id == team["node_id"])
        ).all() == []
