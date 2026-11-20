"""实质性条款（★/▲）→ 正文响应的绑定：解析保留标记、覆盖判定、人工确认与门禁。

判定刻意保守：关键词覆盖不到就当作未响应，人工再标「已响应 / 不适用」。
因此这里重点验证「不会漏报」和「人工确认后不会再报」。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "sub-pass-1"

SECTION_TEXT = """第三章 评标办法
★ 投标人须提供近三年同类项目业绩不少于3项
▲ 项目经理须具备一级建造师执业资格
（1）投标文件应包含完整的施工组织设计
"""


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_project(client, name: str = "实质性条款项目", package: str = "weakcurrent-cabling") -> str:
    response = client.post("/projects", json={"name": name, "package": package})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture()
def actors(db):
    admin = _login(*ADMIN)
    for username in ("sub-owner", "sub-viewer"):
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
    return {"admin": admin, "owner": _login("sub-owner"), "viewer": _login("sub-viewer")}


def _seed_requirement(
    project_id: str,
    *,
    text: str,
    marker: str = "",
    mandatory: bool = True,
    source_quote: str = "",
    status: str = "pending",
) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import Requirement

    with session_scope() as session:
        row = Requirement(
            project_id=project_id,
            kind="substantive",
            text=text,
            mandatory=mandatory,
            source_quote=source_quote or text,
            marker=marker,
            response_status=status,
        )
        session.add(row)
        session.flush()
        return row.id


def _seed_section(project_id: str, heading: str, body: str, position: int = 1) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading=heading, position=position)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body=body, status="written", words=len(body)))
        session.flush()
        return node.id


# --------------------------------------------------------------- 解析层


def test_extract_keeps_marker_and_source_line():
    from bidcraft.parsing.extract import extract_requirements_and_risks
    from bidcraft.rules.loader import load_package

    package = load_package("weakcurrent-cabling")
    requirements, _risks = extract_requirements_and_risks(SECTION_TEXT, package)

    starred = [item for item in requirements if item.marker == "★"]
    triangled = [item for item in requirements if item.marker == "▲"]
    assert len(starred) == 1 and len(triangled) == 1
    assert starred[0].mandatory is True
    # source_quote 必须是命中的原文行，而不是章节标题
    assert starred[0].source_quote.startswith("★ 投标人须提供近三年同类项目业绩")
    assert "第三章" not in starred[0].source_quote
    # 无标记条目仍然保留（mandatory 语义不变）
    plain = [item for item in requirements if item.marker == "" and "施工组织设计" in item.text]
    assert plain and plain[0].mandatory is False


def test_requirement_migration_is_idempotent(db):
    from bidcraft.db import get_engine, init_db

    init_db()
    init_db()
    from sqlalchemy import inspect

    columns = {column["name"] for column in inspect(get_engine()).get_columns("requirements")}
    assert {
        "marker",
        "response_status",
        "response_note",
        "responded_by",
        "responded_at",
        "section_id",
        "node_id",
        "auto_covered",
    } <= columns


# --------------------------------------------------------------- 判定层


def test_requirement_issues_levels_and_manual_confirmation():
    from bidcraft.substantive import requirement_issues

    nodes = [{"id": "n1", "position": 1, "heading": "技术方案", "body": "本章只写了进度安排。"}]
    requirements = [
        SimpleNamespace(
            id="r1",
            text="★ 投标人须提供近三年同类项目业绩不少于3项",
            marker="★",
            mandatory=True,
            source_quote="★ …",
            response_status="pending",
        ),
        SimpleNamespace(
            id="r2",
            text="▲ 项目经理须具备一级建造师执业资格",
            marker="▲",
            mandatory=True,
            source_quote="▲ …",
            response_status="pending",
        ),
        SimpleNamespace(
            id="r3",
            text="投标人必须提供安全生产许可证",
            marker="",
            mandatory=True,
            source_quote="…",
            response_status="pending",
        ),
        SimpleNamespace(
            id="r4",
            text="★ 投标人须提供近三年同类项目业绩不少于3项",
            marker="★",
            mandatory=True,
            source_quote="★ …",
            response_status="responded",
        ),
        SimpleNamespace(
            id="r5",
            text="▲ 项目经理须具备一级建造师执业资格",
            marker="▲",
            mandatory=True,
            source_quote="▲ …",
            response_status="na",
        ),
    ]
    issues = requirement_issues(requirements, nodes)
    levels = {issue.finding: issue.level for issue in issues}
    assert levels["★ 投标人须提供近三年同类项目业绩不少于3项"] == "blocking"
    assert levels["▲ 项目经理须具备一级建造师执业资格"] == "blocking"
    assert levels["投标人必须提供安全生产许可证"] == "critical"
    # 人工确认过的条款不再产出问题（r5 与 r2 同文本，但 r5 已标不适用）；
    # 编号按「★ → ▲ → 仅 mandatory」的稳定顺序分配，跳过已确认项不影响其它编号。
    assert len(issues) == 3
    assert [issue.code for issue in issues] == ["SUB-001", "SUB-003", "SUB-005"]
    assert [issue.code for issue in requirement_issues(requirements, nodes)] == [
        "SUB-001",
        "SUB-003",
        "SUB-005",
    ]


def test_find_covering_section_picks_first_matching_position():
    from bidcraft.substantive import find_covering_section

    text = "★ 投标人须提供近三年同类项目业绩不少于3项并附合同复印件"
    nodes = [
        {"id": "late", "position": 3, "heading": "售后服务", "body": "近三年同类项目业绩 3 项，附合同复印件。"},
        {"id": "early", "position": 2, "heading": "商务响应", "body": "提供近三年同类项目业绩不少于3项，并附合同复印件。"},
    ]
    assert find_covering_section(text, nodes)["id"] == "early"
    assert find_covering_section(text, [{"id": "x", "position": 1, "heading": "封面", "body": "投标文件"}]) is None


# --------------------------------------------------------------- 接口层


def test_requirements_list_and_viewer_cannot_confirm(actors):
    project_id = _make_project(actors["owner"])
    requirement_id = _seed_requirement(project_id, text="★ 须提供业绩证明", marker="★")
    actors["owner"].post(
        f"/projects/{project_id}/members", json={"username": "sub-viewer", "role": "viewer"}
    )

    listing = actors["viewer"].get(f"/projects/{project_id}/requirements")
    assert listing.status_code == 200, listing.text
    items = listing.json()
    assert [item["id"] for item in items] == [requirement_id]
    assert items[0]["marker"] == "★"
    assert items[0]["response_status"] == "pending"

    denied = actors["viewer"].patch(
        f"/projects/{project_id}/requirements/{requirement_id}",
        json={"response_status": "responded"},
    )
    assert denied.status_code == 403


def test_na_requires_reason_and_is_audited(actors):
    project_id = _make_project(actors["owner"])
    requirement_id = _seed_requirement(project_id, text="▲ 项目经理须具备一级建造师", marker="▲")

    short = actors["owner"].patch(
        f"/projects/{project_id}/requirements/{requirement_id}",
        json={"response_status": "na", "note": "无"},
    )
    assert short.status_code == 400
    assert "不适用" in short.json()["detail"]

    ok = actors["owner"].patch(
        f"/projects/{project_id}/requirements/{requirement_id}",
        json={"response_status": "na", "note": "本次为设备采购，不涉及项目经理执业资格。"},
    )
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["response_status"] == "na"
    assert payload["responded_by"] == "sub-owner"
    assert payload["responded_at"].endswith("+08:00")

    audit = actors["owner"].get(f"/projects/{project_id}/audit?limit=50").json()["items"]
    actions = [item["action"] for item in audit]
    assert "requirement.response" in actions


def test_pending_clears_confirmation_and_cross_project_404(actors):
    project_id = _make_project(actors["owner"], name="A 项目")
    other_id = _make_project(actors["owner"], name="B 项目")
    requirement_id = _seed_requirement(project_id, text="★ 须提供业绩证明", marker="★")

    actors["owner"].patch(
        f"/projects/{project_id}/requirements/{requirement_id}",
        json={"response_status": "responded", "note": "第 5 章已响应"},
    )
    cleared = actors["owner"].patch(
        f"/projects/{project_id}/requirements/{requirement_id}",
        json={"response_status": "pending"},
    )
    assert cleared.status_code == 200
    payload = cleared.json()
    assert payload["response_status"] == "pending"
    assert payload["response_note"] == ""
    assert payload["responded_by"] == ""
    assert payload["responded_at"] is None

    wrong = actors["owner"].patch(
        f"/projects/{other_id}/requirements/{requirement_id}",
        json={"response_status": "responded"},
    )
    assert wrong.status_code == 404


# --------------------------------------------------------------- 核对与门禁


def test_check_creates_blocking_issue_and_manual_confirmation_silences_it(actors):
    from bidcraft.db import session_scope
    from bidcraft.models import Issue, Requirement
    from bidcraft.pipeline import check_project

    project_id = _make_project(actors["owner"])
    requirement_id = _seed_requirement(
        project_id,
        text="★ 投标人须提供近三年同类项目业绩不少于3项并附合同复印件",
        marker="★",
        source_quote="★ 投标人须提供近三年同类项目业绩不少于3项并附合同复印件",
    )
    _seed_section(project_id, "技术方案", "本章描述施工工艺与质量控制措施。")

    with session_scope() as session:
        check_project(session, project_id)
        session.flush()
        issues = [row for row in session.query(Issue).all() if row.code.startswith("SUB-")]
        assert len(issues) == 1
        assert issues[0].level == "blocking"
        assert issues[0].category == "实质性响应"
        assert issues[0].location.startswith("★ 投标人须提供")
        requirement = session.get(Requirement, requirement_id)
        assert requirement.auto_covered is False

    # 重复核对：finding 不变 → 不重开、不新增
    with session_scope() as session:
        check_project(session, project_id)
        session.flush()
        again = [row for row in session.query(Issue).all() if row.code.startswith("SUB-")]
        assert len(again) == 1

    # 人工确认「已响应」后，再核对不再产出该问题
    actors["owner"].patch(
        f"/projects/{project_id}/requirements/{requirement_id}",
        json={"response_status": "responded", "note": "第 5 章 5.2 已逐条响应"},
    )
    with session_scope() as session:
        check_project(session, project_id)
        session.flush()
        assert [row for row in session.query(Issue).all() if row.code.startswith("SUB-")] == []


def test_covered_requirement_is_marked_and_not_an_issue(actors):
    from bidcraft.db import session_scope
    from bidcraft.models import Issue, Requirement
    from bidcraft.pipeline import check_project

    project_id = _make_project(actors["owner"])
    requirement_id = _seed_requirement(
        project_id,
        text="★ 投标人须提供近三年同类项目业绩不少于3项",
        marker="★",
    )
    _seed_section(
        project_id,
        "商务响应",
        "我方提供近三年同类项目业绩不少于3项，合同复印件见附件。",
    )

    with session_scope() as session:
        check_project(session, project_id)
        session.flush()
        assert [row for row in session.query(Issue).all() if row.code.startswith("SUB-")] == []
        requirement = session.get(Requirement, requirement_id)
        assert requirement.auto_covered is True
        assert requirement.node_id

    listing = actors["owner"].get(f"/projects/{project_id}/requirements").json()
    assert listing[0]["auto_covered"] is True
    assert listing[0]["section_heading"] == "商务响应"


def test_uncovered_star_requirement_blocks_export(actors):
    project_id = _make_project(actors["owner"])
    _seed_requirement(
        project_id,
        text="★ 投标人须提供近三年同类项目业绩不少于3项",
        marker="★",
    )
    _seed_section(project_id, "技术方案", "本章只写了进度安排与人员分工。")

    checked = actors["owner"].post(f"/projects/{project_id}/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["verdict"] == "blocked"

    blocked = actors["owner"].post(f"/projects/{project_id}/export", json={"format": "docx"})
    assert blocked.status_code == 409, blocked.text
    assert "未处置" in blocked.json()["detail"]

    # xlsx 台账导出不受交付门禁影响
    xlsx = actors["owner"].post(f"/projects/{project_id}/export", json={"format": "xlsx"})
    assert xlsx.status_code == 200
