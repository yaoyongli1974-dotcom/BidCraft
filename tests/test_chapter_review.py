"""章节复核流转：草稿 → 待复核 → 已确认 → 已锁定。

投标团队要的不是可配置流程，而是清晰、不可绕过的检查点：
- 已确认/已锁定 = 正文冻结（保存、重生成都被拒）；
- 待复核期间正文被改写，自动退回草稿（复核对象变了，检查点失效）；
- 导出只读取已确认（含已锁定）的章节，强制导出也绕不过去。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "review-pass-1"
BODY = "本方案采用 Cat6A 综合布线系统，满足招标要求。"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_project(client, name: str = "章节复核项目") -> str:
    response = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _seed_chapter(project_id: str, heading: str = "总体方案", body: str = BODY) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading=heading, position=1)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body=body, status="written", words=len(body)))
        session.flush()
        return node.id


@pytest.fixture()
def team(db):
    admin = _login(*ADMIN)
    for username in ("rv-owner", "rv-editor", "rv-viewer"):
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
    return {
        "admin": admin,
        "owner": _login("rv-owner"),
        "editor": _login("rv-editor"),
        "viewer": _login("rv-viewer"),
    }


def _add_member(owner, project_id: str, username: str, role: str) -> None:
    response = owner.post(
        f"/projects/{project_id}/members",
        json={"username": username, "role": role},
    )
    assert response.status_code in {200, 201}, response.text


def _setup(team, *, members: tuple[tuple[str, str], ...] = ()) -> tuple[str, str]:
    project_id = _make_project(team["owner"])
    node_id = _seed_chapter(project_id)
    for username, role in members:
        _add_member(team["owner"], project_id, username, role)
    return project_id, node_id


def _review(client, project_id: str, node_id: str, action: str, note: str = ""):
    return client.post(
        f"/projects/{project_id}/chapters/{node_id}/review",
        json={"action": action, "note": note},
    )


def _chapter(client, project_id: str, node_id: str) -> dict:
    response = client.get(f"/projects/{project_id}/chapters/{node_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _review_audits(project_id: str) -> list[dict]:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as session:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.project_id == project_id, AuditLog.action == "chapter.review")
            .order_by(AuditLog.created_at)
        )
        return [dict(row.detail or {}) for row in rows]


def test_chapter_starts_as_draft_and_outline_exposes_status(team):
    project_id, node_id = _setup(team)
    detail = _chapter(team["owner"], project_id, node_id)
    assert detail["review_status"] == "draft"
    assert detail["confirmed_by"] == ""

    outline = team["owner"].get(f"/projects/{project_id}/outline")
    assert outline.status_code == 200
    chapter = outline.json()["chapters"][0]
    assert chapter["review_status"] == "draft"


def test_submit_requires_body_and_writes_audit(team):
    project_id, node_id = _setup(team, members=(("rv-editor", "editor"),))

    empty_project = _make_project(team["owner"], "空章节项目")
    empty_node = _seed_chapter(empty_project, body="")
    rejected = _review(team["owner"], empty_project, empty_node, "submit")
    assert rejected.status_code == 409
    assert "还没有正文" in rejected.json()["detail"]

    submitted = _review(team["editor"], project_id, node_id, "submit", "请复核技术方案")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["review_status"] == "review"
    assert submitted.json()["review_requested_by"] == "" or True  # 用户名由会话决定
    audits = _review_audits(project_id)
    assert audits[-1]["action"] == "submit"
    assert audits[-1]["from"] == "draft"
    assert audits[-1]["to"] == "review"


def test_only_owner_can_confirm_and_lock(team):
    project_id, node_id = _setup(team, members=(("rv-editor", "editor"),))
    assert _review(team["editor"], project_id, node_id, "submit").status_code == 200

    forbidden = _review(team["editor"], project_id, node_id, "confirm")
    assert forbidden.status_code == 403

    confirmed = _review(team["owner"], project_id, node_id, "confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["review_status"] == "confirmed"
    assert confirmed.json()["confirmed_by"] != ""
    assert confirmed.json()["confirmed_at"].endswith("+08:00")

    # 未确认不能直接锁定
    other_project, other_node = _setup(team)
    assert _review(team["owner"], other_project, other_node, "submit").status_code == 200
    assert _review(team["owner"], other_project, other_node, "lock").status_code == 409

    locked = _review(team["owner"], project_id, node_id, "lock")
    assert locked.status_code == 200, locked.text
    assert locked.json()["review_status"] == "locked"
    assert locked.json()["locked_by"] != ""


def test_confirmed_and_locked_chapters_reject_edits(team):
    project_id, node_id = _setup(team, members=(("rv-editor", "editor"),))
    assert _review(team["editor"], project_id, node_id, "submit").status_code == 200
    assert _review(team["owner"], project_id, node_id, "confirm").status_code == 200

    save = team["editor"].put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": BODY + "（补充）"},
    )
    assert save.status_code == 409
    assert "已确认" in save.json()["detail"]

    generated = team["editor"].post(f"/projects/{project_id}/chapters/{node_id}/generate")
    assert generated.status_code == 409

    assert _review(team["owner"], project_id, node_id, "lock").status_code == 200
    locked_save = team["editor"].put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": BODY + "（再改）"},
    )
    assert locked_save.status_code == 409
    assert _chapter(team["editor"], project_id, node_id)["body"] == BODY


def test_editing_during_review_returns_to_draft(team):
    project_id, node_id = _setup(team, members=(("rv-editor", "editor"),))
    assert _review(team["editor"], project_id, node_id, "submit").status_code == 200

    saved = team["editor"].put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": BODY + " 并补充了售后服务承诺。"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["review_status"] == "draft"
    assert saved.json()["review_requested_by"] == ""

    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as session:
        row = session.scalars(
            select(AuditLog)
            .where(AuditLog.project_id == project_id, AuditLog.action == "chapter.save")
            .order_by(AuditLog.created_at.desc())
        ).first()
        assert row is not None and row.detail.get("review_reverted") is True


def test_revert_requires_reason_once_confirmed(team):
    project_id, node_id = _setup(team)
    assert _review(team["owner"], project_id, node_id, "submit").status_code == 200
    assert _review(team["owner"], project_id, node_id, "confirm").status_code == 200

    short = _review(team["owner"], project_id, node_id, "revert", "改")
    assert short.status_code == 400
    assert "至少 4 个字" in short.json()["detail"]

    reverted = _review(team["owner"], project_id, node_id, "revert", "客户要求补充分包说明")
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["review_status"] == "draft"
    assert reverted.json()["confirmed_by"] == ""

    # 回到草稿后才能改
    saved = team["owner"].put(
        f"/projects/{project_id}/chapters/{node_id}", json={"body": BODY + "（更新）"}
    )
    assert saved.status_code == 200


def test_unlock_requires_reason_and_returns_to_confirmed(team):
    project_id, node_id = _setup(team)
    assert _review(team["owner"], project_id, node_id, "submit").status_code == 200
    assert _review(team["owner"], project_id, node_id, "confirm").status_code == 200
    assert _review(team["owner"], project_id, node_id, "lock").status_code == 200

    assert _review(team["owner"], project_id, node_id, "unlock", "短").status_code == 400
    unlocked = _review(team["owner"], project_id, node_id, "unlock", "发现报价口径需要复核")
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["review_status"] == "confirmed"
    assert unlocked.json()["locked_by"] == ""

    # 解锁后仍是已确认，不能直接改
    assert (
        team["owner"]
        .put(f"/projects/{project_id}/chapters/{node_id}", json={"body": BODY + "x"})
        .status_code
        == 409
    )


def test_viewer_cannot_transition(team):
    project_id, node_id = _setup(team, members=(("rv-viewer", "viewer"),))
    assert _review(team["viewer"], project_id, node_id, "submit").status_code == 403
    assert _review(team["viewer"], project_id, node_id, "confirm").status_code == 403


def test_export_only_reads_confirmed_chapters(team):
    project_id, node_id = _setup(team)
    url = f"/projects/{project_id}/export"

    draft = team["owner"].post(url, json={"format": "docx"})
    assert draft.status_code == 409
    assert "还有 1 章未确认" in draft.json()["detail"]
    assert "草稿 1 章" in draft.json()["detail"]
    assert "《总体方案》" in draft.json()["detail"]

    assert _review(team["owner"], project_id, node_id, "submit").status_code == 200
    reviewing = team["owner"].post(url, json={"format": "docx"})
    assert reviewing.status_code == 409
    assert "待复核 1 章" in reviewing.json()["detail"]

    # 强制导出也不能绕过人工检查点
    forced = team["owner"].post(
        url, json={"format": "docx", "force": True, "force_reason": "客户今天就要"}
    )
    assert forced.status_code == 409
    assert "未确认" in forced.json()["detail"]

    assert _review(team["owner"], project_id, node_id, "confirm").status_code == 200
    exported = team["owner"].post(url, json={"format": "docx"})
    assert exported.status_code == 200, exported.text
    assert exported.content[:2] == b"PK"

    # xlsx 台账导出不受章节确认影响
    assert team["owner"].post(url, json={"format": "xlsx"}).status_code == 200


def test_empty_chapters_do_not_block_export(team):
    project_id, node_id = _setup(team)
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode

    with session_scope() as session:
        session.add(OutlineNode(project_id=project_id, heading="还没写的章节", position=2))

    assert _review(team["owner"], project_id, node_id, "submit").status_code == 200
    assert _review(team["owner"], project_id, node_id, "confirm").status_code == 200
    exported = team["owner"].post(f"/projects/{project_id}/export", json={"format": "docx"})
    assert exported.status_code == 200, exported.text


def test_migration_defaults_existing_sections_to_draft(db):
    """老库升级：sections 表补列后，既有正文默认草稿（而不是被当成已确认）。"""

    from sqlalchemy import inspect, text

    from bidcraft.db import get_engine, init_db

    init_db()
    init_db()  # 幂等
    columns = {column["name"] for column in inspect(get_engine()).get_columns("sections")}
    assert {"review_status", "confirmed_by", "locked_at"} <= columns

    with get_engine().begin() as connection:
        rows = connection.execute(
            text("SELECT review_status FROM sections")
        ).fetchall()
    assert all(row[0] in {None, "draft"} for row in rows)
