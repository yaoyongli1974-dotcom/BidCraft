"""R30：Qt 原生外壳的纯逻辑测试（不依赖 GUI）。"""

from __future__ import annotations

from bidcraft import qtshell


def test_compare_version_orders_and_prefixes():
    assert qtshell.compare_version("0.6.1", "v0.6.2") == -1
    assert qtshell.compare_version("0.6.1", "0.6.1") == 0
    assert qtshell.compare_version("0.7.0", "0.6.9") == 1
    assert qtshell.compare_version("0.6", "0.6.0") == 0
    assert qtshell.compare_version("0.6.1", "") == 0
    assert qtshell.compare_version("abc", "0.6.1") == 0


def test_bridge_script_exposes_pywebview_api():
    source = qtshell.bridge_script("/* channel */")
    assert "QWebChannel" in source
    assert "pywebviewready" in source
    for name in ("info", "api_request", "pick_and_upload"):
        assert name in source
    assert "window.pywebview" in source
    assert "JSON.parse" in source


def test_css_script_hides_web_masthead():
    source = qtshell.css_script()
    assert ".masthead" in source
    assert "display: none" in source


def test_nav_items_cover_core_pages():
    paths = [path for _, path in qtshell.NAV_ITEMS]
    assert "/" in paths
    assert "/my-docs" in paths
    assert "/billing" in paths
    assert qtshell.ADMIN_NAV[1] == "/admin"


def test_settings_keys_stable():
    assert qtshell.KEY_SERVER == "client/server"
    assert qtshell.KEY_GEOMETRY == "window/geometry"
    assert qtshell.KEY_DOWNLOAD_DIR == "client/download_dir"


def test_run_qt_returns_false_without_qt(monkeypatch):
    monkeypatch.setattr(qtshell, "QT_AVAILABLE", False)
    assert qtshell.run_qt("http://127.0.0.1:8000") is False


def test_latest_version_swallows_errors(monkeypatch):
    import httpx

    def boom(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", boom)
    assert qtshell.latest_version() == ""


def test_latest_version_reads_tag(monkeypatch):
    import httpx

    class Response:
        status_code = 200

        def json(self):
            return {"tag_name": "v9.9.9"}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response())
    assert qtshell.latest_version() == "v9.9.9"


def test_native_page_helpers():
    from bidcraft import qtnative

    assert qtnative.human_size(0) == "0 B"
    assert qtnative.human_size(1536) == "1.5 KB"
    assert qtnative.human_size(5 * 1024 * 1024) == "5.0 MB"
    assert qtnative.doc_status_label("ready") == "可检索"
    assert qtnative.doc_status_label("failed") == "索引失败"
    assert qtnative.doc_status_label("other") == "other"
    assert "名称" in qtnative.project_columns()
    assert "文件名" in qtnative.doc_columns()
