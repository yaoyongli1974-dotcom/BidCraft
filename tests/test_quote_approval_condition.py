"""报价审批条件级（R21）：金额门槛 min_amount 过滤生效审批级。

Covers the contract's A3 matrix: low amounts skip threshold levels straight to
the final applicable level, high amounts require them, equality applies, an
unknown amount is conservative (all levels apply), records keep the original
chain index, approval_stage_count reflects applicable levels, PATCH min_amount
validation (negative / over cap / bool / string / null / non-finite -> 400),
normalize keep/drop, pending inbox filtering, legacy threshold-free chains
unchanged, and countersign/any-mode combinations with thresholds (duplicate
403, reject termination, resubmit clearing). Client fixtures follow
test_quote_approval_parallel.py.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEMBER_PASS = "member-pass-1"
THRESHOLD = 1000
PENDING_KEYS = {
    "project_id",
    "project_name",
    "version_id",
    "version_name",
    "package_id",
    "stage",
    "stage_count",
    "mode",
    "submitted_by",
    "submitted_at",
    "total_amount",
    "approval_explain",
}


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


def _project(client, name: str = "条件审批项目") -> str:
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


def _seed(client, tmp_path):
    """Seed a package whose priced total is 25.0 (row 2: qty 2 x 12.5)."""
    project_id = _project(client)
    package_id = _package(client, project_id)
    path = _write_boq(
        tmp_path / "condition.xlsx",
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


def _price(client, project_id: str, row: int, price: float, package_id: str | None):
    response = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": row, "unit_price": price, "package_id": package_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


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


def _pending(client):
    response = client.get("/approvals/pending")
    assert response.status_code == 200, response.text
    return response.json()


def _stage(ids, mode="all", min_amount=None):
    stage = {"approvers": list(ids), "mode": mode}
    if min_amount is not None:
        stage["min_amount"] = min_amount
    return stage


def _strip_total_amount(version_id: str, variant: str):
    """Simulate an unknown version amount (missing key or explicit None)."""
    from bidcraft.db import session_scope
    from bidcraft.models import BoqQuoteVersion

    with session_scope() as db:
        row = db.get(BoqQuoteVersion, version_id)
        assert row is not None
        summary = dict(row.summary or {})
        if variant == "missing":
            summary.pop("total_amount", None)
        else:
            summary["total_amount"] = None
        row.summary = summary


# ------------------------------------------------------------------ 纯函数


def test_normalize_chain_min_amount_keep_and_drop():
    from bidcraft.boq import normalize_chain

    # valid thresholds (int / float / zero) are preserved on v2 stages
    for value in (0, 100, 2.5, 1e12):
        raw = [{"approvers": [{"id": "u1", "name": "甲"}], "mode": "any", "min_amount": value}]
        assert normalize_chain(raw) == raw

    # invalid values drop the key but keep the stage; other fields unchanged
    for bad in (-1, -0.5, "100", True, False, None, float("nan"), float("inf"), [], {}):
        normalized = normalize_chain(
            [{"approvers": [{"id": "u1", "name": "甲"}], "mode": "any", "min_amount": bad}]
        )
        assert normalized == [{"approvers": [{"id": "u1", "name": "甲"}], "mode": "any"}]

    # absent key stays absent; legacy flat entries may also carry a threshold
    assert normalize_chain([{"approvers": [{"id": "u1", "name": "甲"}]}]) == [
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "all"}
    ]
    assert normalize_chain([{"id": "u1", "name": "甲", "min_amount": 50}]) == [
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "all", "min_amount": 50}
    ]
    assert normalize_chain([{"id": "u1", "name": "甲", "min_amount": -3}]) == [
        {"approvers": [{"id": "u1", "name": "甲"}], "mode": "all"}
    ]


def test_applicable_stages_filter_and_indices():
    from bidcraft.boq import applicable_stages

    chain = [
        {"approvers": [{"id": "a"}], "mode": "all"},
        {"approvers": [{"id": "b"}], "mode": "all", "min_amount": THRESHOLD},
        {"approvers": [{"id": "c"}], "mode": "any", "min_amount": 0},
        {"approvers": [{"id": "d"}], "mode": "all"},
    ]
    everything = [(0, chain[0]), (1, chain[1]), (2, chain[2]), (3, chain[3])]

    # unknown amount -> conservative: every stage applies, original indices kept
    assert applicable_stages(chain, None) == everything
    assert applicable_stages(chain, "not-a-number") == everything
    # low amount skips only the threshold stage; min_amount 0 always applies
    assert applicable_stages(chain, 25) == [(0, chain[0]), (2, chain[2]), (3, chain[3])]
    assert applicable_stages(chain, 0) == [(0, chain[0]), (2, chain[2]), (3, chain[3])]
    # equality applies; just below does not
    assert applicable_stages(chain, THRESHOLD) == everything
    assert applicable_stages(chain, THRESHOLD - 0.01) == [
        (0, chain[0]),
        (2, chain[2]),
        (3, chain[3]),
    ]
    assert applicable_stages(chain, 1025) == everything
    # invalid stored thresholds behave like no threshold (conservative)
    messy = [
        {"approvers": [{"id": "a"}], "mode": "all", "min_amount": -5},
        {"approvers": [{"id": "b"}], "mode": "all", "min_amount": "x"},
    ]
    assert applicable_stages(messy, 0) == [(0, messy[0]), (1, messy[1])]
    assert applicable_stages([], 100) == []


def test_completed_stages_amount_skips_without_break():
    from bidcraft.boq import completed_stages

    chain = [
        {"approvers": [{"id": "a"}], "mode": "all"},
        {"approvers": [{"id": "b"}], "mode": "all", "min_amount": THRESHOLD},
        {"approvers": [{"id": "c"}], "mode": "all"},
    ]

    def rec(stage, uid, decision="approve"):
        return {"stage": stage, "user_id": uid, "decision": decision}

    # two-argument legacy calls keep the pre-R21 behavior (all stages apply)
    assert completed_stages(chain, []) == 0
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c")]) == 1  # stops at level 2
    assert completed_stages(chain, [rec(0, "a"), rec(1, "b"), rec(2, "c")]) == 3

    # low amount: the threshold level is skipped without breaking or counting;
    # records are matched by original chain index
    assert completed_stages(chain, [rec(2, "c")], 25) == 0  # level 1 still pending
    assert completed_stages(chain, [rec(0, "a")], 25) == 1
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c")], 25) == 2
    # a record on the skipped original index neither counts nor blocks
    assert completed_stages(chain, [rec(0, "a"), rec(1, "b"), rec(2, "c")], 25) == 2

    # high amount: the threshold level is required and stops the scan
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c")], 1025) == 1
    assert completed_stages(chain, [rec(0, "a"), rec(1, "b"), rec(2, "c")], 1025) == 3
    # equality applies; None stays conservative
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c")], THRESHOLD) == 1
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c")], None) == 1
    # reject records never count
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c", "reject")], 25) == 1


# ------------------------------------------------------------------ PATCH 校验


def test_patch_min_amount_validation(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")

    bad_values = [
        -1,
        -0.5,
        1e12 + 1,
        True,
        False,
        "1000",
        None,
        float("nan"),
        float("inf"),
        [],
        {},
    ]
    for bad in bad_values:
        # raw JSON body: the stdlib encoder emits NaN/Infinity tokens, which
        # the server must still reject (httpx's json= refuses to send them)
        response = admin.patch(
            f"/projects/{project_id}",
            content=json.dumps(
                {"approval_chain": [{"approvers": [alice_id], "min_amount": bad}]}
            ),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400, f"min_amount={bad!r}: {response.text}"
        assert response.json()["detail"] == "金额门槛无效"
        # no partial chain written
        assert _chain_payload(admin, project_id) == []

    # a bad threshold on a later stage also rejects the whole chain
    mixed = _patch_chain(
        admin,
        project_id,
        [{"approvers": [alice_id]}, {"approvers": [alice_id], "min_amount": -2}],
    )
    assert mixed.status_code == 400
    assert mixed.json()["detail"] == "金额门槛无效"
    assert _chain_payload(admin, project_id) == []


def test_patch_min_amount_snapshot_and_flat(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    _bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")

    # boundary values 0 and 1e12 plus a float are accepted and snapshotted as-is
    patched = _patch_chain(
        admin,
        project_id,
        [
            {"approvers": [alice_id], "min_amount": 0},
            {"approvers": [bob_id], "mode": "any", "min_amount": 25.5},
            {"approvers": [alice_id], "min_amount": 10**12},
            {"approvers": [bob_id]},
        ],
    )
    assert patched.status_code == 200, patched.text
    expected = [
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all", "min_amount": 0},
        {"approvers": [{"id": bob_id, "name": "审批乙"}], "mode": "any", "min_amount": 25.5},
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all", "min_amount": 10**12},
        {"approvers": [{"id": bob_id, "name": "审批乙"}], "mode": "all"},
    ]
    assert patched.json()["approval_chain"] == expected
    assert _chain_payload(admin, project_id) == expected
    assert "min_amount" not in _chain_payload(admin, project_id)[3]

    # R18 flat input is unaffected: no threshold keys appear
    flat = _patch_chain(admin, project_id, [alice_id, bob_id])
    assert flat.status_code == 200, flat.text
    assert flat.json()["approval_chain"] == [
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"},
        {"approvers": [{"id": bob_id, "name": "审批乙"}], "mode": "all"},
    ]


# ------------------------------------------------------------------ 金额门槛流转


def test_low_amount_skips_threshold_level(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id], min_amount=THRESHOLD), _stage([carol_id])],
    ).status_code == 200

    version = _save(alice, project_id, "低金额", package_id)
    assert version["summary"]["total_amount"] == 25.0
    assert _submit(alice, project_id, version["id"]).status_code == 200
    body = _detail(admin, project_id, version["id"])
    assert body["approval_stage_count"] == 2  # threshold level not applicable
    assert body["approval_stage"] == 0

    # the skipped level's approver cannot act while level 1 is pending
    early = _review(bob, project_id, version["id"], "approve")
    assert early.status_code == 403
    assert "第 1 级" in early.json()["detail"]

    first = _review(alice, project_id, version["id"], "approve")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 1
    assert body["approval_stage_count"] == 2
    assert body["approvals"][0]["stage"] == 0  # original chain index

    # bob is still skipped: the pending level is carol (applicable ordinal 2)
    skipped = _review(bob, project_id, version["id"], "approve")
    assert skipped.status_code == 403
    assert "第 2 级" in skipped.json()["detail"]

    final = _review(carol, project_id, version["id"], "approve", "末级定稿")
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["status"] == "approved"
    assert body["approval_stage"] == 0
    assert body["approval_stage_count"] == 2
    assert [r["stage"] for r in body["approvals"]] == [0, 2]  # original indices
    assert body["reviewed_by"] == "审批丙"


def test_high_amount_requires_threshold_level(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id], min_amount=THRESHOLD), _stage([carol_id])],
    ).status_code == 200
    _price(admin, project_id, 3, 100.0, package_id)  # total 25 + 1000 = 1025

    version = _save(alice, project_id, "高金额", package_id)
    assert version["summary"]["total_amount"] == 1025.0
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _detail(admin, project_id, version["id"])["approval_stage_count"] == 3

    assert _review(alice, project_id, version["id"], "approve").json()["approval_stage"] == 1
    # the final level cannot jump over the now-applicable threshold level
    early = _review(carol, project_id, version["id"], "approve")
    assert early.status_code == 403
    assert "第 2 级" in early.json()["detail"]

    middle = _review(bob, project_id, version["id"], "approve", "门槛级同意")
    assert middle.status_code == 200, middle.text
    body = middle.json()
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 2
    assert body["approvals"][1]["stage"] == 1

    final = _review(carol, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["status"] == "approved"
    assert [r["stage"] for r in body["approvals"]] == [0, 1, 2]
    assert body["approval_stage_count"] == 3


def test_amount_equal_threshold_applies(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id], min_amount=THRESHOLD), _stage([carol_id])],
    ).status_code == 200
    _price(admin, project_id, 2, 500.0, package_id)  # qty 2 x 500 = exactly 1000

    version = _save(alice, project_id, "恰好门槛", package_id)
    assert version["summary"]["total_amount"] == float(THRESHOLD)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _detail(admin, project_id, version["id"])["approval_stage_count"] == 3

    assert _review(alice, project_id, version["id"], "approve").json()["approval_stage"] == 1
    assert _review(carol, project_id, version["id"], "approve").status_code == 403
    assert _review(bob, project_id, version["id"], "approve").json()["approval_stage"] == 2
    final = _review(carol, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 1, 2]


@pytest.mark.parametrize("variant", ["missing", "none"])
def test_unknown_amount_conservative(db, tmp_path, variant):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id], min_amount=THRESHOLD), _stage([carol_id])],
    ).status_code == 200

    version = _save(alice, project_id, "金额未知", package_id)
    _strip_total_amount(version["id"], variant)
    submitted = _submit(alice, project_id, version["id"])
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["summary"].get("total_amount") is None
    # conservative: the threshold level counts as applicable
    assert body["approval_stage_count"] == 3
    assert body["approval_stage"] == 0

    assert _review(alice, project_id, version["id"], "approve").json()["approval_stage"] == 1
    early = _review(carol, project_id, version["id"], "approve")
    assert early.status_code == 403
    assert "第 2 级" in early.json()["detail"]
    assert _review(bob, project_id, version["id"], "approve").json()["approval_stage"] == 2
    final = _review(carol, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 1, 2]


def test_pending_inbox_filtered_by_threshold(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id], min_amount=THRESHOLD), _stage([carol_id])],
    ).status_code == 200

    low = _save(alice, project_id, "待审低", package_id)  # total 25
    assert _submit(alice, project_id, low["id"]).status_code == 200
    _price(admin, project_id, 3, 100.0, package_id)
    high = _save(alice, project_id, "待审高", package_id)  # total 1025
    assert _submit(alice, project_id, high["id"]).status_code == 200

    def by_version(items):
        return {item["version_id"]: item for item in items}

    alice_items = by_version(_pending(alice))
    assert set(alice_items) == {low["id"], high["id"]}
    assert set(alice_items[low["id"]]) == PENDING_KEYS
    assert (alice_items[low["id"]]["stage"], alice_items[low["id"]]["stage_count"]) == (1, 2)
    assert (alice_items[high["id"]]["stage"], alice_items[high["id"]]["stage_count"]) == (1, 3)
    assert alice_items[low["id"]]["total_amount"] == 25.0
    # level 1 is pending on both: bob/carol see nothing yet; super admin sees both
    assert _pending(bob) == []
    assert _pending(carol) == []
    assert set(by_version(_pending(admin))) == {low["id"], high["id"]}

    # alice signs the low version -> carol inherits it at applicable level 2/2
    # (the threshold level is skipped); the high version still waits on alice
    assert _review(alice, project_id, low["id"], "approve").status_code == 200
    carol_items = by_version(_pending(carol))
    assert set(carol_items) == {low["id"]}
    assert (carol_items[low["id"]]["stage"], carol_items[low["id"]]["stage_count"]) == (2, 2)
    assert set(by_version(_pending(alice))) == {high["id"]}
    assert _pending(bob) == []

    # alice signs the high version -> bob inherits it at applicable level 2/3
    assert _review(alice, project_id, high["id"], "approve").status_code == 200
    bob_items = by_version(_pending(bob))
    assert (bob_items[high["id"]]["stage"], bob_items[high["id"]]["stage_count"]) == (2, 3)
    assert set(by_version(_pending(carol))) == {low["id"]}

    # bob signs -> carol sees both versions at their final applicable level
    assert _review(bob, project_id, high["id"], "approve").status_code == 200
    carol_items = by_version(_pending(carol))
    assert set(carol_items) == {low["id"], high["id"]}
    assert (carol_items[low["id"]]["stage"], carol_items[low["id"]]["stage_count"]) == (2, 2)
    assert (carol_items[high["id"]]["stage"], carol_items[high["id"]]["stage_count"]) == (3, 3)

    assert _review(carol, project_id, low["id"], "approve").json()["status"] == "approved"
    assert _review(carol, project_id, high["id"], "approve").json()["status"] == "approved"
    assert _pending(alice) == []
    assert _pending(bob) == []
    assert _pending(carol) == []


def test_legacy_chain_without_threshold_unchanged(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")

    # threshold-free v2 chain behaves exactly like R19
    assert _patch_chain(
        admin, project_id, [_stage([alice_id, bob_id]), _stage([carol_id])]
    ).status_code == 200
    assert all("min_amount" not in s for s in _chain_payload(admin, project_id))
    version = _save(alice, project_id, "无门槛v2", package_id)
    assert version["approval_stage_count"] == 2
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").json()["approval_stage"] == 0
    assert _review(bob, project_id, version["id"], "approve").json()["approval_stage"] == 1
    final = _review(carol, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 0, 1]
    assert final.json()["approval_stage_count"] == 2

    # R18 flat input still yields single-approver all stages without thresholds
    assert _patch_chain(admin, project_id, [alice_id, carol_id]).status_code == 200
    flat_version = _save(alice, project_id, "无门槛扁平", package_id)
    assert flat_version["approval_stage_count"] == 2
    assert _submit(alice, project_id, flat_version["id"]).status_code == 200
    assert (
        _review(alice, project_id, flat_version["id"], "approve").json()["approval_stage"] == 1
    )
    final = _review(carol, project_id, flat_version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"


def test_countersign_with_threshold(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id, carol_id], min_amount=THRESHOLD)],
    ).status_code == 200

    # low amount: the countersign threshold level is skipped entirely
    low = _save(alice, project_id, "会签低", package_id)
    assert low["approval_stage_count"] == 1
    assert _submit(alice, project_id, low["id"]).status_code == 200
    assert _review(bob, project_id, low["id"], "approve").status_code == 403
    final = _review(alice, project_id, low["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0]

    # high amount: the countersign level needs both approvers
    _price(admin, project_id, 3, 100.0, package_id)
    high = _save(alice, project_id, "会签高", package_id)
    assert high["approval_stage_count"] == 2
    assert _submit(alice, project_id, high["id"]).status_code == 200
    assert _review(alice, project_id, high["id"], "approve").json()["approval_stage"] == 1
    partial = _review(bob, project_id, high["id"], "approve")
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "submitted"
    assert partial.json()["approval_stage"] == 1  # all-level not complete yet
    duplicate = _review(bob, project_id, high["id"], "approve")
    assert duplicate.status_code == 403
    assert "您已完成本级审批（第 2 级）" in duplicate.json()["detail"]
    final = _review(carol, project_id, high["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 1, 1]


def test_any_mode_threshold_reject_resubmit(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id, carol_id], "any", THRESHOLD)],
    ).status_code == 200
    _price(admin, project_id, 3, 100.0, package_id)  # total 1025

    # any-mode threshold level advances on the first signature and finalizes
    version = _save(alice, project_id, "或签门槛", package_id)
    assert version["approval_stage_count"] == 2
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").json()["approval_stage"] == 1
    final = _review(bob, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 1]

    # reject at the threshold level terminates; resubmit clears the round
    other = _save(alice, project_id, "门槛驳回", package_id)
    assert _submit(alice, project_id, other["id"]).status_code == 200
    assert _review(alice, project_id, other["id"], "approve").status_code == 200
    rejected = _review(carol, project_id, other["id"], "reject", "门槛级驳回")
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["review_note"] == "门槛级驳回"
    assert [r["stage"] for r in body["approvals"]] == [0, 1]

    resubmitted = _submit(alice, project_id, other["id"], "已整改")
    assert resubmitted.status_code == 200, resubmitted.text
    body = resubmitted.json()
    assert body["approvals"] == []
    assert body["approval_stage"] == 0
    assert body["approval_stage_count"] == 2
    assert _review(alice, project_id, other["id"], "approve").json()["approval_stage"] == 1
    # carol may equally sign the any-level in the fresh round
    final = _review(carol, project_id, other["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["user_id"] for r in final.json()["approvals"]] == [alice_id, carol_id]


def test_stage_count_across_statuses_and_empty_chain(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond-alice", "审批甲", "editor")
    _bob, bob_id = _member(admin, project_id, "cond-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "cond-carol", "审批丙", "editor")
    assert _patch_chain(
        admin,
        project_id,
        [_stage([alice_id]), _stage([bob_id], min_amount=THRESHOLD), _stage([carol_id])],
    ).status_code == 200

    # draft already reports the applicable count (low amount -> 2)
    version = _save(alice, project_id, "计数", package_id)
    assert version["approval_stage"] == 0
    assert version["approval_stage_count"] == 2
    listed = admin.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_id}
    ).json()
    assert listed[0]["approval_stage_count"] == 2

    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").json()[
        "approval_stage_count"
    ] == 2
    final = _review(carol, project_id, version["id"], "approve")
    assert final.json()["status"] == "approved"
    assert final.json()["approval_stage_count"] == 2  # kept after finalization
    assert final.json()["approval_stage"] == 0

    # empty chain -> zero applicable levels (single-owner review, R18)
    assert _patch_chain(admin, project_id, []).status_code == 200
    empty = _save(alice, project_id, "空链", package_id)
    assert empty["approval_stage_count"] == 0
    assert _detail(admin, project_id, empty["id"])["approval_stage_count"] == 0
