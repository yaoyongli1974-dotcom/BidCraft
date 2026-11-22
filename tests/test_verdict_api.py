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


def test_review_info_does_not_block_export(owner):
    pid = _project(owner)
    # 纯语义条款 + 弱信号 → 人工复核（info），不进 blocking_open。
    _seed(
        project_id=pid,
        text="★ 投标人须提供详细的项目实施方案说明",
        marker="★",
        body="本项目实施方案另行编制，详见后续附件。",
    )
    checked = owner.post(f"/projects/{pid}/check")
    assert checked.status_code == 200, checked.text
    overview = owner.get(f"/projects/{pid}/risk-overview").json()
    # 人工复核条款产出的 SUB 问题必须是 info（非 blocking），不计入阻断门禁。
    sub_items = [item for item in overview["items"] if item["code"].startswith("SUB-")]
    assert len(sub_items) == 1, sub_items
    assert sub_items[0]["level"] == "info", sub_items[0]
    assert sub_items[0]["evidence"]["verdict"] == "review"


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
