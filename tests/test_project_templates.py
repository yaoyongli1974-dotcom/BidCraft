"""R23 项目模板库：设为模板（PATCH is_template）+ 模板列表 + 从模板创建。"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "tpl-pass-1"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
TEMPLATE_NAME = "智慧园区模板"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _admin():
    return _login(*ADMIN)


def _make_user(admin, username: str, role: str = "manager", display_name: str = "") -> dict:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": display_name or username,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_project(client, name: str) -> dict:
    response = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert response.status_code == 201, response.text
    return response.json()


def _mark_template(client, project_id: str, flag: bool = True):
    return client.patch(f"/projects/{project_id}", json={"is_template": flag})


def _instantiate(client, template_id: str, body: dict | None = None):
    return client.post(f"/projects/templates/{template_id}/instantiate", json=body or {})


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
def tpl(db, tmp_path, tender_path):
    """A marked template carrying outline/sections/BOQ price/image/history,
    plus owner, project editor/viewer members, an outsider and a global viewer."""

    from bidcraft.db import session_scope
    from bidcraft.models import BoqQuoteVersion, OutlineNode, Project, Section
    from bidcraft.pipeline import add_source

    admin = _admin()
    owner_user = _make_user(admin, "tpl-owner", display_name="模板负责人")
    owner = _login("tpl-owner")
    created = _make_project(owner, TEMPLATE_NAME)
    pid = created["id"]
    marked = _mark_template(owner, pid)
    assert marked.status_code == 200, marked.text
    assert marked.json()["is_template"] is True

    with session_scope() as session:
        project = session.get(Project, pid)
        project.meta = {"number": "ZB-2026-0412", "bid_section": "一标段"}
        add_source(session, project, tender_path)

    package = owner.post(
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
        uploaded = owner.post(
            f"/projects/{pid}/boq",
            files={"file": ("boq.xlsx", handle, XLSX_MIME)},
            data={"package_id": package["id"]},
        )
    assert uploaded.status_code == 200, uploaded.text
    priced = owner.post(
        f"/projects/{pid}/boq/price",
        json={"boq_row": 2, "unit_price": 10, "package_id": package["id"]},
    )
    assert priced.status_code == 200, priced.text

    with session_scope() as session:
        parent = OutlineNode(
            project_id=pid, heading="第一章 技术方案", position=0, target_words=2000
        )
        session.add(parent)
        session.flush()
        child = OutlineNode(
            project_id=pid, parent_id=parent.id, heading="1.1 系统概述", position=0
        )
        session.add(child)
        session.flush()
        root2 = OutlineNode(project_id=pid, heading="第二章 报价", position=1)
        session.add(root2)
        session.flush()
        session.add(
            Section(node_id=parent.id, body="父章节正文内容。", status="edited", words=8, revision=5)
        )
        session.add(
            Section(node_id=child.id, body="子章节正文内容。", status="edited", words=8, revision=3)
        )
        session.add(Section(node_id=root2.id, body="", status="pending", words=0))
        session.add(
            BoqQuoteVersion(
                project_id=pid,
                package_id=package["id"],
                name="V1",
                prices={"工程量清单!2": 10.0},
                summary={"total": 4800.0},
            )
        )
        ids = {"parent": parent.id, "child": child.id, "root2": root2.id}

    uploaded = owner.post(
        f"/projects/{pid}/images/upload",
        data={"label": "拓扑图", "node_id": ids["child"]},
        files={"file": ("topo.png", PNG_1X1, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text

    _make_user(admin, "tpl-editor")
    _make_user(admin, "tpl-viewer")
    for username, role in (("tpl-editor", "editor"), ("tpl-viewer", "viewer")):
        granted = owner.post(f"/projects/{pid}/members", json={"username": username, "role": role})
        assert granted.status_code == 201, granted.text
    outsider_user = _make_user(admin, "tpl-outsider")
    _make_user(admin, "tpl-guest", role="viewer")

    return {
        "admin": admin,
        "owner": owner,
        "editor": _login("tpl-editor"),
        "viewer": _login("tpl-viewer"),
        "outsider": _login("tpl-outsider"),
        "guest": _login("tpl-guest"),
        "created": created,
        "project_id": pid,
        "package_id": package["id"],
        "owner_user_id": owner_user["id"],
        "outsider_user_id": outsider_user["id"],
        **ids,
    }


# ---------------------------------------------------------------- PATCH 权限


def test_patch_is_template_owner_success_and_echo(tpl):
    pid = tpl["project_id"]
    response = _mark_template(tpl["owner"], pid)
    assert response.status_code == 200, response.text
    assert response.json()["is_template"] is True
    detail = tpl["owner"].get(f"/projects/{pid}")
    assert detail.status_code == 200
    assert detail.json()["is_template"] is True
    listed = tpl["owner"].get("/projects").json()
    assert next(item for item in listed if item["id"] == pid)["is_template"] is True
    trail = tpl["owner"].get(f"/projects/{pid}/audit").json()["items"]
    updates = [item for item in trail if item["action"] == "project.update"]
    assert any(item["detail"] == {"keys": ["is_template"]} for item in updates)


def test_patch_is_template_editor_forbidden(tpl):
    pid = tpl["project_id"]
    response = _mark_template(tpl["editor"], pid, flag=False)
    assert response.status_code == 403
    assert response.json()["detail"] == "仅项目负责人可执行此操作"
    assert tpl["owner"].get(f"/projects/{pid}").json()["is_template"] is True


def test_patch_is_template_viewer_forbidden(tpl):
    response = _mark_template(tpl["viewer"], tpl["project_id"], flag=False)
    assert response.status_code == 403
    assert tpl["owner"].get(f"/projects/{tpl['project_id']}").json()["is_template"] is True


def test_patch_is_template_unset_removes_from_library(tpl):
    pid = tpl["project_id"]
    response = _mark_template(tpl["owner"], pid, flag=False)
    assert response.status_code == 200, response.text
    assert response.json()["is_template"] is False
    assert tpl["owner"].get("/projects/templates").json() == []
    assert _instantiate(tpl["outsider"], pid).status_code == 404


# ---------------------------------------------------------------- 模板列表


def test_templates_list_only_templates_and_lightweight_fields(tpl):
    _make_project(tpl["owner"], "普通项目")
    response = tpl["owner"].get("/projects/templates")
    assert response.status_code == 200, response.text
    items = response.json()
    assert isinstance(items, list)
    assert [item["id"] for item in items] == [tpl["project_id"]]
    item = items[0]
    assert set(item) == {"id", "name", "package", "is_template", "updated_at", "owner_name"}
    assert item["name"] == TEMPLATE_NAME
    assert item["package"] == "weakcurrent-cabling"
    assert item["is_template"] is True
    assert item["owner_name"] == "模板负责人"
    assert datetime.fromisoformat(item["updated_at"])


def test_templates_list_any_logged_in_user_including_viewer(tpl):
    for client in (tpl["guest"], tpl["viewer"], tpl["outsider"], tpl["admin"]):
        response = client.get("/projects/templates")
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()] == [tpl["project_id"]]


def test_templates_list_requires_login(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.get("/projects/templates")
    assert response.status_code == 401
    assert response.json()["detail"] == "请先登录"


def test_templates_list_sorted_by_updated_desc(tpl):
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    older = _make_project(tpl["owner"], "旧模板")["id"]
    newer = _make_project(tpl["owner"], "新模板")["id"]
    assert _mark_template(tpl["owner"], older).status_code == 200
    assert _mark_template(tpl["owner"], newer).status_code == 200
    with session_scope() as session:
        session.get(Project, older).updated_at = datetime(2026, 1, 1)
        session.get(Project, tpl["project_id"]).updated_at = datetime(2026, 6, 1)
        session.get(Project, newer).updated_at = datetime(2026, 12, 1)
    items = tpl["owner"].get("/projects/templates").json()
    assert [item["id"] for item in items] == [newer, tpl["project_id"], older]


def test_templates_list_capped_at_200_and_null_owner(db):
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    # 复用同一 client：create_app 会触发 ensure_admin 认领无主项目，
    # 插入后不能再建新 app，否则 owner_name 不再为空。
    admin = _admin()
    with session_scope() as session:
        for index in range(201):
            session.add(Project(name=f"批量模板 {index}", is_template=True))
    items = admin.get("/projects/templates").json()
    assert len(items) == 200
    assert all(item["owner_name"] is None for item in items)


# ---------------------------------------------------------------- 从模板创建


def test_instantiate_non_member_becomes_owner(tpl):
    response = _instantiate(tpl["outsider"], tpl["project_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] != tpl["project_id"]
    assert body["my_role"] == "owner"
    assert body["owner_id"] == tpl["outsider_user_id"]
    assert body["is_template"] is False
    assert body["name"] == f"{TEMPLATE_NAME} 副本"
    assert body["stats"]["outline"] == 3
    assert tpl["outsider"].get(f"/projects/{body['id']}").status_code == 200
    # 模板本身不受影响，新项目不是模板。
    assert tpl["owner"].get(f"/projects/{tpl['project_id']}").json()["is_template"] is True
    assert [item["id"] for item in tpl["owner"].get("/projects/templates").json()] == [
        tpl["project_id"]
    ]


def test_instantiate_copies_outline_sections_and_boq_prices(tpl):
    from bidcraft.models import BidPackage, BoqSnapshot, OutlineNode, Section, SourceFile

    new_id = _instantiate(tpl["outsider"], tpl["project_id"]).json()["id"]
    nodes = _all(OutlineNode, new_id)
    assert len(nodes) == 3
    assert {node.id for node in nodes}.isdisjoint({tpl["parent"], tpl["child"], tpl["root2"]})
    by_heading = {node.heading: node for node in nodes}
    parent = by_heading["第一章 技术方案"]
    child = by_heading["1.1 系统概述"]
    assert child.parent_id == parent.id
    assert parent.parent_id is None
    assert len(_all(SourceFile, new_id)) == 2  # tender + boq 源文件

    from sqlalchemy import select

    from bidcraft.db import session_scope

    with session_scope() as session:
        sections = {
            section.node_id: section
            for section in session.scalars(
                select(Section).where(Section.node_id.in_([node.id for node in nodes]))
            )
        }
    assert sections[parent.id].body == "父章节正文内容。"
    assert sections[parent.id].revision == 0
    assert sections[child.id].body == "子章节正文内容。"
    assert sections[child.id].revision == 0

    packages = _all(BidPackage, new_id)
    assert len(packages) == 1
    assert packages[0].id != tpl["package_id"]
    snapshot = _all(BoqSnapshot, new_id)[0]
    assert snapshot.package_id == packages[0].id
    boq = tpl["outsider"].get(f"/projects/{new_id}/boq?package_id={packages[0].id}")
    assert boq.status_code == 200, boq.text
    priced_row = next(row for row in boq.json()["rows"] if row["boq_row"] == 2)
    assert priced_row["price"]["unit_price"] == 10.0


def test_instantiate_versions_and_members_not_copied(tpl):
    from bidcraft.models import BidPackage, BoqQuoteVersion, ImageAsset, ProjectMember

    new_id = _instantiate(tpl["outsider"], tpl["project_id"]).json()["id"]
    assert _all(BoqQuoteVersion, new_id) == []
    assert _all(ProjectMember, new_id) == []
    package_id = _all(BidPackage, new_id)[0].id
    versions = tpl["outsider"].get(f"/projects/{new_id}/boq/versions?package_id={package_id}")
    assert versions.status_code == 200
    assert versions.json() == []
    members = tpl["outsider"].get(f"/projects/{new_id}/members").json()
    assert [(item["username"], item["role"]) for item in members] == [("tpl-outsider", "owner")]
    # 模板的历史与成员保持不变。
    assert len(_all(BoqQuoteVersion, tpl["project_id"])) == 1
    assert len(_all(ProjectMember, tpl["project_id"])) == 2
    assert len(_all(ImageAsset, tpl["project_id"])) == 1


def test_instantiate_name_default_custom_and_blank(tpl):
    pid = tpl["project_id"]
    assert _instantiate(tpl["outsider"], pid, {}).json()["name"] == f"{TEMPLATE_NAME} 副本"
    assert _instantiate(tpl["outsider"], pid, {"name": "   "}).json()["name"] == (
        f"{TEMPLATE_NAME} 副本"
    )
    assert _instantiate(tpl["outsider"], pid, {"name": None}).json()["name"] == (
        f"{TEMPLATE_NAME} 副本"
    )
    custom = _instantiate(tpl["outsider"], pid, {"name": "  自定义新项目  "})
    assert custom.status_code == 201
    assert custom.json()["name"] == "自定义新项目"


def test_instantiate_name_length_bounds(tpl):
    pid = tpl["project_id"]
    too_long = _instantiate(tpl["outsider"], pid, {"name": "x" * 256})
    assert too_long.status_code == 400
    assert "255" in too_long.json()["detail"]
    assert len(_projects()) == 1
    ok = _instantiate(tpl["outsider"], pid, {"name": "x" * 255})
    assert ok.status_code == 201
    assert ok.json()["name"] == "x" * 255


def test_instantiate_include_boq_false_skips_packages(tpl):
    from bidcraft.models import BidPackage, BoqSnapshot, OutlineNode

    response = _instantiate(tpl["outsider"], tpl["project_id"], {"include_boq": False})
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    assert _all(BidPackage, new_id) == []
    assert _all(BoqSnapshot, new_id) == []
    assert len(_all(OutlineNode, new_id)) == 3
    assert tpl["outsider"].get(f"/projects/{new_id}/packages").json() == []


def test_instantiate_include_images_false_skips_assets(tpl):
    from bidcraft.config import settings
    from bidcraft.models import ImageAsset

    response = _instantiate(tpl["outsider"], tpl["project_id"], {"include_images": False})
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    assert _all(ImageAsset, new_id) == []
    assert not (settings.project_dir(new_id) / "images").exists()


def test_instantiate_non_template_project_404(tpl):
    plain = _make_project(tpl["owner"], "非模板项目")["id"]
    response = _instantiate(tpl["outsider"], plain)
    assert response.status_code == 404
    assert response.json()["detail"] == "模板不存在"


def test_instantiate_unknown_template_404(tpl):
    response = _instantiate(tpl["outsider"], "no-such-template")
    assert response.status_code == 404
    assert response.json()["detail"] == "模板不存在"


def test_instantiate_requires_login(tpl):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        f"/projects/templates/{tpl['project_id']}/instantiate", json={}
    )
    assert response.status_code == 401


def test_instantiate_audit_recorded_on_new_project(tpl):
    pid = tpl["project_id"]
    response = _instantiate(
        tpl["outsider"],
        pid,
        {"name": "审计新项目", "include_boq": False, "include_images": False},
    )
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    trail = tpl["outsider"].get(f"/projects/{new_id}/audit").json()["items"]
    records = [item for item in trail if item["action"] == "project.instantiate"]
    assert len(records) == 1
    assert records[0]["username"] == "tpl-outsider"
    assert records[0]["detail"] == {
        "template_id": pid,
        "name": "审计新项目",
        "include_boq": False,
        "include_images": False,
    }
    source_trail = tpl["owner"].get(f"/projects/{pid}/audit").json()["items"]
    assert all(item["action"] != "project.instantiate" for item in source_trail)


def test_instantiate_failure_cleans_new_directory(tpl, monkeypatch):
    import shutil

    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    dirs_before = _project_dirs()
    projects_before = len(_projects())

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", boom)
    client = TestClient(create_app(), raise_server_exceptions=False)
    login = client.post("/auth/login", json={"username": "tpl-outsider", "password": PASSWORD})
    assert login.status_code == 200
    response = client.post(f"/projects/templates/{tpl['project_id']}/instantiate", json={})
    assert response.status_code == 500
    assert len(_projects()) == projects_before
    assert _project_dirs() == dirs_before


def test_instantiate_images_copied_to_new_dir(tpl):
    from bidcraft.config import settings
    from bidcraft.models import ImageAsset, OutlineNode

    new_id = _instantiate(tpl["outsider"], tpl["project_id"]).json()["id"]
    assets = _all(ImageAsset, new_id)
    assert len(assets) == 1
    asset = assets[0]
    new_child = next(node for node in _all(OutlineNode, new_id) if node.heading == "1.1 系统概述")
    assert asset.node_id == new_child.id != tpl["child"]
    stored = Path(asset.file_path)
    assert stored.is_relative_to(settings.project_dir(new_id))
    assert stored.read_bytes() == PNG_1X1
