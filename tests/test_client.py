from __future__ import annotations

import json
import stat
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")


def _wait_started(server, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not server.started and time.monotonic() < deadline:
        if server.should_exit:
            break
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("uvicorn 未能在超时内启动")


@pytest.fixture()
def live_server(db, monkeypatch):
    import uvicorn

    from bidcraft.api import create_app

    # 上传文档会触发知识库重建线程；测试中替换为空实现，避免与临时数据库竞争。
    monkeypatch.setattr("bidcraft.api._run_kb_reindex_job", lambda *a, **k: None)
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_started(server)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def _client(base_url: str, home: Path):
    from bidcraft.client import BidCraftClient

    return BidCraftClient(server=base_url, home=home)


def test_login_persists_cookies_and_config(live_server, tmp_path):
    from bidcraft.client import BidCraftClient

    home = tmp_path / "client-home"
    client = _client(live_server, home)
    user = client.login("admin", "test-admin")
    assert user["username"] == "admin"
    assert user["role"] == "super_admin"
    client.close()

    cookie_file = home / "cookies.json"
    assert cookie_file.is_file()
    assert stat.S_IMODE(cookie_file.stat().st_mode) == 0o600
    saved = json.loads(cookie_file.read_text(encoding="utf-8"))
    assert any(item["name"] == "bidcraft_session" for item in saved)

    config = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert config["server"] == live_server
    assert config["username"] == "admin"

    reused = BidCraftClient(home=home)
    try:
        assert reused.me()["username"] == "admin"
        assert reused.server == live_server
    finally:
        reused.close()


def test_login_rejects_bad_password(live_server, tmp_path):
    from bidcraft.client import ClientError

    client = _client(live_server, tmp_path / "bad-home")
    try:
        with pytest.raises(ClientError, match="用户名或密码错误"):
            client.login("admin", "wrong-password")
    finally:
        client.close()


def test_projects_listing_and_creation(live_server, tmp_path):
    client = _client(live_server, tmp_path / "projects-home")
    try:
        client.login("admin", "test-admin")
        assert client.projects() == []
        created = client.create_project("客户端项目", "weakcurrent-cabling")
        assert created["name"] == "客户端项目"
        rows = client.projects()
        assert [row["id"] for row in rows] == [created["id"]]
        assert client.health()["status"] == "ok"
    finally:
        client.close()


def test_upload_doc_download_and_delete(live_server, tmp_path):
    from bidcraft.config import settings

    home = tmp_path / "docs-home"
    source = tmp_path / "client-note.txt"
    source.write_text("客户端上传的知识库文档\n", encoding="utf-8")

    client = _client(live_server, home)
    try:
        client.login("admin", "test-admin")
        doc = client.upload_doc(source)
        assert doc["filename"] == source.name
        assert doc["size"] == source.stat().st_size
        stored = list((settings.home / "user_docs").rglob(source.name))
        assert stored, "上传文件未落盘"

        target = tmp_path / "downloads" / "note.txt"
        saved = client.download(f"/my/docs/{doc['id']}/file", target)
        assert saved == target
        assert saved.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

        deleted = client.delete_doc(doc["id"])
        assert deleted["deleted"] is True
    finally:
        client.close()


def test_upload_project_source(live_server, tmp_path, tender_path):
    client = _client(live_server, tmp_path / "source-home")
    try:
        client.login("admin", "test-admin")
        project = client.create_project("原件上传项目")
        result = client.upload_source(project["id"], tender_path)
        assert result["project"]["id"] == project["id"]
        assert "ledger_summary" in result
        assert result["notes"] is not None
    finally:
        client.close()


def test_missing_local_file_is_rejected(live_server, tmp_path):
    from bidcraft.client import ClientError

    client = _client(live_server, tmp_path / "missing-home")
    try:
        client.login("admin", "test-admin")
        with pytest.raises(ClientError, match="文件不存在"):
            client.upload_doc(tmp_path / "nope.txt")
    finally:
        client.close()


def test_load_config_prefers_file_then_env(tmp_path, monkeypatch):
    from bidcraft import client as client_module

    monkeypatch.setenv("BIDCRAFT_SERVER", "http://env.example/")
    config = client_module.load_config(tmp_path)
    assert config.server == "http://env.example"

    (tmp_path / "config.json").write_text(
        json.dumps({"server": "https://saved.example/", "username": "alice"}),
        encoding="utf-8",
    )
    config = client_module.load_config(tmp_path)
    assert config.server == "https://saved.example"
    assert config.username == "alice"


def test_desktop_requires_server(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from bidcraft import client as client_module
    from bidcraft.cli import app

    monkeypatch.setattr(client_module, "CLIENT_HOME", tmp_path)
    monkeypatch.delenv("BIDCRAFT_SERVER", raising=False)
    result = CliRunner().invoke(app, ["desktop", "--browser"])
    assert result.exit_code == 2
    assert "未指定服务器" in result.output


def test_register_requires_explicit_consent(live_server, tmp_path):
    """冻结客户端的 register() 不提交 accept_terms，注册必须被服务端拒绝。

    注册同意默认值的 P1 改造要求：缺省/False 一律拒绝，旧客户端不得靠默认同意兼容。
    桌面客户端已冻结（不再更新、不发安装包），不会补传 accept_terms，因此它注册失败
    是有意结果，而不是要改客户端来绕过。
    """
    from bidcraft.client import ClientError

    home = tmp_path / "client-home-reg"
    client = _client(live_server, home)
    try:
        with pytest.raises(ClientError, match="请先阅读并同意服务条款与隐私政策"):
            client.register("reg-user", "Passw0rd!123", "注册测试")
        # 被拒后不应建立会话：me() 仍是未登录。
        with pytest.raises(ClientError, match="未登录"):
            client.me()
    finally:
        client.close()


def test_logout_clears_session(live_server):
    from bidcraft.client import BidCraftClient, ClientError

    client = BidCraftClient(live_server)
    client.login("admin", "test-admin")
    assert client.me()["username"] == "admin"
    client.logout()
    assert not client.cookies_path.is_file()
    with pytest.raises(ClientError):
        client.me()
