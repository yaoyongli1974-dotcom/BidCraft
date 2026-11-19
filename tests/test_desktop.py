from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import httpx
import pytest

from bidcraft import desktop

TOKEN = "test-client-token"


class _UpstreamHandler(BaseHTTPRequestHandler):
    records: ClassVar[list[dict]] = []

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib 签名
        return

    def _send(self, status: int, payload: dict, content_type: str = "application/json") -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self.records.append(
            {
                "method": "GET",
                "path": self.path,
                "cookie": self.headers.get("Cookie", ""),
                "host": self.headers.get("Host", ""),
            }
        )
        if self.path == "/health":
            self._send(
                200,
                {
                    "ok": True,
                    "version": "9.9.9",
                    "cookie": self.headers.get("Cookie", ""),
                    "host": self.headers.get("Host", ""),
                },
            )
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        self.records.append(
            {
                "method": "POST",
                "path": self.path,
                "cookie": self.headers.get("Cookie", ""),
                "content_type": self.headers.get("Content-Type", ""),
                "body": body,
            }
        )
        if self.path == "/my/docs":
            self._send(202, {"doc": {"id": "doc-1", "filename": "note.txt"}, "job_id": "job-1"})
            return
        if self.path.startswith("/projects/") and self.path.endswith("/sources"):
            self._send(200, {"project": {"id": "proj-1"}, "ledger_summary": {}, "notes": []})
            return
        self._send(404, {"detail": "not found"})


@pytest.fixture()
def upstream():
    _UpstreamHandler.records = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(5)


@pytest.fixture()
def gateway(upstream, tmp_path):
    spa = tmp_path / "web"
    spa.mkdir()
    (spa / "index.html").write_text("<html>desktop-spa</html>", encoding="utf-8")
    (spa / "app.js").write_text("console.log('ok');", encoding="utf-8")
    httpd = desktop.start_gateway(upstream, port=0, spa_dir=spa, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(5)


def _get(base_url: str, path: str, **kwargs):
    with httpx.Client(base_url=base_url, timeout=10) as client:
        return client.get(path, **kwargs)


def _post(base_url: str, path: str, **kwargs):
    with httpx.Client(base_url=base_url, timeout=10) as client:
        return client.post(path, **kwargs)


def test_token_js(gateway, upstream):
    response = _get(gateway, "/__client/token.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "no-store"
    assert TOKEN in response.text
    assert upstream in response.text
    assert "window.__BIDCRAFT_CLIENT_TOKEN" in response.text
    assert "window.__BIDCRAFT_SERVER" in response.text


def test_info_requires_token(gateway, upstream):
    assert _get(gateway, "/__client/info").status_code == 403
    response = _get(gateway, "/__client/info", headers={"X-Client-Token": TOKEN})
    assert response.status_code == 200
    body = response.json()
    assert body["server"] == upstream
    assert body["version"]


def test_proxy_get_preserves_cookie_and_rewrites_host(gateway, upstream):
    response = _get(gateway, "/health", headers={"Cookie": "bidcraft_session=abc"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["cookie"] == "bidcraft_session=abc"
    assert body["host"] == upstream.split("://", 1)[1]
    record = _UpstreamHandler.records[-1]
    assert record["path"] == "/health"
    assert record["cookie"] == "bidcraft_session=abc"


def test_static_files_and_spa_fallback(gateway):
    index = _get(gateway, "/")
    assert index.status_code == 200
    assert "desktop-spa" in index.text

    asset = _get(gateway, "/app.js")
    assert "console.log" in asset.text
    assert "javascript" in asset.headers["content-type"]

    fallback = _get(gateway, "/dashboard/settings")
    assert "desktop-spa" in fallback.text

    missing = _get(gateway, "/missing.png")
    assert "desktop-spa" in missing.text


def test_api_prefix_never_hits_static(gateway, upstream):
    response = _get(gateway, "/projects")
    assert response.status_code == 404
    assert "desktop-spa" not in response.text
    assert _UpstreamHandler.records[-1]["path"] == "/projects"


def test_upload_requires_token_and_path(gateway):
    local = __file__
    assert (
        _post(gateway, "/__client/upload", json={"path": local}).status_code == 403
    )
    response = _post(
        gateway,
        "/__client/upload",
        headers={"X-Client-Token": TOKEN},
        json={},
    )
    assert response.status_code == 400
    assert "路径" in response.json()["detail"]


def test_upload_doc_forwards_multipart_and_cookie(gateway, upstream, tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("本地文件桥内容\n", encoding="utf-8")
    response = _post(
        gateway,
        "/__client/upload",
        headers={"X-Client-Token": TOKEN, "Cookie": "bidcraft_session=xyz"},
        json={"path": str(source), "kind": "doc"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == "job-1"

    record = _UpstreamHandler.records[-1]
    assert record["path"] == "/my/docs"
    assert record["cookie"] == "bidcraft_session=xyz"
    assert record["content_type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"' in record["body"]
    assert b'filename="note.txt"' in record["body"]
    assert "本地文件桥内容".encode() in record["body"]


def test_upload_project_source(gateway, upstream, tmp_path):
    source = tmp_path / "tender.txt"
    source.write_text("招标文件", encoding="utf-8")
    response = _post(
        gateway,
        "/__client/upload",
        headers={"X-Client-Token": TOKEN, "Cookie": "bidcraft_session=xyz"},
        json={"path": str(source), "kind": "project_source", "project": "proj-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["project"]["id"] == "proj-1"
    assert _UpstreamHandler.records[-1]["path"] == "/projects/proj-1/sources"


def test_missing_local_file_returns_404(gateway, tmp_path):
    response = _post(
        gateway,
        "/__client/upload",
        headers={"X-Client-Token": TOKEN},
        json={"path": str(tmp_path / "absent.txt"), "kind": "doc"},
    )
    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


def test_picker_requires_token_and_lists_files(gateway, tmp_path, monkeypatch):
    sample = tmp_path / "投标资料.txt"
    sample.write_text("本地文件内容", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    monkeypatch.setattr(desktop.Path, "home", classmethod(lambda cls: tmp_path))

    denied = httpx.get(f"{gateway}/__client/picker", params={"kind": "doc"}, timeout=10)
    assert denied.status_code == 403

    allowed = httpx.get(
        f"{gateway}/__client/picker",
        params={"t": TOKEN, "kind": "doc", "dir": str(tmp_path)},
        timeout=10,
    )
    assert allowed.status_code == 200
    assert "投标资料.txt" in allowed.text
    assert "/__client/picker-upload" in allowed.text
    assert "sub/" in allowed.text
    assert allowed.headers.get("X-Frame-Options") == "DENY"


def test_picker_upload_forwards_and_notifies_opener(gateway, upstream, tmp_path):
    sample = tmp_path / "本地上传.txt"
    sample.write_text("picker upload", encoding="utf-8")

    denied = httpx.post(
        f"{gateway}/__client/picker-upload",
        data={"path": str(sample), "kind": "doc"},
        timeout=10,
    )
    assert denied.status_code == 200
    assert "上传失败" in denied.text

    response = httpx.post(
        f"{gateway}/__client/picker-upload",
        data={"t": TOKEN, "path": str(sample), "kind": "doc"},
        timeout=10,
    )
    assert response.status_code == 200
    assert "上传成功" in response.text
    assert "bidcraft-desktop-upload" in response.text
    forwarded = [item for item in _UpstreamHandler.records if item["path"] == "/my/docs"]
    assert forwarded, _UpstreamHandler.records
    assert "multipart/form-data" in forwarded[-1]["content_type"]


def test_html_navigation_wins_over_api_prefix(gateway):
    # 浏览器/窗口导航（Accept: text/html）即便路径与 API 前缀同名也落到 SPA。
    for path in ("/billing", "/admin", "/app/billing"):
        response = _get(gateway, path, headers={"Accept": "text/html,application/xhtml+xml"})
        assert response.status_code == 200, path
        assert "desktop-spa" in response.text, path
    # 真正的 API 调用（JSON）仍然转发给上游。
    api = _get(gateway, "/billing/summary", headers={"Accept": "application/json"})
    assert api.status_code != 404 or "desktop-spa" not in api.text


def test_native_bridge_mode_spa_navigation(upstream, tmp_path):
    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text("<html>native-shell</html>", encoding="utf-8")
    httpd = desktop.start_gateway(upstream, port=0, spa_dir=spa, token=TOKEN, proxy_enabled=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        page = _get(base, "/app/billing", headers={"Accept": "text/html"})
        assert page.status_code == 200 and "native-shell" in page.text
        # 非导航的 API 请求在原生桥模式下明确拒绝，交给客户端桥处理。
        api = _get(base, "/projects", headers={"Accept": "application/json"})
        assert api.status_code == 404
        assert api.json()["detail"] == "API 由客户端原生桥处理"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
