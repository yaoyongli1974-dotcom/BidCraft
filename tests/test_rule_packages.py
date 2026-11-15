from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

CUSTOM_PACKAGE = """\
package: fire-test
version: 1
title: 消防测试包
description: 消防测试用规则包
project_type: fire
project_type_label: 消防工程
extends: [common-bid-facts]
fields:
  - key: fire.system
    label: 消防系统
outline:
  items:
    - heading: 项目概述
      intent: 概述项目范围
checks:
  - id: FIRE-001
    assert: "True"
    message: 占位核对项
"""


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _create_package(client, document: str = CUSTOM_PACKAGE):
    return client.post("/admin/rule-packages", json={"document": document})


def _override_builtin_document(client, name: str = "weakcurrent-cabling") -> tuple[str, str]:
    original = client.get(f"/admin/rule-packages/{name}").json()["document"]
    lines = [
        "title: 综合布线（项目定制）" if line.startswith("title:") else line
        for line in original.splitlines()
    ]
    return original, "\n".join(lines) + "\n"


def test_list_packages_exposes_project_type(db):
    from bidcraft.rules.loader import list_packages

    items = list_packages()
    assert items
    required = {
        "name",
        "title",
        "version",
        "abstract",
        "extends",
        "project_type",
        "project_type_label",
        "description",
        "builtin",
        "overridden",
        "hidden",
        "error",
    }
    for item in items:
        assert required <= set(item)
        assert item["error"] == ""
        assert item["project_type"]

    by_name = {item["name"]: item for item in items}
    assert by_name["fire-protection"]["project_type"] == "fire"
    assert by_name["installation"]["project_type"] == "installation"
    assert by_name["civil"]["project_type"] == "civil"
    assert by_name["hvac"]["project_type"] == "hvac"
    assert by_name["decoration"]["project_type"] == "decoration"
    assert by_name["weakcurrent-cabling"]["project_type"] == "weakcurrent"
    assert by_name["common-bid-facts"]["project_type"] == "common"


def test_create_custom_package_writes_user_dir(db):
    from bidcraft.config import settings
    from bidcraft.rules.loader import load_package

    client = _client()
    created = _create_package(client)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "fire-test"
    assert body["builtin"] is False
    assert body["overridden"] is False
    assert body["hidden"] is False
    assert body["usage"] == 0
    assert body["project_type"] == "fire"
    assert body["document"] == CUSTOM_PACKAGE

    path = settings.home / "packages" / "fire-test.yaml"
    assert path.is_file()
    assert load_package("fire-test").title == "消防测试包"

    visible = client.get("/packages").json()
    entry = next(item for item in visible if item["name"] == "fire-test")
    assert entry["project_type"] == "fire"
    assert entry["project_type_label"] == "消防工程"
    assert entry["builtin"] is False


def test_edit_builtin_package_creates_override(db):
    from bidcraft.config import settings
    from bidcraft.rules.loader import load_package

    client = _client()
    original, document = _override_builtin_document(client)
    assert document != original

    updated = client.put("/admin/rule-packages/weakcurrent-cabling", json={"document": document})
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["builtin"] is True
    assert body["overridden"] is True
    assert body["document"] == document
    assert (settings.home / "packages" / "weakcurrent-cabling.yaml").is_file()

    package = load_package("weakcurrent-cabling")
    assert package.title == "综合布线（项目定制）"
    # Inheritance still resolves through the common base package.
    assert any(field.key == "project.name" for field in package.fields)

    entry = next(
        item for item in client.get("/packages").json() if item["name"] == "weakcurrent-cabling"
    )
    assert entry["overridden"] is True


def test_delete_hides_from_new_projects_but_keeps_loading(db):
    from bidcraft.config import settings
    from bidcraft.rules.loader import load_package

    client = _client()
    assert _create_package(client).status_code == 201

    deleted = client.delete("/admin/rule-packages/fire-test")
    assert deleted.status_code == 200
    assert deleted.json() == {"name": "fire-test", "hidden": True}
    # The file is retained so existing projects keep rendering.
    assert (settings.home / "packages" / "fire-test.yaml").is_file()
    assert load_package("fire-test").title == "消防测试包"

    visible = client.get("/packages").json()
    assert not any(item["name"] == "fire-test" for item in visible)
    blocked = client.post("/projects", json={"name": "隐藏包项目", "package": "fire-test"})
    assert blocked.status_code == 400
    assert "隐藏" in blocked.json()["detail"]

    default_admin = client.get("/admin/rule-packages").json()
    assert not any(item["name"] == "fire-test" for item in default_admin)
    hidden_admin = client.get("/admin/rule-packages?include_hidden=true").json()
    record = next(item for item in hidden_admin if item["name"] == "fire-test")
    assert record["hidden"] is True


def test_delete_referenced_package_returns_409(db):
    client = _client()
    assert _create_package(client).status_code == 201
    project = client.post("/projects", json={"name": "消防测试项目", "package": "fire-test"})
    assert project.status_code == 201, project.text

    blocked = client.delete("/admin/rule-packages/fire-test")
    assert blocked.status_code == 409
    assert "仍有 1 个项目使用该规则包" in blocked.json()["detail"]

    record = next(
        item
        for item in client.get("/admin/rule-packages").json()
        if item["name"] == "fire-test"
    )
    assert record["usage"] == 1


def test_restore_unhides_and_reset_override_restores_builtin(db):
    from bidcraft.config import settings
    from bidcraft.rules.loader import load_package

    client = _client()
    assert _create_package(client).status_code == 201
    assert client.delete("/admin/rule-packages/fire-test").status_code == 200

    restored = client.post("/admin/rule-packages/fire-test/restore")
    assert restored.status_code == 200
    assert restored.json()["hidden"] is False
    assert any(item["name"] == "fire-test" for item in client.get("/packages").json())

    original, document = _override_builtin_document(client)
    assert client.put(
        "/admin/rule-packages/weakcurrent-cabling", json={"document": document}
    ).status_code == 200
    assert load_package("weakcurrent-cabling").title == "综合布线（项目定制）"

    reset = client.post(
        "/admin/rule-packages/weakcurrent-cabling/restore?reset_override=true"
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["overridden"] is False
    assert reset.json()["document"] == original
    assert not (settings.home / "packages" / "weakcurrent-cabling.yaml").exists()
    assert load_package("weakcurrent-cabling").title != "综合布线（项目定制）"

    bad_reset = client.post("/admin/rule-packages/fire-test/restore?reset_override=true")
    assert bad_reset.status_code == 400


def test_validate_endpoint_checks_yaml_expressions_and_name(db):
    client = _client()

    ok = client.post(
        "/admin/rule-packages/validate",
        json={"name": "fire-test", "document": CUSTOM_PACKAGE},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["problems"] == []
    assert body["summary"]["fields"] >= 1
    assert body["summary"]["outline"] == 1
    assert body["summary"]["checks"] >= 1
    # Validation never writes.
    from bidcraft.config import settings

    assert not (settings.home / "packages" / "fire-test.yaml").exists()

    broken_yaml = client.post(
        "/admin/rule-packages/validate",
        json={"document": "package: [unclosed"},
    )
    assert broken_yaml.status_code == 200
    assert broken_yaml.json()["ok"] is False
    assert broken_yaml.json()["problems"]

    bad_expression = CUSTOM_PACKAGE.replace(
        'assert: "True"', 'assert: "open(\'/etc/passwd\')"'
    )
    expression_result = client.post(
        "/admin/rule-packages/validate",
        json={"name": "fire-test", "document": bad_expression},
    )
    assert expression_result.json()["ok"] is False
    assert any("FIRE-001" in problem for problem in expression_result.json()["problems"])

    mismatch = client.post(
        "/admin/rule-packages/validate",
        json={"name": "other-name", "document": CUSTOM_PACKAGE},
    )
    assert mismatch.json()["ok"] is False
    assert any("不一致" in problem for problem in mismatch.json()["problems"])


def test_update_requires_package_field_matching_path(db):
    client = _client()
    assert _create_package(client).status_code == 201

    missing = client.put(
        "/admin/rule-packages/fire-test",
        json={"document": "title: 缺少包名\nfields: []\n"},
    )
    assert missing.status_code == 400
    assert "package" in missing.json()["detail"]

    mismatch = client.put(
        "/admin/rule-packages/fire-test",
        json={"document": CUSTOM_PACKAGE.replace("package: fire-test", "package: other")},
    )
    assert mismatch.status_code == 400
    assert "不一致" in mismatch.json()["detail"]


def test_create_duplicate_and_invalid_document(db):
    client = _client()
    assert _create_package(client).status_code == 201
    duplicate = _create_package(client)
    assert duplicate.status_code == 409

    no_name = _create_package(client, "title: 缺少包名\nfields: []\n")
    assert no_name.status_code == 400
    assert "package" in no_name.json()["detail"]

    bad_name = _create_package(client, CUSTOM_PACKAGE.replace("package: fire-test", "package: ../evil"))
    assert bad_name.status_code == 400


def test_non_super_admin_cannot_manage_rule_packages(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    created = admin.post(
        "/admin/users",
        json={"username": "rule-viewer", "password": "viewer-pass-1", "role": "viewer"},
    )
    assert created.status_code == 201

    viewer = TestClient(create_app())
    assert viewer.post(
        "/auth/login", json={"username": "rule-viewer", "password": "viewer-pass-1"}
    ).status_code == 200
    assert viewer.get("/admin/rule-packages").status_code == 403
    assert viewer.post(
        "/admin/rule-packages/validate", json={"document": CUSTOM_PACKAGE}
    ).status_code == 403


def test_broken_yaml_in_user_dir_does_not_500(db):
    from bidcraft.config import settings

    packages = settings.home / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    (packages / "broken.yaml").write_text("package: broken\nfields: [\n", encoding="utf-8")

    client = _client()
    response = client.get("/admin/rule-packages?include_hidden=true")
    assert response.status_code == 200
    record = next(item for item in response.json() if item["name"] == "broken")
    assert record["error"]
    # The healthy packages still list normally.
    assert any(item["name"] == "weakcurrent-cabling" for item in response.json())
