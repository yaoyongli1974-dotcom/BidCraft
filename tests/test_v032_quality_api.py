"""v0.32 Phase 6：质量报告 Markdown 只读导出与单/跨项目质量摘要。

全部用例使用真实 DB 与真实 HTTP 会话生成报告，不用替身伪造成功路径。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "qa-api-pass"


def _login(username, password=PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture()
def qa(db):
    admin = _login(*ADMIN)
    for name in ("qa-owner", "qa-viewer", "qa-outsider"):
        resp = admin.post(
            "/admin/users",
            json={"username": name, "display_name": name, "password": PASSWORD, "role": "manager"},
        )
        assert resp.status_code == 201, resp.text
    owner = _login("qa-owner")
    project = owner.post(
        "/projects", json={"name": "质量导出项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    viewer = _login("qa-viewer")
    joined = owner.post(
        f"/projects/{project}/members", json={"username": "qa-viewer", "role": "viewer"}
    )
    assert joined.status_code in {200, 201}, joined.text
    outsider = _login("qa-outsider")
    other = outsider.post(
        "/projects", json={"name": "外部质量项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    with session_scope() as sess:
        project_name = sess.get(Project, project).name
        other_name = sess.get(Project, other).name
    return {
        "admin": admin,
        "owner": owner,
        "viewer": viewer,
        "outsider": outsider,
        "project": project,
        "project_name": project_name,
        "other": other,
        "other_name": other_name,
    }


def _run_with_finding(client, project, code="QA-BLOCK-7", clause_no="QA-CLAUSE-9"):
    """造一条真实阻断问题 + 一条 ★ 未确认需求，让报告带可抽查的证据锚点。"""
    from bidcraft.db import session_scope
    from bidcraft.models import Issue, OutlineNode, Requirement

    with session_scope() as db:
        node = OutlineNode(project_id=project, heading="质量证据锚点章节")
        db.add(node)
        db.flush()
        db.add(
            Issue(
                project_id=project,
                code=code,
                level="blocking",
                category="核对项",
                location="第 9 页",
                finding=f"测试阻断 {code}",
                status="open",
                node_id=node.id,
            )
        )
        db.add(
            Requirement(
                project_id=project,
                kind="substantive",
                text=f"实质性条款 {clause_no}",
                marker="★",
                clause_no=clause_no,
                response_status="pending",
                node_id=node.id,
            )
        )
    resp = client.post(f"/projects/{project}/evaluation/run", json={"note": "质量报告导出用例"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_report_export_is_viewer_safe_markdown_with_real_anchors(qa):
    project = qa["project"]
    report = _run_with_finding(qa["owner"], project)
    findings = qa["viewer"].get(
        f"/projects/{project}/evaluation/reports/{report['id']}/findings"
    ).json()
    issue_finding = next(row for row in findings if row["evidence"].get("code") == "QA-BLOCK-7")
    star_finding = next(
        row for row in findings if row["evidence"].get("clause_no") == "QA-CLAUSE-9"
    )

    resp = qa["viewer"].get(f"/projects/{project}/evaluation/reports/{report['id']}/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "charset=utf-8" in resp.headers["content-type"]
    assert resp.headers["content-disposition"] == 'attachment; filename="quality-report-1.md"'
    body = resp.text
    # 报告元数据 + 六维评分。
    assert "# 质量报告" in body
    assert report["id"] in body
    assert "版本：1" in body
    assert report["created_at"] in body
    assert "维度评分" in body and "资格符合性" in body
    assert "内部质量参考分" in body
    # 真实 finding 的锚点/证据，不是占位符。
    assert "问题明细" in body
    assert issue_finding["title"] in body
    assert "QA-BLOCK-7" in body
    assert issue_finding["node_id"] in body
    assert "code=QA-BLOCK-7" in body
    assert "clause_no=QA-CLAUSE-9" in body
    assert star_finding["requirement_id"] in body
    # 覆盖 / 指标 / 经验摘要来自报告快照。
    assert "覆盖 / 指标 / 经验摘要" in body
    assert "覆盖" in body and "指标" in body and "经验" in body
    assert "manual_edit_rate" in body
    # 零分母指标保留比率键但明确呈现为未测量，不冒充 0.0%。
    assert "manual_edit_rate：未测量" in body
    assert "issue_density：未测量" in body
    assert "boq_match_rate：未测量" in body
    # viewer 只能读，不能生成报告。
    assert qa["viewer"].post(f"/projects/{project}/evaluation/run", json={}).status_code == 403


def test_report_export_cross_project_is_404_even_with_force(qa):
    report = _run_with_finding(qa["owner"], qa["project"])
    for client, ref in ((qa["owner"], qa["other"]), (qa["outsider"], qa["other"])):
        for suffix in ("", "?force=true"):
            resp = client.get(
                f"/projects/{ref}/evaluation/reports/{report['id']}/export{suffix}"
            )
            assert resp.status_code == 404, resp.text


def test_ignored_finding_requires_nonempty_note_before_update(qa):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    project = qa["project"]
    report = _run_with_finding(qa["owner"], project, code="QA-IGNORE-3")
    findings = qa["owner"].get(
        f"/projects/{project}/evaluation/reports/{report['id']}/findings"
    ).json()
    fid = next(row["id"] for row in findings if row["evidence"].get("code") == "QA-IGNORE-3")
    url = f"/projects/{project}/evaluation/findings/{fid}"

    for note in ("", "   \t\n"):
        resp = qa["owner"].patch(url, json={"status": "ignored", "note": note})
        assert resp.status_code == 400, resp.text
    unchanged = qa["owner"].get(
        f"/projects/{project}/evaluation/reports/{report['id']}/findings"
    ).json()
    assert next(row for row in unchanged if row["id"] == fid)["status"] == "open"

    ok = qa["owner"].patch(url, json={"status": "ignored", "note": "  已确认无需修改  "})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ignored"

    with session_scope() as db:
        rows = list(
            db.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == project, AuditLog.action == "quality.finding.http"
                )
            )
        )
    assert rows and rows[-1].detail["finding_id"] == fid

    # 重新打开无需重复理由；清理处置字段由 report.py 负责，这里不加旁路逻辑。
    reopened = qa["owner"].patch(url, json={"status": "open"})
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"


def test_summary_is_scoped_and_never_fabricates_scores(qa):
    project = qa["project"]
    report = _run_with_finding(qa["owner"], project)
    empty_project = qa["owner"].post(
        "/projects", json={"name": "无报告项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    other_report = qa["outsider"].post(
        f"/projects/{qa['other']}/evaluation/run", json={}
    ).json()

    single = qa["owner"].get(f"/projects/{project}/evaluation/summary")
    assert single.status_code == 200, single.text
    assert single.json() == {
        "items": [
            {
                "project_id": project,
                "ref": project,
                "name": qa["project_name"],
                "report_id": report["id"],
                "report_version": report["report_version"],
                "overall_score": report["overall_score"],
                "grade": report["grade"],
                "verdict": report["verdict"],
                "created_at": report["created_at"],
                "measured": True,
            }
        ]
    }

    scoped = qa["owner"].get(
        f"/projects/{project}/evaluation/summary"
        f"?refs={project},{empty_project},{qa['other']},missing"
    )
    assert scoped.status_code == 200, scoped.text
    scoped_items = scoped.json()["items"]
    # 请求过的可访问项目都在，未出报告的以未测量占位条目呈现；
    # 无权访问的 other 与不存在的 missing 仍由授权层过滤。
    assert [row["project_id"] for row in scoped_items] == [project, empty_project]
    assert [row["measured"] for row in scoped_items] == [True, False]
    assert scoped_items[1] == {
        "project_id": empty_project,
        "ref": empty_project,
        "name": "无报告项目",
        "report_id": None,
        "report_version": None,
        "overall_score": None,
        "grade": None,
        "verdict": None,
        "created_at": None,
        "measured": False,
    }

    outsider = qa["outsider"].get(
        f"/projects/{qa['other']}/evaluation/summary?refs={project},{qa['other']}"
    )
    assert outsider.status_code == 200, outsider.text
    items = outsider.json()["items"]
    assert [row["project_id"] for row in items] == [qa["other"]]
    assert items[0]["ref"] == qa["other"]
    assert items[0]["name"] == qa["other_name"]
    assert items[0]["report_id"] == other_report["id"]
    assert items[0]["report_version"] == other_report["report_version"]
    assert items[0]["created_at"] == other_report["created_at"]
    assert items[0]["measured"] is True

    viewer = qa["viewer"].get(
        f"/projects/{project}/evaluation/summary?refs={project},{qa['other']}"
    )
    assert viewer.status_code == 200, viewer.text
    assert [row["project_id"] for row in viewer.json()["items"]] == [project]

    # 没有真实报告的项目只返回无分数的未测量占位条目，绝不带默认分数。
    no_report = qa["owner"].get(f"/projects/{empty_project}/evaluation/summary")
    assert no_report.status_code == 200
    assert no_report.json() == {
        "items": [
            {
                "project_id": empty_project,
                "ref": empty_project,
                "name": "无报告项目",
                "report_id": None,
                "report_version": None,
                "overall_score": None,
                "grade": None,
                "verdict": None,
                "created_at": None,
                "measured": False,
            }
        ]
    }
