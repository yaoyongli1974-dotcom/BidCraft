"""T01：人工复核状态与导出权限关联（导出门禁验收）。

覆盖任务书要求：
- 重要条款（★）未人工确认 → 阻止正式导出；
- 强制导出必须由**独立权限**（owner/admin）控制，editor 无权强制；
- 强制导出必须填原因，原因与风险条款写入完整审计；
- 四条导出出口（HTTP / MCP / CLI / 批量）共用同一服务端门禁。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "export-review-pass-1"


def _login(username, password=PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def actors(db):
    admin = _login(*ADMIN)
    for username in ("ex-owner", "ex-editor"):
        r = admin.post(
            "/admin/users",
            json={
                "username": username,
                "display_name": username,
                "password": PASSWORD,
                "role": "manager",
            },
        )
        assert r.status_code == 201, r.text
    owner = _login("ex-owner")
    project = owner.post("/projects", json={"name": "导出门禁项目", "package": "weakcurrent-cabling"})
    assert project.status_code == 201, project.text
    pid = project.json()["id"]
    # editor 加入项目，仅可写正文，不能强制导出。
    r = owner.post(f"/projects/{pid}/members", json={"username": "ex-editor", "role": "editor"})
    assert r.status_code in {200, 201}, r.text
    return {
        "admin": admin,
        "owner": owner,
        "editor": _login("ex-editor"),
        "project_id": pid,
    }


def _seed_substantive(actors, text="★ 投标人须提供详细的项目实施方案说明", body="方案另行编制。"):
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Requirement, Section

    with session_scope() as db:
        row = Requirement(
            project_id=actors["project_id"],
            kind="substantive",
            text=text,
            mandatory=True,
            source_quote=text[:40],
            marker="★",
            response_status="pending",
        )
        db.add(row)
        node = OutlineNode(project_id=actors["project_id"], heading="技术方案", position=1)
        db.add(node)
        db.flush()
        db.add(Section(node_id=node.id, body=body, status="written", words=len(body),
                       review_status="confirmed"))
        db.flush()


def test_unconfirmed_star_blocks_export_and_editor_cannot_force(actors):
    _seed_substantive(actors)
    owner, editor, pid = actors["owner"], actors["editor"], actors["project_id"]
    assert owner.post(f"/projects/{pid}/check").status_code == 200

    # ★ review → blocking，普通导出被拦（editor 与 owner 都被拦）。
    assert editor.post(f"/projects/{pid}/export", json={"format": "docx"}).status_code == 409
    assert owner.post(f"/projects/{pid}/export", json={"format": "docx"}).status_code == 409

    # 强制导出是独立权限：editor 无权，即便填了原因也被 403 拒绝。
    denied = editor.post(
        f"/projects/{pid}/export",
        json={"format": "docx", "force": True, "force_reason": "业主已确认风险可控"},
    )
    assert denied.status_code == 403, denied.text

    # 强制导出必须填原因（owner 缺原因 → 400）。
    no_reason = owner.post(
        f"/projects/{pid}/export", json={"format": "docx", "force": True, "force_reason": ""}
    )
    assert no_reason.status_code == 400, no_reason.text


def test_force_export_records_full_audit_and_risk_clause(actors):
    _seed_substantive(actors)
    owner, pid = actors["owner"], actors["project_id"]
    assert owner.post(f"/projects/{pid}/check").status_code == 200

    forced = owner.post(
        f"/projects/{pid}/export",
        json={"format": "docx", "force": True, "force_reason": "业主书面确认风险可控"},
    )
    assert forced.status_code == 200, forced.text

    audit = owner.get(f"/projects/{pid}/audit?limit=50").json()["items"]
    entry = next(item for item in audit if item["action"] == "export.force")
    detail = entry["detail"]
    assert detail["reason"] == "业主书面确认风险可控"
    assert detail["blocking_open"] >= 1
    assert entry["username"] == "ex-owner"
    assert len(detail.get("overridden_substantives", [])) == 1
    assert detail["overridden_substantives"][0]["marker"] == "★"


def test_confirmation_removes_substantive_blocker(actors):
    """人工确认（responded）并记录意见后，该 ★ 条款不再产出 SUB 阻断问题。

    注意：最小项目还带合规类基线阻断（COM-*），所以这里只断言「实质性条款这一
    道关卡」被解除——SUB-* 消失，而不是整库放行（那需要真实招标件基线）。
    """

    _seed_substantive(actors)
    owner, pid = actors["owner"], actors["project_id"]
    assert owner.post(f"/projects/{pid}/check").status_code == 200
    before = owner.get(f"/projects/{pid}/risk-overview").json()["items"]
    assert any(item["code"].startswith("SUB-") and item["level"] == "blocking" for item in before)

    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import Requirement

    with session_scope() as db:
        req = db.scalar(select(Requirement).where(Requirement.project_id == pid))
        req_id = req.id

    confirmed = owner.patch(
        f"/projects/{pid}/requirements/{req_id}",
        json={"response_status": "responded", "note": "第5章已完整响应并附方案"},
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["responded_by"] == "ex-owner"
    assert payload["response_note"] == "第5章已完整响应并附方案"
    assert payload["responded_at"].endswith("+08:00")
    assert payload["response_verdict"]  # 原始判定被快照

    assert owner.post(f"/projects/{pid}/check").status_code == 200
    after = owner.get(f"/projects/{pid}/risk-overview").json()["items"]
    assert not any(item["code"].startswith("SUB-") for item in after)


def _first_star_req_id(pid: str) -> str:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import Requirement

    with session_scope() as db:
        req = db.scalar(
            select(Requirement).where(
                Requirement.project_id == pid, Requirement.marker == "★"
            )
        )
        assert req is not None
        return req.id


def test_editor_cannot_final_confirm_star_but_owner_can(actors):
    """R07：编制≠确认。editor 不能最终确认 ★ 条款，项目负责人可以。"""

    _seed_substantive(actors)
    owner, editor, pid = actors["owner"], actors["editor"], actors["project_id"]
    req_id = _first_star_req_id(pid)

    denied = editor.patch(
        f"/projects/{pid}/requirements/{req_id}",
        json={"response_status": "responded", "note": "我已自行复核确认"},
    )
    assert denied.status_code == 403, denied.text

    allowed = owner.patch(
        f"/projects/{pid}/requirements/{req_id}",
        json={"response_status": "responded", "note": "负责人复核后确认"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["responded_by"] == "ex-owner"


def test_failed_force_export_leaves_no_success_audit(actors):
    """R07 事务边界：章节未确认导致强制导出失败时，不得留下成功的 export.force 审计。"""

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Requirement, Section

    pid = actors["project_id"]
    # ★ 条款（制造 blocking）+ 正文草稿（章节门禁未通过）。
    with session_scope() as db:
        db.add(
            Requirement(
                project_id=pid,
                kind="substantive",
                text="★ 投标人须提供详细的项目实施方案说明",
                mandatory=True,
                source_quote="投标人须提供详细的项目实施方案说明",
                marker="★",
                response_status="pending",
            )
        )
        node = OutlineNode(project_id=pid, heading="技术方案", position=1)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body="方案另行编制。",
                status="written",
                words=6,
                review_status="draft",  # 未确认 → 章节门禁拦截
            )
        )
        db.flush()

    owner = actors["owner"]
    assert owner.post(f"/projects/{pid}/check").status_code == 200
    # 先确认没有基线 export.force 审计
    before = owner.get(f"/projects/{pid}/audit?limit=100").json()["items"]
    assert not any(item["action"] == "export.force" for item in before)

    # force 满足权限+原因，但因章节未确认失败（409）。
    forced = owner.post(
        f"/projects/{pid}/export",
        json={"format": "docx", "force": True, "force_reason": "业主已确认风险可控"},
    )
    assert forced.status_code == 409, forced.text
    assert "未确认" in forced.json()["detail"]

    # 关键：失败后仍不得出现 export.force 成功审计。
    after = owner.get(f"/projects/{pid}/audit?limit=100").json()["items"]
    assert not any(item["action"] == "export.force" for item in after)


def test_chapter_edit_invalidates_confirmed_requirement(actors):
    """R15：正文修改后，基于旧正文确认的条款必须失效（退回 pending）。

    确认快照仍在审计/字段中可追溯，但「已确认」不能掩盖改动后的正文——
    否则导出会拿旧确认放行新正文。
    """
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, OutlineNode, Requirement, Section

    pid = actors["project_id"]
    with session_scope() as db:
        node = OutlineNode(project_id=pid, heading="技术方案", position=1)
        db.add(node)
        db.flush()
        db.add(Section(node_id=node.id, body="旧正文内容。", status="written", words=6,
                       review_status="draft"))
        db.add(
            Requirement(
                project_id=pid,
                kind="substantive",
                text="★ 投标人须提供详细的项目实施方案说明",
                mandatory=True,
                source_quote="投标人须提供详细的项目实施方案说明",
                marker="★",
                response_status="responded",  # 已人工确认
                response_verdict="satisfied",
                response_verdict_label="满足",
                response_verdict_reason="旧正文已覆盖",
                node_id=node.id,
                auto_covered=True,
            )
        )
        db.flush()
        node_id = node.id

    owner = actors["owner"]
    saved = owner.put(
        f"/projects/{pid}/chapters/{node_id}",
        json={"body": "这是改写后的新正文内容，与旧确认无关。"},
    )
    assert saved.status_code == 200, saved.text

    with session_scope() as db:
        row = db.query(Requirement).filter_by(project_id=pid).one()
        assert row.response_status == "pending", "确认应失效"
        assert row.node_id is None and row.section_id is None and row.auto_covered is False
        # 确认快照字段已被清空（失效后不再代表当前正文）
        assert row.response_verdict == ""

    # 失效动作写入审计
    with session_scope() as db:
        audits = [a for a in db.query(AuditLog).filter_by(project_id=pid).all()
                  if a.action == "chapter.save" and (a.detail or {}).get("requirement_confirmations_invalidated")]
        assert audits and audits[-1].detail["requirement_confirmations_invalidated"]
