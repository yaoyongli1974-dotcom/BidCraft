"""插件包（.bidplugin）导入/导出：包体校验、规范化、受控安装与升级 API。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from bidcraft import plugin_pack
from bidcraft.db import session_scope
from bidcraft.models import Plugin

IMPORTABLE = (
    "name",
    "transport",
    "command",
    "args",
    "env",
    "url",
    "enabled",
    "tool_allowlist",
    "mem_limit_mb",
    "cpu_seconds",
)


def _client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _viewer_client(admin):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    assert (
        admin.post(
            "/admin/users",
            json={"username": "packviewer", "password": "viewer-pass-1", "role": "viewer"},
        ).status_code
        == 201
    )
    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "packviewer", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200
    return viewer


def _make_plugin(**overrides) -> Plugin:
    fields = {
        "name": "包插件",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"LOG_LEVEL": "error"},
        "url": "",
        "enabled": True,
        "tool_allowlist": ["read_file", "list_directory"],
        "mem_limit_mb": 256,
        "cpu_seconds": 20,
    }
    fields.update(overrides)
    with session_scope() as db:
        row = Plugin(**fields)
        db.add(row)
        db.flush()
        return row


def _pack(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return buffer.getvalue()


def _base_manifest(**overrides) -> dict:
    manifest = {
        "format": plugin_pack.FORMAT,
        "version": plugin_pack.FORMAT_VERSION,
        "name": "包插件",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "server"],
        "env": {"LOG_LEVEL": "error"},
        "url": "",
        "enabled": True,
        "tool_allowlist": ["read_file"],
        "mem_limit_mb": 256,
        "cpu_seconds": 20,
    }
    manifest.update(overrides)
    return manifest


def _upload(client, data: bytes, *, on_conflict: str | None = None, name="pkg.bidplugin.zip"):
    url = "/admin/plugins/import"
    if on_conflict is not None:
        url += f"?on_conflict={on_conflict}"
    return client.post(url, files={"file": (name, data, "application/zip")})


def _manifest_from_zip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        assert bundle.namelist() == ["manifest.json"]
        return json.loads(bundle.read("manifest.json"))


# ------------------------------------------------------------------ 包体与规范化


def test_export_parse_roundtrip(db):
    row = _make_plugin()
    data = plugin_pack.export_package(row)
    manifest = plugin_pack.parse_package(data)
    assert manifest["format"] == plugin_pack.FORMAT
    assert manifest["version"] == plugin_pack.FORMAT_VERSION
    assert manifest["exported_at"]
    for key in IMPORTABLE:
        assert manifest[key] == getattr(row, key), key
    # validate 幂等：规范化后再次校验字段不变。
    normalized = plugin_pack.validate_manifest(manifest)
    assert plugin_pack.validate_manifest(normalized) == normalized


def test_parse_package_rejects_bad_input(db):
    with pytest.raises(ValueError, match="过大"):
        plugin_pack.parse_package(b"x" * (plugin_pack.MAX_PACKAGE_BYTES + 1))
    with pytest.raises(ValueError, match="zip"):
        plugin_pack.parse_package(b"not a zip at all")
    empty_zip = io.BytesIO()
    with zipfile.ZipFile(empty_zip, "w") as bundle:
        bundle.writestr("readme.txt", "no manifest here")
    with pytest.raises(ValueError, match="manifest.json"):
        plugin_pack.parse_package(empty_zip.getvalue())
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as bundle:
        bundle.writestr("manifest.json", "{not json")
    with pytest.raises(ValueError, match="JSON"):
        plugin_pack.parse_package(broken.getvalue())
    with pytest.raises(ValueError, match="format"):
        plugin_pack.parse_package(_pack(_base_manifest(format="other-format")))
    with pytest.raises(ValueError, match="version"):
        plugin_pack.parse_package(_pack(_base_manifest(version=plugin_pack.FORMAT_VERSION + 1)))


def test_validate_manifest_rules_and_clamp(db):
    normalized = plugin_pack.validate_manifest(
        _base_manifest(mem_limit_mb=999999, cpu_seconds=-3, note="忽略我", exported_at="x")
    )
    assert normalized["mem_limit_mb"] == 512
    assert normalized["cpu_seconds"] == 10
    assert "note" not in normalized
    assert plugin_pack.validate_manifest(_base_manifest())["enabled"] is True
    assert plugin_pack.validate_manifest({"name": "n", "command": "npx"})["enabled"] is True

    for bad, match in (
        (_base_manifest(name=""), "名称"),
        (_base_manifest(name="a__b"), "双下划线"),
        (_base_manifest(name="x" * 61), "60"),
        (_base_manifest(transport="carrier"), "transport"),
        (_base_manifest(command=""), "command"),
        (_base_manifest(command="c" * 501), "500"),
        (_base_manifest(transport="http", url=""), "url"),
        (_base_manifest(transport="http", url="ftp://x"), "http://"),
        (_base_manifest(args=[f"a{i}" for i in range(21)]), "20"),
        (_base_manifest(args=["x" * 201]), "200"),
        (_base_manifest(env={f"k{i}": "v" for i in range(41)}), "40"),
        (_base_manifest(env={"k" * 61: "v"}), "60"),
        (_base_manifest(env={"K": "v" * 201}), "200"),
        (_base_manifest(tool_allowlist=[f"t{i}" for i in range(201)]), "200"),
        (_base_manifest(tool_allowlist=["t" * 121]), "120"),
    ):
        with pytest.raises(ValueError, match=match):
            plugin_pack.validate_manifest(bad)
    with pytest.raises(ValueError):
        plugin_pack.validate_manifest({"command": "npx"})  # 缺 name


def test_apply_manifest_create_replace_skip(db):
    manifest = _base_manifest()
    with session_scope() as session:
        row, created = plugin_pack.apply_manifest(session, manifest, on_conflict="replace")
        assert created is True
        assert row.name == "包插件"
        plugin_id = row.id

    upgraded = _base_manifest(command="uvx", mem_limit_mb=128, cpu_seconds=5, enabled=False)
    with session_scope() as session:
        row, created = plugin_pack.apply_manifest(session, upgraded, on_conflict="replace")
        assert created is False
        assert row.id == plugin_id
        assert row.command == "uvx"
        assert row.mem_limit_mb == 128
        assert row.cpu_seconds == 5
        assert row.enabled is False

    with session_scope() as session:
        with pytest.raises(plugin_pack.PluginPackConflict):
            plugin_pack.apply_manifest(session, manifest, on_conflict="skip")
        with pytest.raises(ValueError, match="on_conflict"):
            plugin_pack.apply_manifest(session, manifest, on_conflict="merge")


# ------------------------------------------------------------------ 导入 API


def test_import_roundtrip_creates_plugin_with_same_fields(db):
    client = _client()
    row = _make_plugin()
    data = plugin_pack.export_package(row)
    manifest = _manifest_from_zip(data)
    manifest["name"] = "导入副本"

    response = _upload(client, _pack(manifest))
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["name"] == "导入副本"
    assert payload["id"] != row.id
    for key in IMPORTABLE:
        if key == "name":
            continue
        assert payload[key] == manifest[key], key
    assert client.get("/admin/plugins").status_code == 200
    assert len(client.get("/admin/plugins").json()) == 2


def test_import_replace_overwrites_existing_plugin(db):
    client = _client()
    created = client.post(
        "/admin/plugins",
        json={"name": "升级目标", "command": "npx", "mem_limit_mb": 256, "cpu_seconds": 20},
    )
    assert created.status_code == 201
    plugin_id = created.json()["id"]

    manifest = _base_manifest(
        name="升级目标",
        command="uvx",
        args=["mcp-server-fetch"],
        env={"TOKEN": "abc"},
        tool_allowlist=["fetch"],
        mem_limit_mb=1024,
        cpu_seconds=60,
        enabled=False,
    )
    response = _upload(client, _pack(manifest))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == plugin_id
    assert payload["command"] == "uvx"
    assert payload["args"] == ["mcp-server-fetch"]
    assert payload["env"] == {"TOKEN": "abc"}
    assert payload["tool_allowlist"] == ["fetch"]
    assert payload["mem_limit_mb"] == 1024
    assert payload["cpu_seconds"] == 60
    assert payload["enabled"] is False

    listed = client.get("/admin/plugins").json()
    assert [item["name"] for item in listed] == ["升级目标"]


def test_import_skip_conflict_is_409(db):
    client = _client()
    _make_plugin(name="同名插件")
    response = _upload(client, _pack(_base_manifest(name="同名插件")), on_conflict="skip")
    assert response.status_code == 409, response.text
    assert "已存在" in response.json()["detail"]
    # skip 且不同名：正常新建。
    fresh = _upload(client, _pack(_base_manifest(name="另一个")), on_conflict="skip")
    assert fresh.status_code == 201, fresh.text


def test_import_rejects_invalid_packages(db):
    client = _client()
    cases = [
        (b"definitely not a zip", "zip"),
        (b"", None),
    ]
    for data, _ in cases:
        response = _upload(client, data)
        assert response.status_code == 400, response.text

    no_manifest = io.BytesIO()
    with zipfile.ZipFile(no_manifest, "w") as bundle:
        bundle.writestr("other.json", "{}")
    assert _upload(client, no_manifest.getvalue()).status_code == 400

    bad_json = io.BytesIO()
    with zipfile.ZipFile(bad_json, "w") as bundle:
        bundle.writestr("manifest.json", "{oops")
    assert _upload(client, bad_json.getvalue()).status_code == 400

    for manifest in (
        _base_manifest(format="zip-of-something"),
        _base_manifest(version=99),
        _base_manifest(transport="carrier"),
        {key: value for key, value in _base_manifest().items() if key != "name"},
        _base_manifest(name=""),
    ):
        response = _upload(client, _pack(manifest))
        assert response.status_code == 400, response.text
        assert response.json()["detail"]

    oversized = b"0" * (plugin_pack.MAX_PACKAGE_BYTES + 1)
    response = _upload(client, oversized)
    assert response.status_code == 400
    assert "过大" in response.json()["detail"]

    bad_conflict = _upload(client, _pack(_base_manifest()), on_conflict="merge")
    assert bad_conflict.status_code == 400
    assert client.get("/admin/plugins").json() == []


def test_import_clamps_out_of_range_limits(db):
    client = _client()
    response = _upload(
        client, _pack(_base_manifest(name="限额插件", mem_limit_mb=999999, cpu_seconds=0))
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["mem_limit_mb"] == 512
    assert payload["cpu_seconds"] == 10
    with session_scope() as session:
        row = session.get(Plugin, payload["id"])
        assert row.mem_limit_mb == 512
        assert row.cpu_seconds == 10


# ------------------------------------------------------------------ 导出 API


def test_download_package_matches_payload(db):
    client = _client()
    row = _make_plugin()
    response = client.get(f"/admin/plugins/{row.id}/package")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert ".bidplugin.zip" in disposition
    assert "filename*=UTF-8''" in disposition

    manifest = _manifest_from_zip(response.content)
    payload = next(item for item in client.get("/admin/plugins").json() if item["id"] == row.id)
    for key in IMPORTABLE:
        assert manifest[key] == payload[key], key
    assert manifest["format"] == plugin_pack.FORMAT
    assert manifest["version"] == plugin_pack.FORMAT_VERSION

    assert client.get("/admin/plugins/missing/package").status_code == 404
    # 静态路径不被 {plugin_id} 吞掉。
    assert client.get("/admin/plugins/catalog").status_code == 200


def test_download_ascii_filename_for_ascii_name(db):
    client = _client()
    row = _make_plugin(name="fetch-pack")
    response = client.get(f"/admin/plugins/{row.id}/package")
    assert response.status_code == 200
    assert 'filename="fetch-pack.bidplugin.zip"' in response.headers["content-disposition"]


def test_package_routes_require_super_admin(db):
    admin = _client()
    row = _make_plugin()
    viewer = _viewer_client(admin)
    assert viewer.get(f"/admin/plugins/{row.id}/package").status_code == 403
    response = viewer.post(
        "/admin/plugins/import",
        files={"file": ("pkg.zip", _pack(_base_manifest()), "application/zip")},
    )
    assert response.status_code == 403


def test_import_writes_audit_entry(db):
    from sqlalchemy import select

    from bidcraft.models import AuditLog

    client = _client()
    created = _upload(client, _pack(_base_manifest(name="审计包")))
    assert created.status_code == 201
    updated = _upload(client, _pack(_base_manifest(name="审计包", command="uvx")))
    assert updated.status_code == 200

    with session_scope() as session:
        entries = list(
            session.scalars(select(AuditLog).where(AuditLog.action == "plugin.import"))
        )
    assert len(entries) == 2
    assert entries[0].detail["name"] == "审计包"
    assert entries[0].detail["created"] is True
    assert entries[0].detail["transport"] == "stdio"
    assert entries[1].detail["updated"] is True
    assert entries[1].detail["plugin_id"] == updated.json()["id"]
