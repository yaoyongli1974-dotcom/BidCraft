"""报价审批条件分支（R25）：包件 packages / 币种 currencies 白名单。

Covers contract A6: normalize keeps/drops the two new keys (dedupe, order,
≤40 chars), applicable_stages package/currency matching plus min_amount
combinations, the empty-filter fallback to the whole chain, unknown-context
policy, completed_stages context passthrough, PATCH validation matrix (status
400 「条件无效」, no partial write), the meta snapshot, version_payload
approval_stage/approval_stage_count following the version context, the
pending inbox, and end-to-end review flows for USD/CNY and package gates
(records keep the original chain index). Client fixtures follow
test_quote_approval_condition.py (R21).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEMBER_PASS = "member-pass-1"
LONG_ITEM = "x" * 41


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


def _member(admin, project_id: str | None, username: str, display_name: str):
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
            json={"username": username, "role": "editor"},
        )
        assert added.status_code == 201, added.text
    return _login(username), user_id


def _project(client, name: str = "条件分支项目") -> str:
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


def _seed_package(client, project_id: str, tmp_path, package_id: str, name: str):
    """Upload and price one package; the priced total is 25.0."""
    path = _write_boq(
        tmp_path / f"{name}.xlsx",
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


def _seed(client, tmp_path):
    """Seed one package with a priced total of 25.0."""
    project_id = _project(client)
    package_id = _package(client, project_id)
    _seed_package(client, project_id, tmp_path, package_id, "seed")
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


def _set_policy(client, project_id: str, **updates):
    response = client.put(f"/projects/{project_id}/price-policy", json=updates)
    assert response.status_code == 200, response.text
    return response.json()


def _pending(client):
    response = client.get("/approvals/pending")
    assert response.status_code == 200, response.text
    return response.json()


def _stage(ids, mode="all", **conditions):
    stage = {"approvers": list(ids), "mode": mode}
    stage.update(conditions)
    return stage


# ------------------------------------------------------------------ 纯函数


def test_normalize_chain_conditions_keep_and_drop():
    from bidcraft.boq import normalize_chain

    person = [{"id": "u1", "name": "甲"}]
    # valid lists survive: order kept, duplicates collapsed, edges trimmed
    normalized = normalize_chain(
        [
            {
                "approvers": person,
                "mode": "any",
                "packages": ["p2", " p1 ", "p2"],
                "currencies": ["USD", "CNY"],
            }
        ]
    )
    assert normalized == [
        {
            "approvers": person,
            "mode": "any",
            "packages": ["p2", "p1"],
            "currencies": ["USD", "CNY"],
        }
    ]

    # 40-char items are fine; longer or blank ones are dropped individually
    assert normalize_chain(
        [{"approvers": person, "packages": ["x" * 40, "", "  ", LONG_ITEM, "ok"]}]
    ) == [{"approvers": person, "mode": "all", "packages": ["x" * 40, "ok"]}]

    # non-lists and lists without a usable item drop the key entirely
    for bad in ("p1", 1, True, None, {}, [], [""], [1, 2], [LONG_ITEM]):
        normalized = normalize_chain([{"approvers": person, "packages": bad}])
        assert normalized == [{"approvers": person, "mode": "all"}]
        normalized = normalize_chain([{"approvers": person, "currencies": bad}])
        assert normalized == [{"approvers": person, "mode": "all"}]

    # legacy flat entries may carry the new keys too; absent keys stay absent
    assert normalize_chain(
        [{"id": "u1", "name": "甲", "packages": ["p1"], "currencies": ["usd"]}]
    ) == [
        {
            "approvers": person,
            "mode": "all",
            "packages": ["p1"],
            "currencies": ["usd"],
        }
    ]


def test_applicable_stages_package_whitelist():
    from bidcraft.boq import applicable_stages

    chain = [
        {"approvers": [{"id": "a"}], "mode": "all"},
        {"approvers": [{"id": "b"}], "mode": "all", "packages": ["p1", "p2"]},
        {"approvers": [{"id": "c"}], "mode": "all", "packages": []},
    ]
    # membership matches; an empty list means unrestricted
    assert applicable_stages(chain, None, "p1") == [
        (0, chain[0]),
        (1, chain[1]),
        (2, chain[2]),
    ]
    assert applicable_stages(chain, None, "p2") == [
        (0, chain[0]),
        (1, chain[1]),
        (2, chain[2]),
    ]
    # unknown / non-matching package skips the restricted level only
    assert applicable_stages(chain, None, "p3") == [(0, chain[0]), (2, chain[2])]
    assert applicable_stages(chain, 25, None) == [(0, chain[0]), (2, chain[2])]
    assert applicable_stages(chain, 25, "") == [(0, chain[0]), (2, chain[2])]
    # whitespace is not a package id
    assert applicable_stages(chain, 25, "  ") == [(0, chain[0]), (2, chain[2])]
    assert applicable_stages([], 25, "p1") == []


def test_applicable_stages_currency_whitelist():
    from bidcraft.boq import applicable_stages

    chain = [
        {"approvers": [{"id": "a"}], "mode": "all"},
        {"approvers": [{"id": "b"}], "mode": "all", "currencies": ["USD"]},
        {"approvers": [{"id": "c"}], "mode": "all", "currencies": ["CNY", "eur"]},
    ]
    # codes compare normalized (case/space insensitive)
    assert applicable_stages(chain, 25, None, "USD") == [
        (0, chain[0]),
        (1, chain[1]),
    ]
    assert applicable_stages(chain, 25, None, " usd ") == [(0, chain[0]), (1, chain[1])]
    assert applicable_stages(chain, 25, None, "EUR") == [(0, chain[0]), (2, chain[2])]
    assert applicable_stages(chain, 25, None, "CNY") == [(0, chain[0]), (2, chain[2])]
    # unknown currency skips the restricted levels
    assert applicable_stages(chain, 25, None, None) == [(0, chain[0])]
    assert applicable_stages(chain, 25, None, "") == [(0, chain[0])]
    assert applicable_stages(chain, 25, None, "JPY") == [(0, chain[0])]


def test_applicable_stages_combined_conditions_and_fallback():
    from bidcraft.boq import applicable_stages

    threshold = 1000
    chain = [
        {"approvers": [{"id": "a"}], "mode": "all"},
        {
            "approvers": [{"id": "b"}],
            "mode": "all",
            "min_amount": threshold,
            "packages": ["p1"],
            "currencies": ["USD"],
        },
        {"approvers": [{"id": "c"}], "mode": "all", "packages": ["p2"]},
    ]
    # every condition must hold: high amount alone is not enough
    assert applicable_stages(chain, 25, "p1", "USD") == [(0, chain[0])]
    assert applicable_stages(chain, 1025, "p1", "USD") == [
        (0, chain[0]),
        (1, chain[1]),
    ]
    assert applicable_stages(chain, 1025, "p1", "CNY") == [(0, chain[0])]
    assert applicable_stages(chain, 1025, "p2", "USD") == [
        (0, chain[0]),
        (2, chain[2]),
    ]
    # unknown amount keeps the amount condition satisfied
    assert applicable_stages(chain, None, "p1", "USD") == [
        (0, chain[0]),
        (1, chain[1]),
    ]

    # every stage filtered out -> fall back to the whole chain (never vacant)
    blocked = [
        {"approvers": [{"id": "a"}], "mode": "all", "packages": ["ghost"]},
        {"approvers": [{"id": "b"}], "mode": "all", "currencies": ["XXX"]},
    ]
    assert applicable_stages(blocked, 25, "p1", "CNY") == [
        (0, blocked[0]),
        (1, blocked[1]),
    ]
    # ... while an empty chain stays empty
    assert applicable_stages([], 25, "p1", "CNY") == []


def test_completed_stages_context_passthrough():
    from bidcraft.boq import completed_stages

    chain = [
        {"approvers": [{"id": "a"}], "mode": "all"},
        {"approvers": [{"id": "b"}], "mode": "all", "packages": ["p1"]},
        {"approvers": [{"id": "c"}], "mode": "all", "currencies": ["USD"]},
        {"approvers": [{"id": "d"}], "mode": "all"},
    ]

    def rec(stage, uid, decision="approve"):
        return {"stage": stage, "user_id": uid, "decision": decision}

    # package + currency context: only levels 0/1 apply, then 2 is skipped too
    assert completed_stages(chain, [rec(0, "a")], 25, "p1", "USD") == 1
    assert completed_stages(chain, [rec(0, "a"), rec(1, "b")], 25, "p1", "USD") == 2
    narrowed = [{"approvers": [{"id": "a"}], "mode": "all", "currencies": ["EUR"]},
                {"approvers": [{"id": "d"}], "mode": "all"}]
    assert completed_stages(narrowed, [rec(1, "d")], 25, None, "USD") == 1
    assert completed_stages(narrowed, [rec(0, "a"), rec(1, "d")], 25, None, "EUR") == 2
    # skipped index records neither count nor block
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c")], 25, "p2", "CNY") == 1
    # legacy two-argument calls stay untouched
    assert completed_stages(chain, [rec(0, "a"), rec(2, "c")]) == 1
    # a partially matching chain keeps only its matching level (no fallback)
    assert (
        completed_stages(
            [
                {"approvers": [{"id": "a"}], "mode": "all", "packages": ["ghost"]},
                {"approvers": [{"id": "b"}], "mode": "all"},
            ],
            [rec(0, "a")],
            25,
            "p1",
            "CNY",
        )
        == 0
    )
    # when no level matches, the fallback chain counts every level
    assert (
        completed_stages(
            [{"approvers": [{"id": "a"}], "mode": "all", "packages": ["ghost"]}],
            [rec(0, "a")],
            25,
            "p1",
            "CNY",
        )
        == 1
    )


# ------------------------------------------------------------------ PATCH 校验


def test_patch_conditions_validation(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")

    bad_values = [
        "p1",
        1,
        True,
        None,
        {},
        [""],
        ["   "],
        [1],
        [True],
        [["p1"]],
        [LONG_ITEM],
    ]
    for key in ("packages", "currencies"):
        for bad in bad_values:
            response = admin.patch(
                f"/projects/{project_id}",
                content=json.dumps(
                    {"approval_chain": [{"approvers": [alice_id], key: bad}]}
                ),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400, f"{key}={bad!r}: {response.text}"
            assert response.json()["detail"] == "条件无效"
            assert _chain_payload(admin, project_id) == []

    # over-long lists (21 distinct items) are rejected for both keys
    for key in ("packages", "currencies"):
        response = _patch_chain(
            admin, project_id, [{"approvers": [alice_id], key: [f"v{i}" for i in range(21)]}]
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "条件无效"
        assert _chain_payload(admin, project_id) == []

    # one bad later stage rejects the whole chain (no partial write)
    mixed = _patch_chain(
        admin,
        project_id,
        [
            {"approvers": [alice_id], "packages": ["p1"]},
            {"approvers": [alice_id], "currencies": [LONG_ITEM]},
        ],
    )
    assert mixed.status_code == 400
    assert mixed.json()["detail"] == "条件无效"
    assert _chain_payload(admin, project_id) == []


def test_patch_conditions_snapshot(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    _bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")

    # 20 items is the accepted boundary; order kept, duplicates collapsed
    boundary = [f"pkg-{i}" for i in range(20)]
    assert (
        _patch_chain(
            admin, project_id, [{"approvers": [alice_id], "packages": boundary}]
        ).status_code
        == 200
    )
    assert _chain_payload(admin, project_id)[0]["packages"] == boundary

    patched = _patch_chain(
        admin,
        project_id,
        [
            {"approvers": [alice_id], "mode": "any", "packages": ["B", "A", "B"]},
            {"approvers": [bob_id], "currencies": ["USD", "EUR"], "min_amount": 10},
            {"approvers": [alice_id]},
        ],
    )
    assert patched.status_code == 200, patched.text
    expected = [
        {
            "approvers": [{"id": alice_id, "name": "审批甲"}],
            "mode": "any",
            "packages": ["B", "A"],
        },
        {
            "approvers": [{"id": bob_id, "name": "审批乙"}],
            "mode": "all",
            "currencies": ["USD", "EUR"],
            "min_amount": 10,
        },
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"},
    ]
    assert patched.json()["approval_chain"] == expected
    assert _chain_payload(admin, project_id) == expected

    # an explicit empty list is stored as given and reads back as unrestricted
    empty = _patch_chain(
        admin, project_id, [{"approvers": [alice_id], "packages": [], "currencies": []}]
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["approval_chain"] == [
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"}
    ]

    # R18 flat input is unaffected: no condition keys appear
    flat = _patch_chain(admin, project_id, [alice_id, bob_id])
    assert flat.status_code == 200, flat.text
    assert all(
        "packages" not in stage and "currencies" not in stage
        for stage in flat.json()["approval_chain"]
    )


# ------------------------------------------------------------------ 上下文流转


def test_currency_stage_applies_under_usd(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")
    carol, carol_id = _member(admin, project_id, "cond2-carol", "审批丙")
    assert _set_policy(admin, project_id, currency="USD")["currency"] == "USD"
    assert (
        _patch_chain(
            admin,
            project_id,
            [
                _stage([alice_id]),
                _stage([bob_id], currencies=["usd"]),
                _stage([carol_id]),
            ],
        ).status_code
        == 200
    )

    version = _save(alice, project_id, "美元版", package_id)
    assert version["approval_stage_count"] == 3
    assert _submit(alice, project_id, version["id"]).status_code == 200
    body = _detail(admin, project_id, version["id"])
    assert (body["approval_stage"], body["approval_stage_count"]) == (0, 3)

    assert _review(alice, project_id, version["id"], "approve").json()[
        "approval_stage"
    ] == 1
    # carol cannot jump over the now-applicable middle level
    early = _review(carol, project_id, version["id"], "approve")
    assert early.status_code == 403
    assert "第 2 级" in early.json()["detail"]
    middle = _review(bob, project_id, version["id"], "approve")
    assert middle.json()["approval_stage"] == 2
    assert middle.json()["approvals"][1]["stage"] == 1  # original chain index
    final = _review(carol, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 1, 2]


def test_currency_stage_skipped_under_cny(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")
    carol, carol_id = _member(admin, project_id, "cond2-carol", "审批丙")
    assert _set_policy(admin, project_id, currency="CNY")["currency"] == "CNY"
    assert (
        _patch_chain(
            admin,
            project_id,
            [
                _stage([alice_id]),
                _stage([bob_id], currencies=["USD"]),
                _stage([carol_id]),
            ],
        ).status_code
        == 200
    )

    version = _save(alice, project_id, "人民币版", package_id)
    assert version["approval_stage_count"] == 2
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _detail(admin, project_id, version["id"])["approval_stage_count"] == 2

    # bob is skipped entirely: level 1 still waits on alice
    early = _review(bob, project_id, version["id"], "approve")
    assert early.status_code == 403
    assert "第 1 级" in early.json()["detail"]
    assert _review(alice, project_id, version["id"], "approve").json()[
        "approval_stage"
    ] == 1
    # the pending applicable level is 2/2 and belongs to carol
    skipped = _review(bob, project_id, version["id"], "approve")
    assert skipped.status_code == 403
    assert "第 2 级" in skipped.json()["detail"]
    final = _review(carol, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 2]


def test_package_stage_matches_version_package(db, tmp_path):
    admin = _client()
    project_id, package_a = _seed(admin, tmp_path)
    package_b = _package(admin, project_id, "B 标段")
    _seed_package(admin, project_id, tmp_path, package_b, "second")
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")
    carol, carol_id = _member(admin, project_id, "cond2-carol", "审批丙")
    assert (
        _patch_chain(
            admin,
            project_id,
            [
                _stage([alice_id]),
                _stage([bob_id], packages=[package_b]),
                _stage([carol_id]),
            ],
        ).status_code
        == 200
    )

    # version of package B: the package-restricted level applies
    version_b = _save(alice, project_id, "B 版", package_b)
    assert version_b["package_id"] == package_b
    assert version_b["approval_stage_count"] == 3
    assert _submit(alice, project_id, version_b["id"]).status_code == 200
    assert _review(bob, project_id, version_b["id"], "approve").status_code == 403
    assert _review(alice, project_id, version_b["id"], "approve").json()[
        "approval_stage"
    ] == 1
    assert _review(bob, project_id, version_b["id"], "approve").json()[
        "approval_stage"
    ] == 2

    # version of package A: the same level is skipped
    version_a = _save(alice, project_id, "A 版", package_a)
    assert version_a["package_id"] == package_a
    assert version_a["approval_stage_count"] == 2
    assert _submit(alice, project_id, version_a["id"]).status_code == 200
    early = _review(bob, project_id, version_a["id"], "approve")
    assert early.status_code == 403
    assert "第 1 级" in early.json()["detail"]
    assert _review(alice, project_id, version_a["id"], "approve").status_code == 200
    skipped = _review(bob, project_id, version_a["id"], "approve")
    assert skipped.status_code == 403
    assert "第 2 级" in skipped.json()["detail"]
    final = _review(carol, project_id, version_a["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 2]


def test_all_stages_filtered_falls_back_to_full_chain(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")
    # no stored stage matches this package/currency -> every level stays active
    assert (
        _patch_chain(
            admin,
            project_id,
            [
                _stage([alice_id], packages=["ghost-package"]),
                _stage([bob_id], currencies=["XXX"]),
            ],
        ).status_code
        == 200
    )

    version = _save(alice, project_id, "回退全链", package_id)
    assert version["approval_stage_count"] == 2
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").json()[
        "approval_stage"
    ] == 1
    second = _review(bob, project_id, version["id"], "approve")
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"
    assert [r["stage"] for r in second.json()["approvals"]] == [0, 1]


def test_pending_inbox_respects_conditions(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")
    assert (
        _patch_chain(
            admin,
            project_id,
            [_stage([alice_id]), _stage([bob_id], currencies=["USD"])],
        ).status_code
        == 200
    )

    # CNY version: the USD level is skipped, so bob never inherits it
    cny = _save(alice, project_id, "CNY 待审", package_id)
    assert cny["approval_stage_count"] == 1
    assert _submit(alice, project_id, cny["id"]).status_code == 200
    items = {item["version_id"]: item for item in _pending(alice)}
    assert (items[cny["id"]]["stage"], items[cny["id"]]["stage_count"]) == (1, 1)
    assert _pending(bob) == []
    assert _review(alice, project_id, cny["id"], "approve").json()["status"] == "approved"
    assert _pending(bob) == []

    # USD version: bob receives it at level 2/2
    assert _set_policy(admin, project_id, currency="USD")["currency"] == "USD"
    usd = _save(alice, project_id, "USD 待审", package_id)
    assert usd["approval_stage_count"] == 2
    assert _submit(alice, project_id, usd["id"]).status_code == 200
    assert _pending(bob) == []
    assert _review(alice, project_id, usd["id"], "approve").status_code == 200
    items = {item["version_id"]: item for item in _pending(bob)}
    assert (items[usd["id"]]["stage"], items[usd["id"]]["stage_count"]) == (2, 2)
    assert _review(bob, project_id, usd["id"], "approve").json()["status"] == "approved"
    assert _pending(bob) == []


def test_version_payload_count_follows_policy_switch(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")
    _carol, carol_id = _member(admin, project_id, "cond2-carol", "审批丙")
    assert (
        _patch_chain(
            admin,
            project_id,
            [
                _stage([alice_id]),
                _stage([bob_id], currencies=["USD"]),
                _stage([carol_id], packages=["ghost-package"]),
            ],
        ).status_code
        == 200
    )

    version = _save(alice, project_id, "上下文计数", package_id)
    # CNY: USD level skipped; ghost-package level skipped but the chain is not
    # empty (level 1 applies), so the fallback does not kick in
    assert version["approval_stage_count"] == 1
    assert version["approval_stage"] == 0

    assert _set_policy(admin, project_id, currency="USD")["currency"] == "USD"
    listed = admin.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_id}
    ).json()
    assert listed[0]["approval_stage_count"] == 2

    # approval_stage only reports a pending level while submitted
    assert _detail(admin, project_id, version["id"])["approval_stage"] == 0
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _detail(admin, project_id, version["id"])["approval_stage"] == 0
    assert _review(alice, project_id, version["id"], "approve").json()[
        "approval_stage"
    ] == 1
    final = _review(bob, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert final.json()["approval_stage"] == 0
    assert [r["stage"] for r in final.json()["approvals"]] == [0, 1]


def test_conditions_reject_and_resubmit(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    bob, bob_id = _member(admin, project_id, "cond2-bob", "审批乙")
    assert _set_policy(admin, project_id, currency="USD")["currency"] == "USD"
    assert (
        _patch_chain(
            admin,
            project_id,
            [
                _stage([alice_id]),
                _stage([bob_id], "any", currencies=["USD"], packages=[package_id]),
            ],
        ).status_code
        == 200
    )

    version = _save(alice, project_id, "驳回重提", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").status_code == 200
    rejected = _review(bob, project_id, version["id"], "reject", "条件级驳回")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["review_note"] == "条件级驳回"

    resubmitted = _submit(alice, project_id, version["id"], "已整改")
    assert resubmitted.status_code == 200, resubmitted.text
    body = resubmitted.json()
    assert body["approvals"] == []
    assert (body["approval_stage"], body["approval_stage_count"]) == (0, 2)
    # an unconfigured currency no longer matches: only level 1 applies now
    assert _set_policy(admin, project_id, currency="EUR")["currency"] == "EUR"
    detail = _detail(admin, project_id, version["id"])
    assert detail["approval_stage_count"] == 1
    assert (
        _review(alice, project_id, version["id"], "approve").json()["status"]
        == "approved"
    )


def test_conditions_from_draft_do_not_leak_into_other_projects(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    other_id = _project(admin, "无链项目")
    other_pkg = _package(admin, other_id, "其他标段")
    _seed_package(admin, other_id, tmp_path, other_pkg, "other")
    alice, alice_id = _member(admin, project_id, "cond2-alice", "审批甲")
    assert (
        _patch_chain(
            admin,
            project_id,
            [_stage([alice_id], packages=[package_id], currencies=["CNY"])],
        ).status_code
        == 200
    )

    # the conditioned project applies its level under the default CNY policy
    version = _save(alice, project_id, "本项目", package_id)
    assert version["approval_stage_count"] == 1
    assert _save(admin, other_id, "无链项目版本", other_pkg)["approval_stage_count"] == 0
