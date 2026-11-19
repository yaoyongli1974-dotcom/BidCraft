import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from bidcraft.config import settings
from bidcraft.parsing import mineru
from bidcraft.parsing.loader import load_document

MARKDOWN = """# 招标文件

## 第一章 招标公告

项目名称：某某弱电工程
工期：90 日历天

| 序号 | 材料名称 | 单位 | 数量 |
| --- | --- | --- | --- |
| 1 | 六类非屏蔽双绞线 | m | 1200 |
| 2 | 网络摄像机 | 台 | 24 |

![figure](images/a.png)
"""


class _Handler(BaseHTTPRequestHandler):
    status = 200
    body: bytes = b""

    def do_POST(self) -> None:  # noqa: N802 - stdlib
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args) -> None:
        return


@pytest.fixture()
def mineru_server(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "mineru_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "mineru_enabled", True)
    yield server
    server.shutdown()
    server.server_close()


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "tender.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def test_available_requires_url(monkeypatch):
    monkeypatch.setattr(settings, "mineru_url", "")
    assert mineru.available() is False
    with pytest.raises(mineru.MineruUnavailable):
        mineru.parse_pdf("x.pdf")


def test_parse_maps_markdown_tables_and_pages(mineru_server, tmp_path):
    _Handler.status = 200
    _Handler.body = json.dumps({"md_content": MARKDOWN}).encode()
    doc = mineru.parse_pdf(_pdf(tmp_path))
    assert doc.meta["pdf_engine"] == "mineru"
    assert "项目名称" in doc.text
    assert doc.page_count >= 1
    assert len(doc.tables) == 1
    assert doc.tables[0].rows[0][:2] == ["序号", "材料名称"]
    # image syntax is stripped
    assert "![" not in doc.text


def test_parse_accepts_nested_data(mineru_server, tmp_path):
    _Handler.status = 200
    _Handler.body = json.dumps({"data": {"markdown": "正文内容"}}).encode()
    doc = mineru.parse_pdf(_pdf(tmp_path))
    assert "正文内容" in doc.text


def test_parse_reports_http_error(mineru_server, tmp_path):
    _Handler.status = 500
    _Handler.body = b'{"detail":"boom"}'
    with pytest.raises(mineru.MineruError):
        mineru.parse_pdf(_pdf(tmp_path))


def test_parse_reports_bad_json(mineru_server, tmp_path):
    _Handler.status = 200
    _Handler.body = b"not json"
    with pytest.raises(mineru.MineruError):
        mineru.parse_pdf(_pdf(tmp_path))


def test_parse_reports_empty_content(mineru_server, tmp_path):
    _Handler.status = 200
    _Handler.body = b"{}"
    with pytest.raises(mineru.MineruError):
        mineru.parse_pdf(_pdf(tmp_path))


def test_loader_prefers_mineru(mineru_server, tmp_path, monkeypatch):
    _Handler.status = 200
    _Handler.body = json.dumps({"md_content": MARKDOWN}).encode()
    doc = load_document(_pdf(tmp_path))
    assert doc.meta["pdf_engine"] == "mineru"
    assert "六类非屏蔽双绞线" in doc.text


def test_loader_falls_back_when_mineru_fails(mineru_server, tmp_path, monkeypatch):
    _Handler.status = 503
    _Handler.body = b'{"detail":"down"}'
    doc = load_document(_pdf(tmp_path))
    assert doc.meta.get("pdf_engine") != "mineru"
    assert "mineru_error" in doc.meta
