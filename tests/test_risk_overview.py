"""交付前风险总览：四档口径、人工处置、重复核对保留结论、导出硬门禁。"""

from __future__ import annotations

import datetime as dt
import json
import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "risk-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_user(admin, username: str) -> dict:
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
    return response.json()


def _make_project(client, name: str = "风险总览项目", package: str = "weakcurrent-cabling") -> str:
    response = client.post("/projects", json={"name": name, "package": package})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _add_member(client, project_id: str, username: str, role: str):
    response = client.post(
        f"/projects/{project_id}/members", json={"username": username, "role": role}
    )
    assert response.status_code == 201, response.text


@pytest.fixture()
def team(db):
    admin = _login(*ADMIN)
    for username in ("risk-owner", "risk-editor", "risk-viewer", "risk-outsider"):
        _make_user(admin, username)
    return {
        "admin": admin,
        "owner": _login("risk-owner"),
        "editor": _login("risk-editor"),
        "viewer": _login("risk-viewer"),
        "outsider": _login("risk-outsider"),
    }


def _add_issue(
    project_id: str,
    *,
    code: str,
    level: str = "major",
    status: str = "open",
    category: str = "测试分类",
    location: str = "",
    finding: str | None = None,
    checked_at: dt.datetime | None = None,
) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import Issue

    with session_scope() as session:
        row = Issue(
            project_id=project_id,
            code=code,
            level=level,
            status=status,
            category=category,
            location=location,
            finding=finding if finding is not None else f"{code} 的发现",
            checked_at=checked_at,
        )
        session.add(row)
        session.flush()
        return row.id


def _overview(client, project_id: str) -> dict:
    response = client.get(f"/projects/{project_id}/risk-overview")
    assert response.status_code == 200, response.text
    return response.json()


def _issues(client, project_id: str) -> list[dict]:
    response = client.get(f"/projects/{project_id}/issues")
    assert response.status_code == 200, response.text
    return response.json()


def _audit_items(client, project_id: str) -> list[dict]:
    response = client.get(f"/projects/{project_id}/audit?limit=200")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_overview_tiers_merge_minor_info_and_blocked_verdict(team):
    project_id = _make_project(team["owner"])
    _add_issue(project_id, code="B1", level="blocking", category="数量一致",
               checked_at=dt.datetime(2026, 11, 20, 1, 0))
    _add_issue(project_id, code="B2", level="blocking", category="数量一致",
               checked_at=dt.datetime(2026, 11, 20, 2, 0))
    _add_issue(project_id, code="B3", level="blocking", status="handled", category="项目身份")
    _add_issue(project_id, code="C1", level="critical", category="资格")
    _add_issue(project_id, code="M1", level="major", status="ignored", category="商务")
    _add_issue(project_id, code="N1", level="minor", category="一致性")
    _add_issue(project_id, code="I1", level="info", category="一致性")

    payload = _overview(team["owner"], project_id)
    assert payload["project_id"] == project_id
    assert payload["verdict"] == "blocked"
    assert payload["gate"] == {
        "blocked": True,
        "blocking_open": 2,
        "reason": "存在 2 项未处置的废标/阻断风险",
    }
    # checked_at 取最大值，naive UTC 02:00 → 北京 10:00（+08:00）
    assert payload["checked_at"] == "2026-11-20T10:00:00+08:00"
    assert payload["tiers"] == [
        {"level": "blocking", "label": "废标/阻断", "total": 3, "open": 2, "handled": 1, "ignored": 0},
        {"level": "critical", "label": "严重", "total": 1, "open": 1, "handled": 0, "ignored": 0},
        {"level": "major", "label": "主要", "total": 1, "open": 0, "handled": 0, "ignored": 1},
        {"level": "minor", "label": "提示", "total": 2, "open": 2, "handled": 0, "ignored": 0},
    ]
    by_category = {entry["category"]: entry for entry in payload["by_category"]}
    assert by_category == {
        "数量一致": {"category": "数量一致", "total": 2, "open": 2, "blocking_open": 2},
        "项目身份": {"category": "项目身份", "total": 1, "open": 0, "blocking_open": 0},
        "资格": {"category": "资格", "total": 1, "open": 1, "blocking_open": 0},
        "商务": {"category": "商务", "total": 1, "open": 0, "blocking_open": 0},
        "一致性": {"category": "一致性", "total": 2, "open": 2, "blocking_open": 0},
    }
    # items 按 SEVERITY_ORDER，再按 open 优先；minor/info 保留原始 level
    levels = [item["level"] for item in payload["items"]]
    assert levels == ["blocking", "blocking", "blocking", "critical", "major", "minor", "info"]
    assert [item["code"] for item in payload["items"][:3]] == ["B1", "B2", "B3"]
    for item in payload["items"]:
        assert {"node_id", "section_id", "heading", "position", "checked_at",
                "resolved_at", "resolved_by", "resolution_note"} <= set(item)

    # viewer 可读同一份总览
    _add_member(team["owner"], project_id, "risk-viewer", "viewer")
    assert _overview(team["viewer"], project_id)["verdict"] == "blocked"


def test_overview_verdict_warn_pass_and_empty(team):
    owner = team["owner"]
    warn_id = _make_project(owner, "警告项目")
    _add_issue(warn_id, code="B1", level="blocking", status="handled")
    _add_issue(warn_id, code="C1", level="critical")
    warn = _overview(owner, warn_id)
    assert warn["verdict"] == "warn"
    assert warn["gate"] == {"blocked": False, "blocking_open": 0, "reason": ""}

    # 历史状态 resolved/closed 读侧兼容：视为已处置（计入 handled 桶）
    pass_id = _make_project(owner, "通过项目")
    _add_issue(pass_id, code="B1", level="blocking", status="resolved")
    _add_issue(pass_id, code="M1", level="major", status="closed")
    _add_issue(pass_id, code="N1", level="minor", status="ignored")
    _add_issue(pass_id, code="I1", level="info", status="handled")
    passed = _overview(owner, pass_id)
    assert passed["verdict"] == "pass"
    assert passed["tiers"][0] == {
        "level": "blocking", "label": "废标/阻断", "total": 1, "open": 0, "handled": 1, "ignored": 0,
    }
    assert passed["tiers"][2]["handled"] == 1
    assert passed["tiers"][3] == {
        "level": "minor", "label": "提示", "total": 2, "open": 0, "handled": 1, "ignored": 1,
    }

    empty_id = _make_project(owner, "空项目")
    empty = _overview(owner, empty_id)
    assert empty["verdict"] == "pass"
    assert empty["checked_at"] is None
    assert empty["gate"]["blocked"] is False
    assert empty["items"] == []
    assert empty["by_category"] == []
    assert all(tier["total"] == 0 for tier in empty["tiers"])


def test_viewer_reads_but_cannot_dispose(team):
    owner = team["owner"]
    project_id = _make_project(owner)
    issue_id = _add_issue(project_id, code="B1", level="blocking")
    _add_member(owner, project_id, "risk-viewer", "viewer")
    _add_member(owner, project_id, "risk-editor", "editor")

    assert _overview(team["viewer"], project_id)["verdict"] == "blocked"
    blocked = team["viewer"].patch(
        f"/projects/{project_id}/issues/{issue_id}",
        json={"status": "handled", "note": "复核"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "只读成员不能修改该项目"
    assert team["viewer"].post(
        f"/projects/{project_id}/issues/bulk-status",
        json={"ids": [issue_id], "status": "handled"},
    ).status_code == 403

    # 无成员资格者连总览都看不到（404，不泄漏项目存在）
    assert team["outsider"].get(f"/projects/{project_id}/risk-overview").status_code == 404
    assert team["outsider"].patch(
        f"/projects/{project_id}/issues/{issue_id}", json={"status": "handled"}
    ).status_code == 404


def test_editor_disposition_fields_and_audit(team):
    owner = team["owner"]
    project_id = _make_project(owner)
    issue_id = _add_issue(project_id, code="B1", level="blocking")
    _add_member(owner, project_id, "risk-editor", "editor")
    url = f"/projects/{project_id}/issues/{issue_id}"

    handled = team["editor"].patch(url, json={"status": "handled", "note": "已人工复核"})
    assert handled.status_code == 200, handled.text
    body = handled.json()
    assert body["status"] == "handled"
    assert body["resolved_by"] == "risk-editor"
    assert body["resolved_at"] is not None and body["resolved_at"].endswith("+08:00")
    assert body["resolution_note"] == "已人工复核"

    # ignored 必须填原因（去空白后非空）
    assert team["editor"].patch(url, json={"status": "ignored"}).status_code == 400
    assert team["editor"].patch(url, json={"status": "ignored", "note": "   "}).status_code == 400
    ignored = team["editor"].patch(url, json={"status": "ignored", "note": " 业主同意偏离 "})
    assert ignored.status_code == 200
    assert ignored.json()["status"] == "ignored"
    assert ignored.json()["resolution_note"] == "业主同意偏离"

    # 重新打开清空处置三字段
    reopened = team["editor"].patch(url, json={"status": "open"})
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["resolved_at"] is None
    assert reopened.json()["resolved_by"] == ""
    assert reopened.json()["resolution_note"] == ""

    trail = [item for item in _audit_items(owner, project_id) if item["action"] == "issue.status"]
    assert len(trail) == 3
    assert trail[0]["username"] == "risk-editor"
    assert trail[0]["detail"] == {
        "issue_id": issue_id,
        "code": "B1",
        "level": "blocking",
        "from": "ignored",
        "to": "open",
        "note": "",
    }
    assert {item["detail"]["to"] for item in trail} == {"handled", "ignored", "open"}


def test_cross_project_issue_disposition_404(team):
    owner = team["owner"]
    project_a = _make_project(owner, "项目甲")
    project_b = _make_project(owner, "项目乙")
    issue_b = _add_issue(project_b, code="B1", level="blocking")
    _add_member(owner, project_a, "risk-editor", "editor")

    assert team["editor"].patch(
        f"/projects/{project_a}/issues/{issue_b}", json={"status": "handled"}
    ).status_code == 404
    assert team["editor"].patch(
        f"/projects/{project_a}/issues/ghost-id", json={"status": "handled"}
    ).status_code == 404
    # 批量处置同样忽略跨项目 id
    result = team["editor"].post(
        f"/projects/{project_a}/issues/bulk-status",
        json={"ids": [issue_b], "status": "handled"},
    )
    assert result.status_code == 200
    assert result.json() == {"updated": 0, "items": []}


def test_bulk_status_dedup_and_limit(team):
    owner = team["owner"]
    project_id = _make_project(owner)
    other_id = _make_project(owner, "另一个项目")
    ids = [_add_issue(project_id, code=f"B{i}", level="blocking") for i in range(3)]
    foreign = _add_issue(other_id, code="X1", level="blocking")
    _add_member(owner, project_id, "risk-editor", "editor")
    url = f"/projects/{project_id}/issues/bulk-status"

    result = team["editor"].post(
        url,
        json={"ids": [ids[0], ids[0], ids[1], foreign, "ghost"], "status": "handled", "note": "批量复核"},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["updated"] == 2
    assert {item["id"] for item in body["items"]} == {ids[0], ids[1]}
    assert all(item["resolved_by"] == "risk-editor" for item in body["items"])
    # 跨项目 id 未被改动
    assert _overview(owner, other_id)["gate"]["blocking_open"] == 1

    trail = [item for item in _audit_items(owner, project_id) if item["action"] == "issue.bulk_status"]
    assert len(trail) == 1
    assert trail[0]["detail"]["status"] == "handled"
    assert trail[0]["detail"]["ids"] == 4
    assert trail[0]["detail"]["updated"] == 2

    assert team["editor"].post(url, json={"ids": ids, "status": "ignored"}).status_code == 400
    too_many = team["editor"].post(
        url, json={"ids": [f"id-{i}" for i in range(201)], "status": "handled"}
    )
    assert too_many.status_code == 400

    cleared = team["editor"].post(url, json={"ids": ids, "status": "open"})
    assert cleared.status_code == 200
    assert cleared.json()["updated"] == 3
    assert all(item["resolved_at"] is None for item in cleared.json()["items"])


ANCHOR_PACKAGE = """\
package: risk-anchor
version: 1
title: 风险锚定测试包
description: 测试锚定与处置保留
project_type: fire
project_type_label: 消防工程
extends: [common-bid-facts]
outline:
  items:
    - heading: 项目概述
      intent: 概述项目范围
    - heading: 技术方案
      intent: 技术方案
checks:
  - id: RISK-001
    level: blocking
    category: 一致性
    assert: "not contains(doc, facts['project.name'])"
    message: 正文未出现项目名称（锚定测试）
    recommendation: 在正文中使用台账项目名称
  - id: RISK-002
    level: minor
    category: 综合
    assert: "False"
    message: 全局提示项（锚定测试）
"""


def test_check_anchors_and_preserves_manual_disposition(team, tender_path):
    owner = team["owner"]
    assert team["admin"].post(
        "/admin/rule-packages", json={"document": ANCHOR_PACKAGE}
    ).status_code == 201
    project_id = _make_project(owner, "锚定项目", package="risk-anchor")
    with tender_path.open("rb") as handle:
        uploaded = owner.post(
            f"/projects/{project_id}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert uploaded.status_code == 200, uploaded.text
    ledger = owner.get(f"/projects/{project_id}/ledger").json()
    project_name = next(f["value"] for f in ledger["facts"] if f["key"] == "project.name")
    outline = owner.post(f"/projects/{project_id}/outline", json={"mode": "package"})
    assert outline.status_code == 200, outline.text
    chapters = owner.get(f"/projects/{project_id}/outline").json()["chapters"]
    assert [chapter["heading"] for chapter in chapters] == ["项目概述", "技术方案"]
    saved = owner.put(
        f"/projects/{project_id}/chapters/{chapters[1]['id']}",
        json={"body": f"## 技术方案\n\n本方案针对{project_name}编制。"},
    )
    assert saved.status_code == 200, saved.text

    checked = owner.post(f"/projects/{project_id}/check")
    assert checked.status_code == 200, checked.text
    by_code = {item["code"]: item for item in checked.json()["issues"]}
    assert by_code["RISK-001"]["node_id"] == chapters[1]["id"]

    issues = {item["code"]: item for item in _issues(owner, project_id)}
    anchored = issues["RISK-001"]
    assert anchored["node_id"] == chapters[1]["id"]
    assert anchored["section_id"] == chapters[1]["section_id"]
    assert anchored["heading"] == "技术方案"
    assert anchored["position"] == 1
    assert anchored["status"] == "open"
    assert anchored["checked_at"] is not None and anchored["checked_at"].endswith("+08:00")
    # location 匹配不到大纲时留空，不瞎猜
    unanchored = issues["RISK-002"]
    assert unanchored["node_id"] is None
    assert unanchored["section_id"] is None
    assert unanchored["heading"] == ""
    assert unanchored["position"] is None

    _add_member(owner, project_id, "risk-editor", "editor")
    handled = team["editor"].patch(
        f"/projects/{project_id}/issues/{anchored['id']}",
        json={"status": "handled", "note": "已人工复核"},
    )
    assert handled.status_code == 200, handled.text

    # 重复核对：finding 不变 → 人工结论保留
    assert owner.post(f"/projects/{project_id}/check").status_code == 200
    again = {item["code"]: item for item in _issues(owner, project_id)}["RISK-001"]
    assert again["status"] == "handled"
    assert again["resolved_by"] == "risk-editor"
    assert again["resolution_note"] == "已人工复核"
    assert again["resolved_at"] is not None
    assert again["node_id"] == chapters[1]["id"]

    # finding 变化 → 回到 open（模拟上一轮 finding 与本轮不同）
    from bidcraft.db import session_scope
    from bidcraft.models import Issue

    with session_scope() as session:
        row = session.get(Issue, again["id"])
        row.finding = "旧版本的不同发现描述"
    assert owner.post(f"/projects/{project_id}/check").status_code == 200
    reopened = {item["code"]: item for item in _issues(owner, project_id)}["RISK-001"]
    assert reopened["status"] == "open"
    assert reopened["resolved_at"] is None
    assert reopened["resolved_by"] == ""
    assert reopened["resolution_note"] == ""


def _prepared_project(client, tender_path, name: str = "门禁项目") -> str:
    project_id = _make_project(client, name)
    with tender_path.open("rb") as handle:
        uploaded = client.post(
            f"/projects/{project_id}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert uploaded.status_code == 200, uploaded.text
    assert client.post(f"/projects/{project_id}/outline", json={"mode": "package"}).status_code == 200
    written = client.post(f"/projects/{project_id}/write")
    assert written.status_code == 200, written.text
    assert written.json()["failed"] == []
    # 章节确认是独立硬检查点；本文件只关心风险门禁，这里直接置为已确认
    from helpers import confirm_project_chapters

    confirm_project_chapters(project_id)
    return project_id


def test_export_hard_gate_and_forced_override(team, tender_path):
    owner = team["owner"]
    project_id = _prepared_project(owner, tender_path)
    issue_id = _add_issue(project_id, code="GATE-001", level="blocking")
    _add_member(owner, project_id, "risk-editor", "editor")
    url = f"/projects/{project_id}/export"
    gate_detail = "存在 1 项未处置的废标/阻断风险，请先在风险总览中处置；确需导出请强制导出并填写原因"

    for fmt in ("docx", "md", "markdown", "pdf"):
        blocked = owner.post(url, json={"format": fmt})
        assert blocked.status_code == 409, (fmt, blocked.text)
        assert blocked.json()["detail"] == gate_detail

    # editor 不能强制导出
    assert team["editor"].post(
        url, json={"format": "docx", "force": True, "force_reason": "业主确认风险可控"}
    ).status_code == 403
    # 原因太短（去空白后 < 4 字）
    assert owner.post(
        url, json={"format": "docx", "force": True, "force_reason": " 短 "}
    ).status_code == 400
    assert owner.post(url, json={"format": "docx", "force": True}).status_code == 400

    forced = owner.post(
        url, json={"format": "docx", "force": True, "force_reason": "  业主确认风险可控  "}
    )
    assert forced.status_code == 200, forced.text
    assert forced.content[:2] == b"PK"
    trail = [item for item in _audit_items(owner, project_id) if item["action"] == "export.force"]
    assert len(trail) == 1
    assert trail[0]["detail"] == {
        "format": "docx",
        "blocking_open": 1,
        "reason": "业主确认风险可控",
    }

    # xlsx 不受门禁约束
    ledger = owner.post(url, json={"format": "xlsx"})
    assert ledger.status_code == 200, ledger.text
    assert ledger.content[:2] == b"PK"

    # 处置后无需 force 即可导出；无 blocking 时 force 参数被忽略
    assert team["editor"].patch(
        f"/projects/{project_id}/issues/{issue_id}",
        json={"status": "handled", "note": "已复核"},
    ).status_code == 200
    normal = owner.post(url, json={"format": "docx"})
    assert normal.status_code == 200, normal.text
    assert owner.post(
        url, json={"format": "docx", "force": True, "force_reason": ""}
    ).status_code == 200


def test_export_without_blocking_is_unaffected(team, tender_path):
    owner = team["owner"]
    project_id = _prepared_project(owner, tender_path, "无阻断项目")
    # 仅有 minor/critical 未处置不拦导出
    _add_issue(project_id, code="N1", level="minor")
    _add_issue(project_id, code="C1", level="critical")
    # 历史状态 resolved 的 blocking 视为已处置
    _add_issue(project_id, code="B1", level="blocking", status="resolved")
    exported = owner.post(f"/projects/{project_id}/export", json={"format": "docx"})
    assert exported.status_code == 200, exported.text
    assert exported.content[:2] == b"PK"
    overview = _overview(owner, project_id)
    assert overview["verdict"] == "warn"
    assert overview["gate"]["blocked"] is False


# ------------------------------------------------- 导出硬门禁：MCP / CLI 旁路

GATE_DETAIL = "存在 1 项未处置的废标/阻断风险，请先在风险总览中处置；确需导出请强制导出并填写原因"
FORCE_REASON_DETAIL = "强制导出必须填写不少于 4 个字的原因"


def _project_with_body(name: str) -> str:
    """建一个带正文的最小项目（MCP/CLI 测试无需走完整 HTTP 流水线）。"""

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section
    from bidcraft.pipeline import create_project

    with session_scope() as session:
        project = create_project(session, name)
        node = OutlineNode(project_id=project.id, heading="总体方案", position=1)
        session.add(node)
        session.flush()
        session.add(
            Section(
                node_id=node.id,
                body="本方案采用 Cat6A 综合布线系统，满足招标要求。",
                status="written",
                words=20,
                review_status="confirmed",
            )
        )
        return project.id


def _force_audits(project_id: str | None = None) -> list[tuple[str, dict]]:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as session:
        statement = select(AuditLog).where(AuditLog.action == "export.force")
        if project_id is not None:
            statement = statement.where(AuditLog.project_id == project_id)
        rows = list(session.scalars(statement.order_by(AuditLog.id)))
        return [(row.username, row.detail) for row in rows]


def _set_issue_status(issue_id: str, status: str) -> None:
    from bidcraft.db import session_scope
    from bidcraft.models import Issue

    with session_scope() as session:
        session.get(Issue, issue_id).status = status


def _mcp_call(name: str, arguments: dict, *, readonly: bool = False):
    from bidcraft.mcp import handle_message

    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        readonly=readonly,
    )
    result = response["result"]
    return result, result["content"][0]["text"]


def _cli_export(*args: str):
    from typer.testing import CliRunner

    from bidcraft.cli import app

    return CliRunner().invoke(app, ["export", *args])


def _squash(text: str) -> str:
    """rich 会按 80 列折行，比较文案前先去掉全部空白。"""

    return "".join(text.split())


def test_mcp_export_gate_and_forced_override(db):
    from bidcraft.mcp import TOOL_SPECS

    project_id = _project_with_body("MCP 门禁项目")
    issue_id = _add_issue(project_id, code="MCP-GATE-1", level="blocking")

    spec = next(item for item in TOOL_SPECS if item["name"] == "export_document")
    properties = spec["inputSchema"]["properties"]
    assert properties["force"]["type"] == "boolean"
    assert properties["force_reason"]["type"] == "string"

    for fmt in ("docx", "md"):
        result, text = _mcp_call("export_document", {"ref": project_id, "format": fmt})
        assert result["isError"] is True
        assert text == GATE_DETAIL

    for arguments in (
        {"ref": project_id, "format": "docx", "force": True, "force_reason": " 短 "},
        {"ref": project_id, "format": "docx", "force": True},
    ):
        result, text = _mcp_call("export_document", arguments)
        assert result["isError"] is True
        assert text == FORCE_REASON_DETAIL
    assert _force_audits(project_id) == []

    result, text = _mcp_call(
        "export_document",
        {
            "ref": project_id,
            "format": "docx",
            "force": True,
            "force_reason": "  业主确认风险可控  ",
        },
    )
    assert result["isError"] is False, text
    assert Path(json.loads(text)["path"]).read_bytes()[:2] == b"PK"
    assert _force_audits(project_id) == [
        (
            "system",
            {
                "format": "docx",
                "blocking_open": 1,
                "reason": "业主确认风险可控",
                "source": "mcp",
            },
        )
    ]

    # xlsx 不受门禁约束
    result, text = _mcp_call("export_document", {"ref": project_id, "format": "xlsx"})
    assert result["isError"] is False, text

    # 处置后无需 force 即可导出
    _set_issue_status(issue_id, "handled")
    result, text = _mcp_call("export_document", {"ref": project_id, "format": "md"})
    assert result["isError"] is False, text


def test_cli_export_gate_and_forced_override(db):
    project_id = _project_with_body("CLI 门禁项目")
    issue_id = _add_issue(project_id, code="CLI-GATE-1", level="blocking")

    blocked = _cli_export(project_id, "--format", "docx")
    assert blocked.exit_code == 1
    assert _squash(GATE_DETAIL) in _squash(blocked.output)

    blocked_md = _cli_export(project_id, "--format", "md")
    assert blocked_md.exit_code == 1
    assert _squash(GATE_DETAIL) in _squash(blocked_md.output)

    short = _cli_export(project_id, "--format", "docx", "--force", "--force-reason", " 短 ")
    assert short.exit_code == 1
    assert _squash(FORCE_REASON_DETAIL) in _squash(short.output)

    missing = _cli_export(project_id, "--format", "docx", "--force")
    assert missing.exit_code == 1
    assert _squash(FORCE_REASON_DETAIL) in _squash(missing.output)
    assert _force_audits(project_id) == []

    forced = _cli_export(
        project_id, "--format", "docx", "--force", "--force-reason", "  业主确认风险可控  "
    )
    assert forced.exit_code == 0, forced.output
    assert _force_audits(project_id) == [
        (
            "system",
            {
                "format": "docx",
                "blocking_open": 1,
                "reason": "业主确认风险可控",
                "source": "cli",
            },
        )
    ]

    # xlsx 不受门禁约束
    ledger = _cli_export(project_id, "--format", "xlsx")
    assert ledger.exit_code == 0, ledger.output

    # 处置后无需 force 即可导出
    _set_issue_status(issue_id, "handled")
    normal = _cli_export(project_id, "--format", "md")
    assert normal.exit_code == 0, normal.output


CLI_GATE_PACKAGE = """\
package: cli-gate
version: 1
title: CLI 门禁测试包
description: 始终产生一条 blocking 风险
project_type: fire
project_type_label: 消防工程
extends: [common-bid-facts]
outline:
  items:
    - heading: 项目概述
      intent: 概述项目范围
checks:
  - id: CLI-GATE-001
    level: blocking
    category: 一致性
    assert: "False"
    message: 强制阻断（CLI run 测试）
"""


def test_cli_run_export_gate_and_forced_override(db, tender_path):
    """`bidcraft run` 末尾的 DOCX 导出也是真实交付路径，必须同门禁。"""

    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.rules import store as package_store

    package_store.save_user_package("cli-gate", CLI_GATE_PACKAGE)
    runner = CliRunner()
    base = ["run", str(tender_path), "--package", "cli-gate"]

    blocked = runner.invoke(app, [*base, "--name", "CLI 跑通门禁"])
    assert blocked.exit_code == 1, blocked.output
    assert "未处置的废标/阻断风险" in blocked.output
    assert _force_audits() == []

    forced = runner.invoke(
        app,
        [
            *base,
            "--name",
            "CLI 跑通强制",
            "--force",
            "--force-reason",
            "业主确认风险可控",
        ],
    )
    assert forced.exit_code == 0, forced.output
    trail = _force_audits()
    assert len(trail) == 1
    assert trail[0][1] == {
        "format": "docx",
        "blocking_open": 1,
        "reason": "业主确认风险可控",
        "source": "cli",
    }


# ------------------------------------------- 批量导出（后台任务）同样过门禁


def _run_batch_export(client, ids: list[str], fmt: str) -> dict:
    """提交批量导出并等待后台任务结束（写法同 tests/test_api_jobs.py）。"""

    response = client.post("/projects/batch-export", json={"ids": ids, "format": fmt})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    status: dict = {}
    for _ in range(200):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert status["status"] == "done", status
    return status


def test_batch_export_skips_gate_blocked_projects(db):
    admin = _login(*ADMIN)
    good_id = _project_with_body("批量正常项目")
    blocked_id = _project_with_body("批量阻断项目")
    _add_issue(blocked_id, code="BATCH-GATE-1", level="blocking")

    status = _run_batch_export(admin, [good_id, blocked_id], "docx")
    report = status["result"]
    assert report["total"] == 2
    # 被阻断项目直接跳过，文案与单项目导出 409 逐字一致
    assert [item["project_id"] for item in report["files"]] == [good_id]
    assert [item["project_id"] for item in report["failed"]] == [blocked_id]
    assert report["failed"][0]["error"] == GATE_DETAIL
    assert report["ok"] == 1
    # job 消息沿用既有结构，额外体现被门禁拦下的数量
    assert "成功 1" in status["message"] and "失败 1" in status["message"]
    assert "风险阻断 1" in status["message"]

    with zipfile.ZipFile(report["zip_path"]) as bundle:
        names = bundle.namelist()
    assert len(names) == 1
    assert names[0].endswith(".docx")


def test_batch_export_xlsx_is_unaffected_by_gate(db):
    admin = _login(*ADMIN)
    blocked_id = _project_with_body("批量阻断 xlsx 项目")
    _add_issue(blocked_id, code="BATCH-GATE-X", level="blocking")
    empty_id = _make_project(admin, "批量空正文项目")

    status = _run_batch_export(admin, [blocked_id, empty_id], "xlsx")
    report = status["result"]
    assert report["failed"] == []
    assert report["ok"] == 2
    assert {item["project_id"] for item in report["files"]} == {blocked_id, empty_id}
    with zipfile.ZipFile(report["zip_path"]) as bundle:
        names = bundle.namelist()
    assert len(names) == 2
    assert all(name.endswith(".xlsx") for name in names)


def test_batch_export_all_blocked_finishes_with_empty_zip(db):
    admin = _login(*ADMIN)
    ids = []
    for index in range(2):
        project_id = _project_with_body(f"批量全阻断项目 {index}")
        _add_issue(project_id, code=f"BATCH-BLOCK-{index}", level="blocking")
        ids.append(project_id)

    status = _run_batch_export(admin, ids, "docx")
    report = status["result"]
    assert status["status"] == "done"  # 任务本身正常完成
    assert report["ok"] == 0
    assert report["files"] == []
    assert len(report["failed"]) == 2
    assert all(item["error"] == GATE_DETAIL for item in report["failed"])
    assert "风险阻断 2" in status["message"]

    with zipfile.ZipFile(report["zip_path"]) as bundle:
        assert bundle.namelist() == []  # zip 不含任何正文文件
