"""插件包 Ed25519 可选签名：导出/验证、防篡改、fail-closed 与作用域字段。"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from bidcraft import plugin_pack
from bidcraft.db import session_scope
from bidcraft.models import Plugin, User


@pytest.fixture(autouse=True)
def _isolated_db(db):
    yield db


def _key_pair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    return private_pem, public_pem


def _configure_keys(monkeypatch, *, trusted: bool = True) -> tuple[str, str]:
    private_pem, public_pem = _key_pair()
    monkeypatch.setenv(plugin_pack.PRIVATE_KEY_ENV, private_pem)
    monkeypatch.delenv(plugin_pack.TRUSTED_KEYS_ENV, raising=False)
    if trusted:
        monkeypatch.setenv(plugin_pack.TRUSTED_KEYS_ENV, public_pem)
    monkeypatch.delenv(plugin_pack.REQUIRE_SIGNATURE_ENV, raising=False)
    return private_pem, public_pem


def _make_row(**overrides) -> dict[str, object]:
    fields = {
        "name": "签名插件",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "server"],
        "env": {"LOG_LEVEL": "error"},
        "url": "",
        "enabled": True,
        "tool_allowlist": ["read_file"],
    }
    fields.update(overrides)
    with session_scope() as db:
        row = Plugin(**fields)
        db.add(row)
        db.flush()
        return {"id": row.id}


def _tamper(data: bytes, change: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            manifest = json.loads(source.read(plugin_pack.MANIFEST_PATH))
        manifest.update(change)
        bundle.writestr(plugin_pack.MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False))
    return buffer.getvalue()


def test_export_signs_and_parse_verifies(monkeypatch):
    _configure_keys(monkeypatch)
    row_id = _make_row()["id"]
    with session_scope() as db:
        row = db.get(Plugin, row_id)
        data = plugin_pack.export_package(row)
    manifest = plugin_pack.parse_package(data)
    assert manifest[plugin_pack.SIGNATURE_KEY]["alg"] == plugin_pack.SIGNATURE_ALG
    assert plugin_pack.verify_manifest_signature(manifest) == "signed"


def test_tampered_package_rejected(monkeypatch):
    _configure_keys(monkeypatch)
    row_id = _make_row()["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    tampered = _tamper(data, {"command": "python -m evil"})
    with pytest.raises(plugin_pack.PluginPackSignatureError):
        plugin_pack.parse_package(tampered)


def test_unsigned_accepted_by_default(monkeypatch):
    monkeypatch.delenv(plugin_pack.PRIVATE_KEY_ENV, raising=False)
    monkeypatch.delenv(plugin_pack.TRUSTED_KEYS_ENV, raising=False)
    monkeypatch.delenv(plugin_pack.REQUIRE_SIGNATURE_ENV, raising=False)
    row_id = _make_row()["id"]
    with session_scope() as db:
        # 没有签名私钥时导出 unsigned（兼容旧包）。
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        exported = json.loads(bundle.read(plugin_pack.MANIFEST_PATH))
    assert plugin_pack.SIGNATURE_KEY not in exported
    assert plugin_pack.parse_package(data)["name"] == "签名插件"


def test_unsigned_rejected_when_required(monkeypatch):
    monkeypatch.delenv(plugin_pack.PRIVATE_KEY_ENV, raising=False)
    monkeypatch.delenv(plugin_pack.TRUSTED_KEYS_ENV, raising=False)
    row_id = _make_row()["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    with pytest.raises(plugin_pack.PluginPackSignatureError):
        plugin_pack.parse_package(data, require_signature=True)
    monkeypatch.setenv(plugin_pack.REQUIRE_SIGNATURE_ENV, "1")
    with pytest.raises(plugin_pack.PluginPackSignatureError):
        plugin_pack.parse_package(data)


def test_signed_package_without_trusted_keys_fails_closed(monkeypatch):
    _configure_keys(monkeypatch, trusted=False)
    row_id = _make_row()["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    with pytest.raises(plugin_pack.PluginPackSignatureError, match="信任"):
        plugin_pack.parse_package(data)


def test_wrong_trusted_key_rejected(monkeypatch):
    _configure_keys(monkeypatch)
    _, other_public = _key_pair()
    monkeypatch.setenv(plugin_pack.TRUSTED_KEYS_ENV, other_public)
    row_id = _make_row()["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    with pytest.raises(plugin_pack.PluginPackSignatureError):
        plugin_pack.parse_package(data)


def test_scope_fields_roundtrip(monkeypatch):
    _configure_keys(monkeypatch)
    with session_scope() as db:
        owner = User(username="pack-owner", display_name="包主人", password_hash="x", role="manager")
        db.add(owner)
        db.flush()
        owner_id = owner.id
    row_id = _make_row(scope="owner", owner_id=owner_id, allow_private_net=True)["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    manifest = plugin_pack.parse_package(data)
    assert manifest["scope"] == "owner"
    assert manifest["owner_id"] == owner_id
    assert manifest["allow_private_net"] is True
    normalized = plugin_pack.validate_manifest(manifest)
    assert normalized["scope"] == "owner"
    assert normalized["owner_id"] == owner_id
    assert normalized["allow_private_net"] is True
    with session_scope() as db:
        plugin_pack.apply_manifest(db, normalized, on_conflict="replace")
        imported = db.scalar(select(Plugin).where(Plugin.name == "签名插件"))
        assert imported is not None
        assert imported.scope == "owner"
        assert imported.owner_id == owner_id
        assert imported.allow_private_net is True


def test_trusted_keys_file(monkeypatch, tmp_path: Path):
    private_pem, public_pem = _key_pair()
    monkeypatch.setenv(plugin_pack.PRIVATE_KEY_ENV, private_pem)
    monkeypatch.delenv(plugin_pack.TRUSTED_KEYS_ENV, raising=False)
    monkeypatch.setattr(plugin_pack.settings, "home", tmp_path)
    (tmp_path / plugin_pack.TRUSTED_KEYS_FILE).write_text(
        json.dumps([public_pem]), encoding="utf-8"
    )
    row_id = _make_row()["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    assert plugin_pack.parse_package(data)["name"] == "签名插件"


def test_admin_import_require_signature_query(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    monkeypatch.delenv(plugin_pack.PRIVATE_KEY_ENV, raising=False)
    monkeypatch.delenv(plugin_pack.TRUSTED_KEYS_ENV, raising=False)
    row_id = _make_row()["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    client = TestClient(create_app())
    assert client.post("/auth/login", json={"username": "admin", "password": "test-admin"}).status_code == 200
    response = client.post(
        "/admin/plugins/import?require_signature=true",
        files={"file": ("unsigned.bidplugin", data, "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "签名" in response.text


def test_base64_trusted_key_supported(monkeypatch):
    private_pem, _ = _key_pair()
    monkeypatch.setenv(plugin_pack.PRIVATE_KEY_ENV, private_pem)
    private = plugin_pack.signing_private_key()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    monkeypatch.setenv(
        plugin_pack.TRUSTED_KEYS_ENV, base64.b64encode(raw).decode()
    )
    row_id = _make_row()["id"]
    with session_scope() as db:
        data = plugin_pack.export_package(db.get(Plugin, row_id))
    assert plugin_pack.parse_package(data)["name"] == "签名插件"
