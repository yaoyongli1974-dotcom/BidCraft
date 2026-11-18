"""R22 项目复制（模板复用）：POST /projects/{ref}/duplicate 契约测试。"""

from __future__ import annotations

import base64
import copy
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "dup-pass-1"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _admin():
    return _login(*ADMIN)


def _make_user(admin, username: str, role: str = "manager") -> dict:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_project(client, name: str = "模板源项目") -> dict:
    response = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _write_boq(path, rows):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def _duplicate(client, project_id: str, body: dict | None = None):
    return client.post(f"/projects/{project_id}/duplicate", json=body or {})


def _all(model, project_id: str) -> list:
    from sqlalchemy import select

    from bidcraft.db import session_scope

    with session_scope() as session:
        return list(session.scalars(select(model).where(model.project_id == project_id)))


def _projects() -> list:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import Project

    with session_scope() as session:
        return list(session.scalars(select(Project)))


def _project_dirs() -> set[str]:
    from bidcraft.config import settings

    root = settings.home / "projects"
    return {path.name for path in root.iterdir()} if root.is_dir() else set()


@pytest.fixture()
def rich(db, tmp_path, tender_path):
    """A source project carrying every copyable entity plus excluded history."""

    from sqlalchemy import select

    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.models import (
        BoqQuoteVersion,
        Delivery,
        Fact,
        ImageAsset,
        Issue,
        Job,
        JobEvent,
        Material,
        OutlineNode,
        Parameter,
        Project,
        Requirement,
        Risk,
        ScoringItem,
        Section,
        SourceFile,
    )

    admin = _admin()
    created = _make_project(admin)
    pid = created["id"]

    # 直接 add_source（不走上传 API 的自动解析），台账完全由本 fixture 控制。
    from bidcraft.pipeline import add_source

    with session_scope() as session:
        project = session.get(Project, pid)
        project.meta = {
            "number": "ZB-2026-0412",
            "bid_section": "一标段",
            "price_policy": {"enabled": True, "factor": 1.05, "tax_rate": 0.09},
            "approval_chain": [{"approvers": [{"id": "u1", "name": "甲"}], "mode": "all"}],
            "nested": {"items": [1, 2, 3]},
        }
        # 同名导入两次 → 第二个物理文件带冲突后缀。
        first = add_source(session, project, tender_path)
        add_source(session, project, tender_path)
        ids_tender = first.id

    package = admin.post(
        f"/projects/{pid}/packages", json={"name": "一标段", "number": "A-01"}
    ).json()
    boq_path = _write_boq(
        tmp_path / "boq.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "480"],
            [2, "配线架", "24 口", "个", "20"],
        ],
    )
    with boq_path.open("rb") as handle:
        uploaded = admin.post(
            f"/projects/{pid}/boq",
            files={"file": ("boq.xlsx", handle, XLSX_MIME)},
            data={"package_id": package["id"]},
        )
    assert uploaded.status_code == 200, uploaded.text
    priced = admin.post(
        f"/projects/{pid}/boq/price",
        json={"boq_row": 2, "unit_price": 10, "package_id": package["id"]},
    )
    assert priced.status_code == 200, priced.text

    ids: dict[str, str] = {"tender_source": ids_tender}
    with session_scope() as session:
        sources = list(
            session.scalars(
                select(SourceFile)
                .where(SourceFile.project_id == pid)
                .order_by(SourceFile.created_at)
            )
        )
        ids["boq_source"] = next(item.id for item in sources if item.role == "boq")

        parent = OutlineNode(
            project_id=pid,
            heading="第一章 技术方案",
            position=0,
            intent="总体方案",
            target_words=2000,
            needs=["project.name"],
            status="edited",
        )
        session.add(parent)
        session.flush()
        child = OutlineNode(
            project_id=pid,
            parent_id=parent.id,
            heading="1.1 系统概述",
            position=0,
            intent="概述",
            target_words=800,
            status="edited",
        )
        session.add(child)
        session.flush()
        root2 = OutlineNode(project_id=pid, heading="第二章 报价", position=1)
        session.add(root2)
        session.flush()
        ids.update(parent=parent.id, child=child.id, root2=root2.id)
        session.add(
            Section(node_id=parent.id, body="父章节正文内容。", status="edited", words=8, revision=5)
        )
        session.add(
            Section(node_id=child.id, body="子章节正文内容。", status="edited", words=8, revision=3)
        )
        session.add(Section(node_id=root2.id, body="", status="pending", words=0))

        session.add(
            Fact(
                project_id=pid,
                key="project.name",
                label="项目名称",
                value="某园区智能化工程",
                status="confirmed",
                confidence=0.9,
                required=True,
                source_file_id=ids["tender_source"],
                source_page=2,
                source_quote="项目名称：某园区智能化工程",
            )
        )
        session.add(
            Fact(project_id=pid, key="ghost.ref", value="悬空引用", source_file_id="ghost-file-id")
        )
        session.add(
            Material(
                project_id=pid,
                name="网络面板",
                spec="双口 Cat6A",
                unit="个",
                qty=480.0,
                note="备注",
                source_quote="面板 480",
            )
        )
        session.add(
            Parameter(
                project_id=pid,
                name="线缆等级",
                value="Cat6A",
                requirement="六类及以上",
                category="技术",
                mandatory=True,
                source_quote="Cat6A",
            )
        )
        session.add(
            ScoringItem(
                project_id=pid,
                name="技术方案",
                score=40.0,
                criteria="方案完整",
                category="技术",
                section_hint="第一章",
                source_quote="技术方案 40",
            )
        )
        session.add(
            Requirement(
                project_id=pid,
                kind="substantive",
                text="须具备二级资质",
                mandatory=True,
                source_quote="二级资质",
            )
        )
        session.add(Risk(project_id=pid, level="major", text="工期紧张", source_quote="90 天"))

        session.add(
            ImageAsset(
                project_id=pid,
                node_id=child.id,
                label="失败配图",
                prompt="p",
                status="failed",
                file_path="",
                provider="openai",
                model="m",
                last_error="provider down",
                options={"style": "realistic"},
            )
        )
        session.add(
            ImageAsset(
                project_id=pid,
                node_id=child.id,
                label="缺文件配图",
                status="ready",
                file_path=str(settings.project_dir(pid) / "images" / "gone.png"),
                filename="gone.png",
                size_bytes=10,
                provider="upload",
            )
        )

        session.add(Issue(project_id=pid, code="CBL-001", level="major", finding="问题"))
        job = Job(project_id=pid, kind="generate", status="done")
        session.add(job)
        session.flush()
        session.add(JobEvent(job_id=job.id, kind="progress", data={"p": 1}))
        session.add(Delivery(project_id=pid, file_format="docx", file_path="x.docx", status="done"))
        session.add(
            BoqQuoteVersion(
                project_id=pid,
                package_id=package["id"],
                name="V1",
                prices={"工程量清单!2": 10.0},
                summary={"total": 4800.0},
            )
        )

    uploaded = admin.post(
        f"/projects/{pid}/images/upload",
        data={"label": "拓扑图", "node_id": ids["child"]},
        files={"file": ("topo.png", PNG_1X1, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    ids["image"] = uploaded.json()["id"]

    editor_user = _make_user(admin, "dup-editor")
    viewer_user = _make_user(admin, "dup-viewer")
    for username, role in (("dup-editor", "editor"), ("dup-viewer", "viewer")):
        granted = admin.post(
            f"/projects/{pid}/members", json={"username": username, "role": role}
        )
        assert granted.status_code == 201, granted.text

    return {
        "admin": admin,
        "editor": _login("dup-editor"),
        "viewer": _login("dup-viewer"),
        "created": created,
        "project_id": pid,
        "package_id": package["id"],
        "editor_user_id": editor_user["id"],
        "viewer_user_id": viewer_user["id"],
        **ids,
    }


def test_full_copy_payload_shape_and_stats(rich):
    response = _duplicate(rich["admin"], rich["project_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == set(rich["created"])
    assert body["id"] != rich["project_id"]
    assert body["name"] == "模板源项目 副本"
    assert body["package"] == rich["created"]["package"]
    assert body["status"] == rich["created"]["status"]
    assert body["buyer"] == rich["created"]["buyer"]
    assert body["my_role"] == "admin"
    assert body["bid_section"] == "一标段"
    assert body["stats"]["outline"] == 3
    assert body["stats"]["written"] == 2
    assert body["stats"]["sources"] == 2
    assert body["stats"]["facts_total"] == 2
    assert body["stats"]["issues_open"] == 0


def test_meta_deep_copied_both_directions(rich):
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    pid = rich["project_id"]
    new_id = _duplicate(rich["admin"], pid).json()["id"]
    with session_scope() as session:
        source = session.get(Project, pid)
        clone = session.get(Project, new_id)
        assert clone.meta == source.meta
        assert clone.meta is not source.meta
        meta = copy.deepcopy(clone.meta)
        meta["nested"]["items"].append(99)
        meta["price_policy"]["factor"] = 2.0
        meta["approval_chain"][0]["mode"] = "any"
        clone.meta = meta
    with session_scope() as session:
        source = session.get(Project, pid)
        assert source.meta["nested"]["items"] == [1, 2, 3]
        assert source.meta["price_policy"]["factor"] == 1.05
        assert source.meta["approval_chain"][0]["mode"] == "all"
        meta = copy.deepcopy(source.meta)
        meta["bid_section"] = "二标段"
        source.meta = meta
    with session_scope() as session:
        assert session.get(Project, new_id).meta["bid_section"] == "一标段"


def test_outline_tree_and_sections_remapped(rich):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    pid = rich["project_id"]
    new_id = _duplicate(rich["admin"], pid).json()["id"]
    nodes = _all(OutlineNode, new_id)
    assert len(nodes) == 3
    old_ids = {rich["parent"], rich["child"], rich["root2"]}
    assert all(node.id not in old_ids for node in nodes)
    by_heading = {node.heading: node for node in nodes}
    parent = by_heading["第一章 技术方案"]
    child = by_heading["1.1 系统概述"]
    assert child.parent_id == parent.id
    assert parent.parent_id is None
    assert by_heading["第二章 报价"].parent_id is None
    assert parent.position == 0 and parent.intent == "总体方案"
    assert parent.target_words == 2000 and parent.needs == ["project.name"]
    assert parent.status == "edited"
    assert child.position == 0 and child.target_words == 800 and child.status == "edited"

    with session_scope() as session:
        sections = {
            section.node_id: section
            for section in session.scalars(
                select(Section).where(Section.node_id.in_([node.id for node in nodes]))
            )
        }
    assert set(sections) == {node.id for node in nodes}
    assert sections[parent.id].body == "父章节正文内容。"
    assert sections[parent.id].words == 8
    assert sections[parent.id].status == "edited"
    assert sections[parent.id].revision == 0
    assert sections[child.id].body == "子章节正文内容。"
    assert sections[child.id].revision == 0
    assert sections[by_heading["第二章 报价"].id].body == ""

    listed = rich["admin"].get(f"/projects/{new_id}/outline")
    assert listed.status_code == 200
    assert len(listed.json()["chapters"]) == 3


def test_ledger_six_tables_copied_and_source_refs_remapped(rich):
    from bidcraft.models import (
        Fact,
        Material,
        Parameter,
        Requirement,
        Risk,
        ScoringItem,
        SourceFile,
    )

    pid = rich["project_id"]
    new_id = _duplicate(rich["admin"], pid).json()["id"]
    for model, count in (
        (Fact, 2),
        (Material, 1),
        (Parameter, 1),
        (ScoringItem, 1),
        (Requirement, 1),
        (Risk, 1),
    ):
        old_rows = _all(model, pid)
        new_rows = _all(model, new_id)
        assert len(new_rows) == len(old_rows) == count
        assert {row.id for row in new_rows}.isdisjoint({row.id for row in old_rows})

    new_tender = next(
        row
        for row in _all(SourceFile, new_id)
        if row.role == "tender" and Path(row.stored_path).name == "tender_cabling.txt"
    )
    facts = {fact.key: fact for fact in _all(Fact, new_id)}
    named = facts["project.name"]
    assert named.value == "某园区智能化工程"
    assert named.label == "项目名称"
    assert named.status == "confirmed"
    assert named.confidence == 0.9
    assert named.required is True
    assert named.source_page == 2
    assert named.source_quote == "项目名称：某园区智能化工程"
    assert named.source_file_id == new_tender.id
    assert named.source_file_id != rich["tender_source"]
    assert facts["ghost.ref"].source_file_id == ""

    material = _all(Material, new_id)[0]
    assert (material.name, material.spec, material.unit, material.qty) == (
        "网络面板",
        "双口 Cat6A",
        "个",
        480.0,
    )
    assert material.note == "备注" and material.source_quote == "面板 480"
    parameter = _all(Parameter, new_id)[0]
    assert (parameter.name, parameter.value, parameter.category) == ("线缆等级", "Cat6A", "技术")
    assert parameter.mandatory is True and parameter.requirement == "六类及以上"
    scoring = _all(ScoringItem, new_id)[0]
    assert (scoring.name, scoring.score, scoring.section_hint) == ("技术方案", 40.0, "第一章")
    requirement = _all(Requirement, new_id)[0]
    assert (requirement.kind, requirement.text, requirement.mandatory) == (
        "substantive",
        "须具备二级资质",
        True,
    )
    risk = _all(Risk, new_id)[0]
    assert (risk.level, risk.text, risk.source_quote) == ("major", "工期紧张", "90 天")


def test_source_files_copied_to_new_dir_with_collision_suffix(rich):
    from bidcraft.config import settings
    from bidcraft.models import SourceFile

    pid = rich["project_id"]
    new_id = _duplicate(rich["admin"], pid).json()["id"]
    old_rows = _all(SourceFile, pid)
    new_rows = _all(SourceFile, new_id)
    assert len(new_rows) == len(old_rows) == 3
    assert {row.id for row in new_rows}.isdisjoint({row.id for row in old_rows})
    new_root = settings.project_dir(new_id)
    fields = ("filename", "role", "size", "mime", "checksum", "page_count", "parse_state")
    for old in old_rows:
        matches = [
            new
            for new in new_rows
            if all(getattr(new, field) == getattr(old, field) for field in fields)
        ]
        assert matches, f"no clone for {old.filename}"
        stored = Path(matches[0].stored_path)
        assert stored.is_relative_to(new_root)
        assert stored.is_file()
        assert stored.read_bytes() == Path(old.stored_path).read_bytes()
    tender_names = {Path(row.stored_path).name for row in new_rows if row.role == "tender"}
    assert tender_names == {"tender_cabling.txt", "tender_cabling-1.txt"}
    # 源项目文件不受影响。
    assert all(Path(row.stored_path).is_file() for row in old_rows)


def test_boq_packages_and_snapshots_copied_with_remapped_ids(rich):
    from bidcraft.models import BidPackage, BoqSnapshot, SourceFile

    pid = rich["project_id"]
    new_id = _duplicate(rich["admin"], pid).json()["id"]
    packages = _all(BidPackage, new_id)
    assert len(packages) == 1
    package = packages[0]
    assert package.id != rich["package_id"]
    assert (package.name, package.number, package.position) == ("一标段", "A-01", 0)

    old_snapshot = _all(BoqSnapshot, pid)[0]
    new_snapshot = _all(BoqSnapshot, new_id)[0]
    assert new_snapshot.id != old_snapshot.id
    assert new_snapshot.package_id == package.id
    new_boq_source = next(row for row in _all(SourceFile, new_id) if row.role == "boq")
    assert new_snapshot.source_file_id == new_boq_source.id
    assert new_snapshot.source_file_id != old_snapshot.source_file_id
    for field in ("items", "prices", "warnings", "ignored", "filename", "sheet", "header_row"):
        assert getattr(new_snapshot, field) == getattr(old_snapshot, field)

    listed = rich["admin"].get(f"/projects/{new_id}/packages").json()
    assert listed[0]["has_boq"] is True
    boq = rich["admin"].get(f"/projects/{new_id}/boq?package_id={package.id}")
    assert boq.status_code == 200, boq.text
    assert boq.json()["summary"]["boq_rows"] == 2
    priced_row = next(row for row in boq.json()["rows"] if row["boq_row"] == 2)
    assert priced_row["price"]["unit_price"] == 10.0


def test_history_and_collaboration_not_copied(rich):
    from sqlalchemy import func, select

    from bidcraft.db import session_scope
    from bidcraft.models import (
        BoqQuoteVersion,
        Delivery,
        Issue,
        Job,
        JobEvent,
        ProjectMember,
    )

    pid = rich["project_id"]
    new_id = _duplicate(rich["admin"], pid).json()["id"]
    for model in (BoqQuoteVersion, Issue, Job, Delivery, ProjectMember):
        assert _all(model, new_id) == []
    # JobEvent 无 project_id 列：全表事件数不应因复制增加。
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(JobEvent)) == 1

    members = rich["admin"].get(f"/projects/{new_id}/members").json()
    assert [item["role"] for item in members] == ["owner"]
    assert members[0]["username"] == "admin"
    assert rich["admin"].get(f"/projects/{new_id}/issues").json() == []
    versions = rich["admin"].get(
        f"/projects/{new_id}/boq/versions?package_id={_all_package_id(new_id)}"
    )
    assert versions.status_code == 200
    assert versions.json() == []

    # 源项目的历史与成员保持不变。
    assert len(_all(BoqQuoteVersion, pid)) == 1
    assert len(_all(Issue, pid)) == 1
    assert len(_all(Job, pid)) == 1
    assert len(_all(Delivery, pid)) == 1
    assert len(_all(ProjectMember, pid)) == 2


def _all_package_id(project_id: str) -> str:
    from bidcraft.models import BidPackage

    return _all(BidPackage, project_id)[0].id


def test_images_copied_remapped_and_degraded_rows_kept(rich):
    from bidcraft.config import settings
    from bidcraft.models import ImageAsset, OutlineNode

    pid = rich["project_id"]
    new_id = _duplicate(rich["admin"], pid).json()["id"]
    assets = {asset.label: asset for asset in _all(ImageAsset, new_id)}
    assert len(assets) == 3
    new_child = next(
        node for node in _all(OutlineNode, new_id) if node.heading == "1.1 系统概述"
    )

    ready = assets["拓扑图"]
    old_ready = next(asset for asset in _all(ImageAsset, pid) if asset.label == "拓扑图")
    assert ready.id != old_ready.id
    assert ready.node_id == new_child.id != rich["child"]
    assert ready.status == "ready"
    assert ready.filename == old_ready.filename
    assert ready.size_bytes == old_ready.size_bytes
    assert ready.provider == old_ready.provider
    assert ready.options == old_ready.options
    stored = Path(ready.file_path)
    assert stored.is_relative_to(settings.project_dir(new_id))
    assert stored.read_bytes() == PNG_1X1

    failed = assets["失败配图"]
    assert failed.status == "failed"
    assert failed.file_path == ""
    assert failed.last_error == "provider down"
    assert failed.node_id == new_child.id
    missing = assets["缺文件配图"]
    assert missing.status == "ready"
    assert missing.file_path == ""

    served = rich["admin"].get(f"/projects/{new_id}/images/{ready.filename}")
    assert served.status_code == 200
    assert served.content == PNG_1X1


def test_name_default_custom_and_repeatable(rich):
    pid = rich["project_id"]
    assert _duplicate(rich["admin"], pid, {}).json()["name"] == "模板源项目 副本"
    assert _duplicate(rich["admin"], pid, {"name": "   "}).json()["name"] == "模板源项目 副本"
    assert _duplicate(rich["admin"], pid, {"name": None}).json()["name"] == "模板源项目 副本"
    custom = _duplicate(rich["admin"], pid, {"name": "  自定义模板  "})
    assert custom.status_code == 201
    assert custom.json()["name"] == "自定义模板"
    again = _duplicate(rich["admin"], pid, {"name": "自定义模板"})
    assert again.status_code == 201
    assert again.json()["id"] != custom.json()["id"]


def test_name_length_bounds(rich):
    pid = rich["project_id"]
    too_long = _duplicate(rich["admin"], pid, {"name": "x" * 256})
    assert too_long.status_code == 400
    assert "255" in too_long.json()["detail"]
    assert len(_projects()) == 1
    ok = _duplicate(rich["admin"], pid, {"name": "x" * 255})
    assert ok.status_code == 201
    assert ok.json()["name"] == "x" * 255


def test_include_boq_false_skips_packages_and_snapshots(rich):
    from bidcraft.models import BidPackage, BoqSnapshot, OutlineNode, SourceFile

    pid = rich["project_id"]
    response = _duplicate(rich["admin"], pid, {"include_boq": False})
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    assert _all(BidPackage, new_id) == []
    assert _all(BoqSnapshot, new_id) == []
    # 台账/大纲/来源文件仍然复制（BOQ 源文件行也在）。
    assert len(_all(SourceFile, new_id)) == 3
    assert len(_all(OutlineNode, new_id)) == 3
    assert rich["admin"].get(f"/projects/{new_id}/packages").json() == []


def test_include_images_false_skips_assets(rich):
    from bidcraft.config import settings
    from bidcraft.models import ImageAsset

    pid = rich["project_id"]
    response = _duplicate(rich["admin"], pid, {"include_images": False})
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    assert _all(ImageAsset, new_id) == []
    assert not (settings.project_dir(new_id) / "images").exists()


def test_viewer_forbidden_editor_becomes_owner(rich):
    from bidcraft.db import session_scope
    from bidcraft.models import Project, ProjectMember

    pid = rich["project_id"]
    forbidden = _duplicate(rich["viewer"], pid)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "只读成员不能修改该项目"

    copied = _duplicate(rich["editor"], pid, {"name": "编辑副本"})
    assert copied.status_code == 201, copied.text
    body = copied.json()
    assert body["owner_id"] == rich["editor_user_id"]
    assert body["my_role"] == "owner"
    assert _all(ProjectMember, body["id"]) == []
    members = rich["editor"].get(f"/projects/{body['id']}/members").json()
    assert [(item["username"], item["role"]) for item in members] == [("dup-editor", "owner")]
    with session_scope() as session:
        assert session.get(Project, pid).owner_id != rich["editor_user_id"]
    # 超管可见副本；无成员授权的外人看不到源项目。
    assert rich["admin"].get(f"/projects/{body['id']}").status_code == 200
    outsider = _login(*_make_outsider(rich["admin"]))
    assert _duplicate(outsider, pid).status_code == 404


def _make_outsider(admin) -> tuple[str, str]:
    _make_user(admin, "dup-outsider")
    return ("dup-outsider", PASSWORD)


def test_audit_project_duplicate_recorded_on_new_project(rich):
    pid = rich["project_id"]
    copied = _duplicate(
        rich["editor"],
        pid,
        {"name": "审计副本", "include_boq": False, "include_images": False},
    )
    assert copied.status_code == 201, copied.text
    new_id = copied.json()["id"]
    trail = rich["editor"].get(f"/projects/{new_id}/audit").json()["items"]
    duplicates = [item for item in trail if item["action"] == "project.duplicate"]
    assert len(duplicates) == 1
    assert duplicates[0]["username"] == "dup-editor"
    assert duplicates[0]["detail"] == {
        "source_id": pid,
        "name": "审计副本",
        "include_boq": False,
        "include_images": False,
    }
    source_trail = rich["admin"].get(f"/projects/{pid}/audit").json()["items"]
    assert all(item["action"] != "project.duplicate" for item in source_trail)


def test_missing_source_file_does_not_crash(db, tender_path):
    from bidcraft.config import settings
    from bidcraft.models import SourceFile

    admin = _admin()
    pid = _make_project(admin, "缺文件项目")["id"]
    with tender_path.open("rb") as handle:
        uploaded = admin.post(
            f"/projects/{pid}/sources", files={"file": ("tender.txt", handle, "text/plain")}
        )
    assert uploaded.status_code == 200, uploaded.text
    Path(_all(SourceFile, pid)[0].stored_path).unlink()

    response = _duplicate(admin, pid)
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    rows = _all(SourceFile, new_id)
    assert len(rows) == 1
    stored = Path(rows[0].stored_path)
    # 实现约定：行保留，stored_path 指向新目录中尚不存在的目标路径。
    assert stored.is_relative_to(settings.project_dir(new_id))
    assert not stored.exists()
    assert rows[0].filename == "tender.txt"


def test_copy_failure_cleans_new_directory(db, tender_path, monkeypatch):
    import shutil

    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _admin()
    pid = _make_project(admin, "失败清理项目")["id"]
    with tender_path.open("rb") as handle:
        uploaded = admin.post(
            f"/projects/{pid}/sources", files={"file": ("tender.txt", handle, "text/plain")}
        )
    assert uploaded.status_code == 200, uploaded.text

    dirs_before = _project_dirs()
    projects_before = len(_projects())

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", boom)
    client = TestClient(create_app(), raise_server_exceptions=False)
    login = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert login.status_code == 200
    response = client.post(f"/projects/{pid}/duplicate", json={})
    assert response.status_code == 500
    assert len(_projects()) == projects_before
    assert _project_dirs() == dirs_before


def test_unknown_project_404(rich):
    assert _duplicate(rich["admin"], "no-such-project").status_code == 404
