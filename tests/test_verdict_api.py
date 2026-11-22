"""五层判定的 API 集成：新字段对外可见 + 四态门禁级别正确。

验收重点（用户确认的门禁策略）：
- 不满足 / 部分满足 → 阻塞导出（blocking / critical）；
- 人工复核 → info，可见但不阻塞导出；
- 满足 → 无 SUB 阻断问题。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "verdict-api-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def owner(db):
    admin = _login(*ADMIN)
    response = admin.post(
        "/admin/users",
        json={
            "username": "v-owner",
            "display_name": "v-owner",
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert response.status_code == 201, response.text
    return _login("v-owner")


def _project(client) -> str:
    r = client.post("/projects", json={"name": "五层判定项目", "package": "weakcurrent-cabling"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _seed(client=None, *, project_id: str, text: str, marker: str, body: str, page=0, clause_no=""):
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Requirement, Section

    with session_scope() as db:
        row = Requirement(
            project_id=project_id,
            kind="substantive",
            text=text,
            mandatory=bool(marker),
            source_quote=text[:40],
            marker=marker,
            response_status="pending",
            page=page,
            clause_no=clause_no,
        )
        db.add(row)
        node = OutlineNode(project_id=project_id, heading="技术方案", position=1)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body=body,
                status="written",
                words=len(body),
                review_status="confirmed",
            )
        )
        db.flush()
        return row.id


def test_requirements_payload_exposes_five_layer_fields(owner):
    pid = _project(owner)
    req_id = _seed(
        project_id=pid,
        text="★ 传输带宽不小于1000Mbps",
        marker="★",
        body="带宽500Mbps",
        page=21,
        clause_no="4.1.2",
    )
    items = owner.get(f"/projects/{pid}/requirements").json()
    row = next(item for item in items if item["id"] == req_id)
    assert row["page"] == 21 and row["clause_no"] == "4.1.2"
    assert row["verdict"] == "unsatisfied"
    assert row["verdict_label"] == "不满足"
    assert row["verdict_level"] == "blocking"
    layers = row["verdict_layers"]
    assert set(layers) == {"layer1", "layer2", "layer3", "layer4", "layer5"}
    assert layers["layer1"]["page"] == 21


def test_review_level_by_marker(owner):
    """实质性复核按标记分级：★ blocking（拦导出）、▲ critical（仅提示）。

    普通条款（无 ★/▲ 且非 mandatory）不属于实质性条款，不产 SUB 问题；
    其 info 级别由 test_verdict.py 的纯函数用例覆盖。
    """

    cases = [
        ("★", "投标人须提供详细的项目实施方案说明", "blocking"),
        ("▲", "投标人须提供详细的售后服务方案说明", "critical"),
    ]
    bodies = [
        "本项目实施方案将另行编制成册。",
        "服务方案的具体内容另附材料。",
    ]
    for (marker, text, expected_level), body in zip(cases, bodies):
        pid = _project(owner)
        _seed(project_id=pid, text=text, marker=marker, body=body)
        checked = owner.post(f"/projects/{pid}/check")
        assert checked.status_code == 200, checked.text
        overview = owner.get(f"/projects/{pid}/risk-overview").json()
        sub_items = [item for item in overview["items"] if item["code"].startswith("SUB-")]
        assert len(sub_items) == 1, (marker, sub_items)
        assert sub_items[0]["level"] == expected_level, (marker, sub_items[0])
        blocking = [item for item in sub_items if item["level"] == "blocking"]
        assert bool(blocking) is (marker == "★")


def test_unsatisfied_blocks_export(owner):
    pid = _project(owner)
    _seed(
        project_id=pid,
        text="★ 传输带宽不小于1000Mbps",
        marker="★",
        body="带宽500Mbps",
    )
    checked = owner.post(f"/projects/{pid}/check")
    assert checked.status_code == 200, checked.text
    overview = owner.get(f"/projects/{pid}/risk-overview").json()
    assert overview["gate"]["blocked"] is True, overview["gate"]
    blocked = owner.post(f"/projects/{pid}/export", json={"format": "docx"})
    assert blocked.status_code == 409


def test_confirmation_records_original_verdict(owner):
    """人工确认快照原始判定：人员/时间/原因/判定，写入 Requirement 与审计。"""

    pid = _project(owner)
    req_id = _seed(
        project_id=pid,
        text="★ 传输带宽不小于1000Mbps",
        marker="★",
        body="带宽500Mbps",
    )
    confirmed = owner.patch(
        f"/projects/{pid}/requirements/{req_id}",
        json={"response_status": "responded", "note": "第 5 章已按千兆光纤响应"},
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["responded_by"] == "v-owner"
    assert payload["responded_at"].endswith("+08:00")
    assert payload["response_verdict"] == "unsatisfied"
    assert payload["response_verdict_label"] == "不满足"
    assert "1000" in payload["response_verdict_reason"]

    audit = owner.get(f"/projects/{pid}/audit?limit=50").json()["items"]
    entry = next(item for item in audit if item["action"] == "requirement.response")
    assert entry["detail"]["verdict"] == "unsatisfied"
    assert entry["detail"]["verdict_label"] == "不满足"
    assert entry["detail"]["note"] == "第 5 章已按千兆光纤响应"

    # 撤回确认时快照一并清除。
    cleared = owner.patch(
        f"/projects/{pid}/requirements/{req_id}", json={"response_status": "pending"}
    )
    assert cleared.json()["response_verdict"] == ""


def test_force_export_audits_overridden_substantive(owner):
    """强制导出越过 ★ 复核时，审计追加被越过条款与原始判定。"""

    pid = _project(owner)
    _seed(
        project_id=pid,
        text="★ 投标人须提供详细的项目实施方案说明",
        marker="★",
        body="本项目实施方案另行编制，详见后续附件。",
    )
    checked = owner.post(f"/projects/{pid}/check")
    assert checked.status_code == 200, checked.text

    # ★ review → blocking，普通导出被拦。
    assert owner.post(f"/projects/{pid}/export", json={"format": "docx"}).status_code == 409
    forced = owner.post(
        f"/projects/{pid}/export",
        json={"format": "docx", "force": True, "force_reason": "业主确认风险可控"},
    )
    assert forced.status_code == 200, forced.text

    audit = owner.get(f"/projects/{pid}/audit?limit=50").json()["items"]
    entry = next(item for item in audit if item["action"] == "export.force")
    overridden = entry["detail"]["overridden_substantives"]
    assert len(overridden) == 1
    assert overridden[0]["marker"] == "★"
    assert overridden[0]["verdict"] == "review"
    assert overridden[0]["code"].startswith("SUB-")
