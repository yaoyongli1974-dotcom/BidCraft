"""报价审批并行会签（R19）：每级多审批人，会签 all / 或签 any。

Covers the contract's A3 matrix: countersign (all) needs every approver before
the level advances, or-sign (any) advances on the first approval, a duplicate
countersign approval is refused (403), wrong-level / outsider refusal, the
super-admin override, final-level countersign completion (+ retiring the old
winner), mid-chain reject termination, resubmit clearing progress, PATCH v2
validation (stage/level limits, mode whitelist, unknown users, dedup) and the
legacy flat input -> v2 echo. Also unit-tests normalize_chain/completed_stages.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ADMIN_NAME = "超级管理员"
MEMBER_PASS = "member-pass-1"


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert response.status_code == 200, response.text
    return client


def _login(username: str):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": username, "password": MEMBER_PASS}
    )
    assert response.status_code == 200, response.text
    return client


def _member(admin, project_id: str | None, username: str, display_name: str, role: str):
    """Create a global user (optionally a project member) and return (client, id)."""
    created = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": display_name,
            "password": MEMBER_PASS,
            "role": "manager",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    if project_id is not None:
        added = admin.post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": role},
        )
        assert added.status_code == 201, added.text
    return _login(username), user_id


def _project(client, name: str = "会签项目") -> str:
    created = client.post("/projects", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _package(client, project_id: str, name: str = "A 标段") -> str:
    created = client.post(f"/projects/{project_id}/packages", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _write_boq(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def _upload(client, project_id: str, path, package_id: str | None = None):
    data = {"package_id": package_id} if package_id is not None else None
    with path.open("rb") as handle:
        return client.post(
            f"/projects/{project_id}/boq",
            files={"file": (path.name, handle, XLSX_MIME)},
            data=data,
        )


def _seed(client, tmp_path, *, package: bool = True):
    project_id = _project(client)
    package_id = _package(client, project_id) if package else None
    path = _write_boq(
        tmp_path / "parallel.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "2", ""],
            [2, "线缆", "Cat6A", "米", "10", ""],
        ],
    )
    assert _upload(client, project_id, path, package_id=package_id).status_code == 200
    priced = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 2, "unit_price": 12.5, "package_id": package_id},
    )
    assert priced.status_code == 200, priced.text
    return project_id, package_id


def _save(client, project_id: str, name: str, package_id: str | None = None):
    body = {"name": name}
    if package_id is not None:
        body["package_id"] = package_id
    response = client.post(f"/projects/{project_id}/boq/versions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client, project_id: str, version_id: str, note: str = ""):
    return client.post(
        f"/projects/{project_id}/boq/versions/{version_id}/submit",
        json={"note": note},
    )


def _review(client, project_id: str, version_id: str, decision: str, note: str = ""):
    return client.post(
        f"/projects/{project_id}/boq/versions/{version_id}/review",
        json={"decision": decision, "note": note},
    )


def _detail(client, project_id: str, version_id: str):
    response = client.get(f"/projects/{project_id}/boq/versions/{version_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _patch_chain(client, project_id: str, chain):
    return client.patch(f"/projects/{project_id}", json={"approval_chain": chain})


def _chain_payload(client, project_id: str):
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 200, response.text
    return response.json()["approval_chain"]


def _audit(project_id: str):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as db:
        rows = list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.project_id == project_id)
                .order_by(AuditLog.id)
            )
        )
        return [(row.action, dict(row.detail or {})) for row in rows]


def _stage(ids, mode="all"):
    return {"approvers": list(ids), "mode": mode}


# ------------------------------------------------------------------ 纯函数


def test_normalize_chain_flat_and_v2():
    from bidcraft.boq import normalize_chain

    assert normalize_chain(None) == []
    assert normalize_chain([]) == []

    # R18 flat -> single-approver all stages
    flat = [{"id": "u1", "name": "甲"}, {"id": "u2", "name": "乙"}]
    assert normalize_chain(flat) == [
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "all"},
        {"approvers": [{"id": "u2", "name": "乙"}], "mode": "all"},
    ]

    # v2 kept as-is; missing mode defaults to all
    v2 = [{"approvers": [{"id": "u1", "name": "甲"}, {"id": "u2", "name": "乙"}], "mode": "any"}]
    assert normalize_chain(v2) == v2
    assert normalize_chain([{"approvers": [{"id": "u1", "name": "甲"}]}]) == [
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "all"}
    ]

    # mixed flat + v2
    mixed = [{"id": "u1", "name": "甲"}, {"approvers": [{"id": "u2", "name": "乙"}], "mode": "any"}]
    assert normalize_chain(mixed) == [
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "all"},
        {"approvers": [{"id": "u2", "name": "乙"}], "mode": "any"},
    ]

    # malformed entries ignored; invalid mode falls back to all
    messy = [
        None,
        "x",
        42,
        {},
        {"id": ""},
        {"approvers": []},
        {"approvers": [{"id": "", "name": "空"}]},
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "bogus"},
    ]
    assert normalize_chain(messy) == [
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "all"}
    ]


def test_completed_stages_all_any():
    from bidcraft.boq import completed_stages

    chain = [
        {"approvers": [{"id": "a"}, {"id": "b"}], "mode": "all"},
        {"approvers": [{"id": "c"}, {"id": "d"}], "mode": "any"},
    ]

    def rec(stage, uid, decision="approve"):
        return {"stage": stage, "user_id": uid, "decision": decision}

    assert completed_stages(chain, []) == 0
    assert completed_stages(chain, [rec(0, "a")]) == 0  # all needs both
    assert completed_stages(chain, [rec(0, "a"), rec(0, "b")]) == 1
    assert completed_stages(chain, [rec(0, "a"), rec(0, "b"), rec(1, "c")]) == 2  # any: one
    assert completed_stages(chain, [rec(0, "a"), rec(0, "b"), rec(1, "c"), rec(1, "d")]) == 2
    # reject records do not count toward completion
    assert completed_stages(chain, [rec(0, "a"), rec(0, "b", "reject")]) == 0
    # the scan stops at the first incomplete stage
    assert completed_stages(chain, [rec(1, "c")]) == 0


# ------------------------------------------------------------------ 会签 all


def test_countersign_level_needs_all_approvers(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "par-carol", "审批丙", "editor")
    patched = _patch_chain(
        admin, project_id, [_stage([alice_id, bob_id]), _stage([carol_id])]
    )
    assert patched.status_code == 200, patched.text

    version = _save(alice, project_id, "会签报价", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _detail(admin, project_id, version["id"])["approval_stage"] == 0

    # first countersign approval: level not complete -> still submitted, stage 0
    first = _review(alice, project_id, version["id"], "approve", "甲同意")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 0
    assert len(body["approvals"]) == 1
    assert body["approvals"][0]["stage"] == 0
    assert body["review_note"] == ""

    # second countersign approval completes level 1 -> advances to level 2
    second = _review(bob, project_id, version["id"], "approve", "乙同意")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 1
    assert len(body["approvals"]) == 2
    assert [r["stage"] for r in body["approvals"]] == [0, 0]

    # level 2 (single approver) finalizes
    final = _review(carol, project_id, version["id"], "approve", "丙定稿")
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["status"] == "approved"
    assert body["approval_stage"] == 0
    assert len(body["approvals"]) == 3
    assert body["approvals"][2]["stage"] == 1
    assert body["reviewed_by"] == "审批丙"
    assert datetime.fromisoformat(body["reviewed_at"])


def test_countersign_duplicate_approval_forbidden(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")
    assert _patch_chain(admin, project_id, [_stage([alice_id, bob_id])]).status_code == 200

    version = _save(alice, project_id, "重复批", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").status_code == 200

    # alice already signed this all-level: approving again is refused
    again = _review(alice, project_id, version["id"], "approve")
    assert again.status_code == 403
    assert "您已完成本级审批" in again.json()["detail"]
    assert "第 1 级" in again.json()["detail"]
    # ... and so is rejecting again (authorization is decision-agnostic)
    again_reject = _review(alice, project_id, version["id"], "reject")
    assert again_reject.status_code == 403
    assert "您已完成本级审批" in again_reject.json()["detail"]

    # state untouched by the refused attempts
    body = _detail(admin, project_id, version["id"])
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 0
    assert len(body["approvals"]) == 1

    # the other countersign approver completes the level -> approved
    final = _review(bob, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert len(final.json()["approvals"]) == 2


def test_super_admin_countersign_override(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    _bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")
    assert _patch_chain(admin, project_id, [_stage([alice_id, bob_id])]).status_code == 200
    admin_id = admin.get("/auth/me").json()["user"]["id"]

    # super admin may sign a countersign level repeatedly (no duplicate 403);
    # two records satisfy the two-approver all-stage.
    version = _save(alice, project_id, "超管代批", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    first = _review(admin, project_id, version["id"], "approve", "超管代甲")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "submitted"
    assert first.json()["approval_stage"] == 0
    second = _review(admin, project_id, version["id"], "approve", "超管代乙")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["status"] == "approved"
    assert len(body["approvals"]) == 2
    assert all(r["user_id"] == admin_id for r in body["approvals"])
    assert body["reviewed_by"] == ADMIN_NAME

    # mixed: a real approver signs, then the super admin signs for the other
    mixed = _save(alice, project_id, "混合代批", package_id)
    assert _submit(alice, project_id, mixed["id"]).status_code == 200
    assert _review(alice, project_id, mixed["id"], "approve").status_code == 200
    final = _review(admin, project_id, mixed["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["user_id"] for r in final.json()["approvals"]] == [alice_id, admin_id]


def test_final_level_countersign_completes_and_retires_old(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "par-carol", "审批丙", "editor")
    assert _patch_chain(
        admin, project_id, [_stage([alice_id]), _stage([bob_id, carol_id])]
    ).status_code == 200

    # an earlier winner walks the whole chain to approved
    old = _save(alice, project_id, "旧定稿", package_id)
    assert _submit(alice, project_id, old["id"]).status_code == 200
    assert _review(alice, project_id, old["id"], "approve").status_code == 200
    assert _review(bob, project_id, old["id"], "approve").status_code == 200
    assert _review(carol, project_id, old["id"], "approve").status_code == 200
    assert _detail(admin, project_id, old["id"])["status"] == "approved"

    # new version: final level is a countersign -> only approved once both sign
    version = _save(alice, project_id, "新定稿", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").json()["approval_stage"] == 1
    partial = _review(bob, project_id, version["id"], "approve")
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "submitted"
    assert partial.json()["approval_stage"] == 1  # final level not complete yet
    assert _detail(admin, project_id, old["id"])["status"] == "approved"  # old still winner

    final = _review(carol, project_id, version["id"], "approve", "会签定稿")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert len(final.json()["approvals"]) == 3
    # the previous winner is retired to draft, keeping its trail
    retired = _detail(admin, project_id, old["id"])
    assert retired["status"] == "draft"
    assert len(retired["approvals"]) == 3


def test_countersign_mid_reject_terminates(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "par-carol", "审批丙", "editor")
    assert _patch_chain(
        admin, project_id, [_stage([alice_id, bob_id]), _stage([carol_id])]
    ).status_code == 200

    version = _save(alice, project_id, "中途驳回", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200

    # level 2 cannot act while level 1 is pending
    early = _review(carol, project_id, version["id"], "reject")
    assert early.status_code == 403
    assert "第 1 级" in early.json()["detail"]

    assert _review(alice, project_id, version["id"], "approve").status_code == 200
    # alice already signed: cannot reject again at this all-level
    assert _review(alice, project_id, version["id"], "reject").status_code == 403

    # any authorized countersign approver may reject -> terminates immediately
    rejected = _review(bob, project_id, version["id"], "reject", "乙驳回")
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["approval_stage"] == 0
    assert [r["decision"] for r in body["approvals"]] == ["approve", "reject"]
    assert body["review_note"] == "乙驳回"
    assert body["reviewed_by"] == "审批乙"
    assert datetime.fromisoformat(body["reviewed_at"])


def test_resubmit_clears_parallel_progress(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")
    assert _patch_chain(admin, project_id, [_stage([alice_id, bob_id])]).status_code == 200

    version = _save(alice, project_id, "重提清空", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").status_code == 200
    assert _review(bob, project_id, version["id"], "reject").status_code == 200

    # resubmit voids the trail and restarts the countersign level
    resubmitted = _submit(alice, project_id, version["id"], "已整改")
    assert resubmitted.status_code == 200, resubmitted.text
    body = resubmitted.json()
    assert body["status"] == "submitted"
    assert body["approvals"] == []
    assert body["approval_stage"] == 0
    assert body["review_note"] == ""
    assert body["reviewed_by"] == ""
    assert body["reviewed_at"] is None

    # alice may sign again in the fresh round (no stale duplicate 403)
    again = _review(alice, project_id, version["id"], "approve")
    assert again.status_code == 200, again.text
    assert again.json()["approval_stage"] == 0
    final = _review(bob, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"


# ------------------------------------------------------------------ 或签 any


def test_or_sign_first_approval_advances(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "par-carol", "审批丙", "editor")
    outsider, _outsider_id = _member(admin, project_id, "par-outsider", "局外人", "editor")
    assert _patch_chain(
        admin, project_id, [_stage([alice_id, bob_id], "any"), _stage([carol_id])]
    ).status_code == 200

    version = _save(alice, project_id, "或签报价", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200

    # either approver may advance an any-level; alice goes first
    first = _review(alice, project_id, version["id"], "approve", "甲或签")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 1  # advanced on the first approval
    assert len(body["approvals"]) == 1

    # bob (level 1) and an outsider are now refused: level 2 is pending
    wrong = _review(bob, project_id, version["id"], "approve")
    assert wrong.status_code == 403
    assert "第 2 级" in wrong.json()["detail"]
    assert _review(outsider, project_id, version["id"], "approve").status_code == 403

    final = _review(carol, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"

    # a second version proves bob could equally have been the first signer
    other = _save(alice, project_id, "或签乙先", package_id)
    assert _submit(alice, project_id, other["id"]).status_code == 200
    bob_first = _review(bob, project_id, other["id"], "approve")
    assert bob_first.status_code == 200, bob_first.text
    assert bob_first.json()["approval_stage"] == 1
    assert _review(carol, project_id, other["id"], "approve").json()["status"] == "approved"


# ------------------------------------------------------------------ PATCH v2


def test_patch_v2_stage_and_level_limits(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    six_ids = [alice_id]
    for index in range(5):
        _client_i, user_id = _member(
            admin, project_id, f"par-extra-{index}", f"审批extra{index}", "editor"
        )
        six_ids.append(user_id)

    # 6 stages -> 400
    too_many_stages = _patch_chain(admin, project_id, [_stage([alice_id]) for _ in range(6)])
    assert too_many_stages.status_code == 400
    assert "审批链最多 5 级" in too_many_stages.json()["detail"]
    assert _chain_payload(admin, project_id) == []

    # 6 approvers in one level -> 400
    too_many_people = _patch_chain(admin, project_id, [_stage(six_ids)])
    assert too_many_people.status_code == 400
    assert _chain_payload(admin, project_id) == []

    # empty / missing approvers -> 400
    assert _patch_chain(admin, project_id, [{"approvers": []}]).status_code == 400
    assert _patch_chain(admin, project_id, [{"mode": "all"}]).status_code == 400
    assert _chain_payload(admin, project_id) == []

    # exactly 5 stages is fine
    five_stages = _patch_chain(admin, project_id, [_stage([alice_id]) for _ in range(5)])
    assert five_stages.status_code == 200, five_stages.text
    assert len(five_stages.json()["approval_chain"]) == 5

    # exactly 5 approvers in one level is fine
    five_people = _patch_chain(admin, project_id, [_stage(six_ids[:5])])
    assert five_people.status_code == 200, five_people.text
    assert len(five_people.json()["approval_chain"][0]["approvers"]) == 5


def test_patch_v2_mode_validation(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")

    for bad_mode in ("xor", "both", "ALL", ""):
        bad = _patch_chain(admin, project_id, [{"approvers": [alice_id], "mode": bad_mode}])
        assert bad.status_code == 400, bad_mode
        assert "审批模式" in bad.json()["detail"]
        assert _chain_payload(admin, project_id) == []

    # missing mode defaults to all
    default = _patch_chain(admin, project_id, [{"approvers": [alice_id]}])
    assert default.status_code == 200, default.text
    assert default.json()["approval_chain"] == [
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"}
    ]

    # explicit any is preserved
    any_mode = _patch_chain(admin, project_id, [{"approvers": [alice_id], "mode": "any"}])
    assert any_mode.status_code == 200, any_mode.text
    assert any_mode.json()["approval_chain"][0]["mode"] == "any"


def test_patch_v2_unknown_user(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")

    unknown = _patch_chain(admin, project_id, [{"approvers": [alice_id, "no-such-user"]}])
    assert unknown.status_code == 400
    assert "审批人不存在: no-such-user" in unknown.json()["detail"]
    # no partial chain written
    assert _chain_payload(admin, project_id) == []


def test_patch_v2_dedup_preserves_order(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    _bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")

    dedup = _patch_chain(
        admin, project_id, [{"approvers": [bob_id, alice_id, bob_id], "mode": "any"}]
    )
    assert dedup.status_code == 200, dedup.text
    assert dedup.json()["approval_chain"] == [
        {
            "approvers": [
                {"id": bob_id, "name": "审批乙"},
                {"id": alice_id, "name": "审批甲"},
            ],
            "mode": "any",
        }
    ]


def test_patch_v2_owner_only_and_clear(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    editor, _editor_id = _member(admin, project_id, "par-editor", "编辑丙", "editor")

    # editors cannot touch the chain (owner-only)
    forbidden = _patch_chain(editor, project_id, [_stage([alice_id])])
    assert forbidden.status_code == 403

    assert _patch_chain(admin, project_id, [_stage([alice_id], "any")]).status_code == 200
    updates = [d for a, d in _audit(project_id) if a == "project.update"]
    assert any("approval_chain" in d.get("keys", []) for d in updates)

    # empty list clears the chain
    cleared = _patch_chain(admin, project_id, [])
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["approval_chain"] == []
    assert _chain_payload(admin, project_id) == []


def test_legacy_flat_patch_echoes_v2(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "par-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "par-bob", "审批乙", "editor")

    # R18 flat input is accepted and echoed as v2 single-approver all stages
    patched = _patch_chain(admin, project_id, [alice_id, bob_id])
    assert patched.status_code == 200, patched.text
    expected = [
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"},
        {"approvers": [{"id": bob_id, "name": "审批乙"}], "mode": "all"},
    ]
    assert patched.json()["approval_chain"] == expected
    assert _chain_payload(admin, project_id) == expected

    # review semantics unchanged: sequential single-approver levels
    version = _save(alice, project_id, "扁平兼容", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").json()["approval_stage"] == 1
    final = _review(bob, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
