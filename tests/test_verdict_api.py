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


def test_requirements_endpoint_loads_outline_once_per_request(owner, monkeypatch):
    """T06：GET /requirements 多条条款只加载一次大纲，且请求间缓存隔离。"""

    import importlib

    shared_mod = importlib.import_module("bidcraft.api.shared")

    real_loader = shared_mod.load_outline
    calls = {"n": 0}

    def _counting_loader(db, project_id):
        calls["n"] += 1
        return real_loader(db, project_id)

    monkeypatch.setattr(shared_mod, "load_outline", _counting_loader)

    pid = _project(owner)
    # 用 _seed 造三条条款（每条都会带一个 OutlineNode + Section）。
    _seed(project_id=pid, text="★ 带宽不小于1000Mbps", marker="★", body="带宽500Mbps")
    _seed(project_id=pid, text="▲ 质保5年", marker="▲", body="质保3年")
    _seed(project_id=pid, text="★ 2小时响应", marker="★", body="4小时响应")

    items = owner.get(f"/projects/{pid}/requirements").json()
    assert len(items) == 3
    # 三条条款共享一次大纲加载，而不是三次。
    assert calls["n"] == 1, calls

    # 再发一次请求（模拟大纲更新后的新请求）→ 重新加载，不使用陈旧缓存。
    owner.get(f"/projects/{pid}/requirements")
    assert calls["n"] == 2, calls


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


# ---------------------------------------------------------------------------
# R05：彻底消除 GET /requirements 的 OutlineNode N+1
# ---------------------------------------------------------------------------


def _seed_bulk(project_id: str, count: int, node_count: int) -> None:
    """造 count 条条款、分布在 node_count 个章节节点上（R05 的压力场景）。"""

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Requirement, Section

    with session_scope() as db:
        nodes = []
        for i in range(node_count):
            node = OutlineNode(project_id=project_id, heading=f"章节{i}", position=i)
            db.add(node)
            nodes.append(node)
        db.flush()
        for node in nodes:
            db.add(
                Section(
                    node_id=node.id,
                    body="系统带宽500Mbps，质保3年。",
                    status="written",
                    words=10,
                    review_status="confirmed",
                )
            )
        for i in range(count):
            db.add(
                Requirement(
                    project_id=project_id,
                    kind="substantive",
                    text=f"★ 第{i}条带宽不小于1000Mbps",
                    source_quote="带宽不小于1000Mbps",
                    marker="★",
                    mandatory=True,
                    node_id=nodes[i % node_count].id,
                    response_status="pending",
                )
            )
        db.flush()


def _seed_linked(project_id: str, heading: str = "技术方案") -> str:
    """造一条带 node_id 链接的条款，使 section_heading 可被验证（R05）。"""

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Requirement, Section

    with session_scope() as db:
        node = OutlineNode(project_id=project_id, heading=heading, position=1)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body="系统带宽500Mbps。",
                status="written",
                words=8,
                review_status="confirmed",
            )
        )
        row = Requirement(
            project_id=project_id,
            kind="substantive",
            text="★ 带宽不小于1000Mbps",
            source_quote="带宽不小于1000Mbps",
            marker="★",
            mandatory=True,
            node_id=node.id,
            response_status="pending",
        )
        db.add(row)
        db.flush()
        return row.id


def test_requirements_endpoint_has_no_per_requirement_db_get(owner, monkeypatch):
    """R05：列表渲染不再对每条条款执行 db.get(OutlineNode)。"""

    from sqlalchemy.orm import Session

    calls = {"outline_get": 0}
    real_get = Session.get

    def counting_get(self, entity, ident, *args, **kwargs):
        if getattr(entity, "__name__", "") == "OutlineNode":
            calls["outline_get"] += 1
        return real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", counting_get)

    pid = _project(owner)
    _seed_bulk(pid, count=100, node_count=50)
    items = owner.get(f"/projects/{pid}/requirements").json()
    assert len(items) == 100
    # 旧实现每条条款一次 db.get(OutlineNode) → 100 次；新实现为 0。
    assert calls["outline_get"] == 0, calls


def test_requirements_endpoint_uses_one_outline_query(owner):
    """R05：100 条款/50 章节下，outline_nodes 的 SELECT 次数不随条款数线性增长。"""

    from sqlalchemy import event

    from bidcraft.db import get_engine

    pid = _project(owner)
    _seed_bulk(pid, count=100, node_count=50)

    engine = get_engine()
    sql = {"outline_select": 0}

    def before_cursor(conn, cursor, statement, params, context, executemany):
        upper = statement.upper()
        if "SELECT" in upper and "OUTLINE_NODES" in upper:
            sql["outline_select"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor)
    try:
        items = owner.get(f"/projects/{pid}/requirements").json()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor)
    assert len(items) == 100
    # 一次 load_outline 只查一次 outline_nodes；不得随 100 条条款增长。
    assert sql["outline_select"] == 1, sql


def test_requirements_endpoint_preloads_node_map(owner, monkeypatch):
    """R05：一次 load_outline 同时构建 node_id→heading 索引，供 section_heading 展示。"""

    import importlib

    shared_mod = importlib.import_module("bidcraft.api.shared")
    real_loader = shared_mod.load_outline
    calls = {"n": 0}

    def counting_loader(db, project_id):
        calls["n"] += 1
        return real_loader(db, project_id)

    monkeypatch.setattr(shared_mod, "load_outline", counting_loader)

    pid = _project(owner)
    _seed_linked(pid)
    items = owner.get(f"/projects/{pid}/requirements").json()
    assert items[0]["section_heading"] == "技术方案"
    assert calls["n"] == 1, calls


def test_requirements_multiple_projects_isolated(owner):
    """R05：不同项目的 node_map 不串（按 project_id 隔离）。"""

    import importlib

    from bidcraft.db import session_scope

    shared_mod = importlib.import_module("bidcraft.api.shared")

    p1 = _project(owner)
    p2 = _project(owner)
    _seed(project_id=p1, text="★ 甲项目带宽", marker="★", body="带宽500Mbps")
    _seed(project_id=p2, text="★ 乙项目质保", marker="★", body="质保3年")

    with session_scope() as db:
        c1 = shared_mod.build_requirement_context(db, p1)
        c2 = shared_mod.build_requirement_context(db, p2)
    assert set(c1.node_map).isdisjoint(c2.node_map)
    assert set(c1.node_map.values()) == {"技术方案"}
    assert set(c2.node_map.values()) == {"技术方案"}


def test_outline_update_visible_on_next_request(owner):
    """R05：大纲更新后下一次请求必须拿到最新 heading（无跨请求陈旧缓存）。"""

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode

    pid = _project(owner)
    _seed_linked(pid)
    first = owner.get(f"/projects/{pid}/requirements").json()
    assert first[0]["section_heading"] == "技术方案"

    with session_scope() as db:
        node = db.scalars(
            __import__("sqlalchemy").select(OutlineNode).where(OutlineNode.project_id == pid)
        ).first()
        node.heading = "已改名的章节"

    second = owner.get(f"/projects/{pid}/requirements").json()
    assert second[0]["section_heading"] == "已改名的章节"


def test_requirement_response_update_reuses_request_context(owner, monkeypatch):
    """R05：PATCH 确认时判定与返回 payload 共享一次上下文加载。"""

    import importlib

    shared_mod = importlib.import_module("bidcraft.api.shared")
    real_loader = shared_mod.load_outline
    calls = {"n": 0}

    def counting_loader(db, project_id):
        calls["n"] += 1
        return real_loader(db, project_id)

    monkeypatch.setattr(shared_mod, "load_outline", counting_loader)

    pid = _project(owner)
    req_id = _seed_linked(pid)
    resp = owner.patch(
        f"/projects/{pid}/requirements/{req_id}",
        json={"response_status": "responded", "note": "已人工复核确认"},
    )
    assert resp.status_code == 200, resp.text
    # 判定一次 + 返回 payload 共享同一 context，整次请求只加载一次大纲。
    assert calls["n"] == 1, calls
