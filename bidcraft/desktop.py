"""BidCraft 桌面网关：本地反代远端服务器 + 本地文件桥。

仅使用标准库（``http.server`` / ``urllib``），双击启动后浏览器访问
``http://127.0.0.1:<port>/`` 即可获得与服务端同源的客户端体验，
并可在网页中选择本地文件直接上传，无需 scp。
"""

from __future__ import annotations

import html
import json
import mimetypes
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .client import BidCraftClient, ClientError

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PICKER_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".md",
    ".xls",
    ".xlsx",
    ".csv",
    ".ppt",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
}
DEFAULT_SPA_DIR = Path(__file__).resolve().parent / "web_dist"

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

PROXY_PREFIXES = (
    "/auth",
    "/projects",
    "/jobs",
    "/batch-jobs",
    "/agents",
    "/packages",
    "/enterprise",
    "/admin",
    "/approvals",
    "/billing",
    "/account",
    "/legal",
    "/my",
    "/docs",
    "/redoc",
)
PROXY_PATHS = {"/health", "/openapi.json"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class _GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        upstream: str,
        spa_dir: Path,
        token: str,
        *,
        proxy_enabled: bool = True,
    ):
        super().__init__(address, _GatewayHandler)
        self.upstream = upstream.rstrip("/")
        self.spa_dir = Path(spa_dir)
        self.token = token
        self.proxy_enabled = proxy_enabled


class _GatewayHandler(BaseHTTPRequestHandler):
    server: _GatewayServer
    server_version = f"BidCraftDesktop/{__version__}"

    # ------------------------------------------------------------------ helpers
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib 签名
        return

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
        *,
        head_only: bool = False,
    ) -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _token_ok(self) -> bool:
        supplied = self.headers.get("X-Client-Token", "")
        if not supplied or not secrets.compare_digest(supplied, self.server.token):
            self._send_json(403, {"detail": "客户端令牌无效"})
            return False
        return True

    @staticmethod
    def _is_api(path: str) -> bool:
        if path in PROXY_PATHS:
            return True
        return any(path == prefix or path.startswith(prefix + "/") for prefix in PROXY_PREFIXES)

    def _wants_html(self) -> bool:
        """浏览器/窗口导航（Accept: text/html）应始终落到 SPA，即便路径与 API 前缀同名。"""
        accept = self.headers.get("Accept", "")
        return "text/html" in accept.lower()

    # ------------------------------------------------------------------ entry
    def do_GET(self) -> None:  # noqa: N802 - stdlib 命名
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        method = self.command.upper()
        if path == "/__client/token.js" and method in {"GET", "HEAD"}:
            self._token_js(head_only=method == "HEAD")
            return
        if path == "/__client/info" and method in {"GET", "HEAD"}:
            self._info(head_only=method == "HEAD")
            return
        if path == "/__client/picker" and method in {"GET", "HEAD"}:
            self._picker(head_only=method == "HEAD")
            return
        if path == "/__client/picker-upload" and method == "POST":
            self._picker_upload()
            return
        if path == "/__client/upload" and method == "POST":
            self._upload()
            return
        if path.startswith("/__client/"):
            self._send_json(404, {"detail": "未知的客户端接口"})
            return
        if not self.server.proxy_enabled:
            if method in {"GET", "HEAD"} and (
                not self._is_api(path) or self._wants_html()
            ) and self._serve_static(path, head_only=method == "HEAD"):
                return
            self._send_json(404, {"detail": "API 由客户端原生桥处理"})
            return
        if (
            method in {"GET", "HEAD"}
            and (not self._is_api(path) or self._wants_html())
            and self._serve_static(path, head_only=method == "HEAD")
        ):
            return
        self._proxy()

    # ------------------------------------------------------------------ client api
    def _token_js(self, *, head_only: bool = False) -> None:
        script = (
            f"window.__BIDCRAFT_CLIENT_TOKEN={json.dumps(self.server.token)};"
            f"window.__BIDCRAFT_SERVER={json.dumps(self.server.upstream)};"
        )
        self._send_bytes(
            200,
            script.encode("utf-8"),
            "application/javascript; charset=utf-8",
            {"Cache-Control": "no-store"},
            head_only=head_only,
        )

    def _info(self, *, head_only: bool = False) -> None:
        if not self._token_ok():
            return
        payload = {"server": self.server.upstream, "version": __version__}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            200,
            body,
            "application/json; charset=utf-8",
            {"Cache-Control": "no-store"},
            head_only=head_only,
        )

    def _upload(self) -> None:
        if not self._token_ok():
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send_json(400, {"detail": "请求体长度无效"})
            return
        if length <= 0:
            self._send_json(400, {"detail": "缺少请求体"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_json(400, {"detail": "请求体必须是 JSON"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"detail": "请求体必须是 JSON 对象"})
            return

        status, response_type, response_body = self._forward_upload(payload)
        self._relay(status, response_type, response_body)

    @staticmethod
    def _json_error(status: int, detail: str) -> tuple[int, str, bytes]:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        return status, "application/json; charset=utf-8", body

    def _forward_upload(self, payload: dict[str, Any]) -> tuple[int, str, bytes]:
        local_path = payload.get("path")
        if not isinstance(local_path, str) or not local_path.strip():
            return self._json_error(400, "缺少本地文件路径")
        kind = payload.get("kind") or "doc"
        if kind not in {"doc", "project_source"}:
            return self._json_error(400, "kind 仅支持 doc 或 project_source")
        project = payload.get("project") or ""
        if kind == "project_source" and (not isinstance(project, str) or not project.strip()):
            return self._json_error(400, "项目原件上传需要 project 参数")

        local = Path(local_path).expanduser()
        if not local.is_file():
            return self._json_error(404, f"本地文件不存在：{local}")
        size = local.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            return self._json_error(413, "文件不能超过 50MB")
        data = local.read_bytes()

        if kind == "doc":
            target_path = "/my/docs"
        else:
            target_path = f"/projects/{urllib.parse.quote(project, safe='')}/sources"

        content_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        boundary = "----BidCraftBoundary" + secrets.token_hex(16)
        safe_name = local.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        body = head + data + tail

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        cookie = self.headers.get("Cookie")
        if cookie:
            headers["Cookie"] = cookie

        return self._call_upstream("POST", target_path, headers, body)

    # ------------------------------------------------------------------ file picker
    def _picker_token_ok(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(token, self.server.token)

    def _picker(self, *, head_only: bool = False) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        token = (query.get("t") or [""])[0]
        if not self._picker_token_ok(token):
            self._send_json(403, {"detail": "客户端令牌无效，请从「我的文档」重新打开"})
            return
        kind = (query.get("kind") or ["doc"])[0]
        if kind not in {"doc", "project_source"}:
            kind = "doc"
        project = (query.get("project") or [""])[0]
        raw_dir = (query.get("dir") or [""])[0]
        directory = Path(raw_dir).expanduser() if raw_dir else Path.home()
        if not directory.is_dir():
            directory = Path.home()

        entries: list[tuple[str, Path]] = []
        files: list[tuple[str, Path]] = []
        try:
            for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    entries.append((child.name, child))
                elif child.is_file() and child.suffix.lower() in PICKER_SUFFIXES:
                    files.append((child.name, child))
                if len(entries) + len(files) >= 400:
                    break
        except OSError as exc:
            self._send_json(400, {"detail": f"无法读取目录：{exc}"})
            return

        def link(target: Path) -> str:
            return (
                "/__client/picker?t="
                + urllib.parse.quote(token, safe="")
                + "&kind="
                + urllib.parse.quote(kind, safe="")
                + "&project="
                + urllib.parse.quote(project, safe="")
                + "&dir="
                + urllib.parse.quote(str(target), safe="")
            )

        rows: list[str] = []
        parent = directory.parent
        if parent != directory:
            rows.append(f'<li><a href="{html.escape(link(parent))}">⬆ 上一级</a></li>')
        for name, child in entries:
            rows.append(
                f'<li><a href="{html.escape(link(child))}">📁 {html.escape(name)}/</a></li>'
            )
        for name, child in files:
            size_kb = child.stat().st_size / 1024
            rows.append(
                "<li><form method='post' action='/__client/picker-upload'>"
                f"<input type='hidden' name='t' value='{html.escape(token)}'>"
                f"<input type='hidden' name='kind' value='{html.escape(kind)}'>"
                f"<input type='hidden' name='project' value='{html.escape(project)}'>"
                f"<input type='hidden' name='path' value='{html.escape(str(child))}'>"
                f"<button type='submit'>上传 {html.escape(name)}</button>"
                f"<span class='meta'>{size_kb:.0f} KB</span></form></li>"
            )
        if not rows:
            rows.append("<li class='meta'>此目录没有可上传的文件</li>")

        scope = "个人知识库（我的文档）" if kind == "doc" else f"项目原件（{project}）"
        page = (
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<title>选择本机文件 · BidCraft</title>"
            "<style>body{font:14px/1.6 system-ui,sans-serif;margin:0;padding:20px;"
            "background:#faf9f6;color:#1d1b16}h1{font-size:16px;margin:0 0 4px}"
            ".meta,.hint{color:#6b6457;font-size:12px}ul{list-style:none;padding:0;margin:12px 0}"
            "li{padding:6px 0;border-bottom:1px solid #e6e1d6}a{color:#245c46;text-decoration:none}"
            "form{display:flex;align-items:center;gap:10px}button{cursor:pointer}"
            ".bar{margin-bottom:8px}</style>"
            f"<h1>选择本机文件上传到{html.escape(scope)}</h1>"
            f"<div class='hint'>{html.escape(str(directory))}</div>"
            f"<ul>{''.join(rows)}</ul>"
            "<p class='hint'>文件由本机客户端直接读取并上传，服务器不会访问你的本地路径。</p>"
            "</html>"
        )
        self._send_bytes(
            200,
            page.encode("utf-8"),
            "text/html; charset=utf-8",
            {"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
            head_only=head_only,
        )

    def _picker_upload(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
        if not self._picker_token_ok((form.get("t") or [""])[0]):
            self._send_html_page("上传失败", "客户端令牌无效，请关闭窗口后重试。", ok=False)
            return
        payload = {
            "path": (form.get("path") or [""])[0],
            "kind": (form.get("kind") or ["doc"])[0],
            "project": (form.get("project") or [""])[0],
        }
        status, _content_type, body = self._forward_upload(payload)
        detail = ""
        try:
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, dict):
                detail = str(parsed.get("detail") or "")
        except (UnicodeDecodeError, ValueError):
            detail = ""
        if 200 <= status < 300:
            self._send_html_page("上传成功", "文件已上传，索引将在后台完成。", ok=True)
        else:
            self._send_html_page("上传失败", detail or f"服务器返回 {status}", ok=False)

    def _send_html_page(self, title: str, message: str, *, ok: bool) -> None:
        color = "#245c46" if ok else "#9a3324"
        page = (
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title>"
            "<style>body{font:14px/1.7 system-ui,sans-serif;padding:32px;background:#faf9f6;"
            "color:#1d1b16;text-align:center}h1{font-size:17px;margin:0 0 8px}"
            "button{margin-top:18px;padding:6px 16px;cursor:pointer}</style>"
            f"<h1 style='color:{color}'>{html.escape(title)}</h1>"
            f"<p>{html.escape(message)}</p>"
            "<button onclick='window.close()'>关闭</button>"
            "<script>if(window.opener){window.opener.postMessage("
            + json.dumps({"type": "bidcraft-desktop-upload", "ok": ok})
            + ",'*')}</script></html>"
        )
        self._send_bytes(
            200,
            page.encode("utf-8"),
            "text/html; charset=utf-8",
            {"Cache-Control": "no-store"},
        )

    # ------------------------------------------------------------------ proxy
    def _call_upstream(
        self, method: str, target_path: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, str, bytes]:
        target = self.server.upstream + target_path
        request = urllib.request.Request(target, data=body, headers=headers, method=method)
        try:
            with _OPENER.open(request, timeout=120) as response:
                return (
                    response.status,
                    response.headers.get("Content-Type", ""),
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            payload = json.dumps({"detail": f"无法连接服务器：{reason}"}, ensure_ascii=False)
            return 502, "application/json; charset=utf-8", payload.encode("utf-8")

    def _relay(self, status: int, content_type: str, body: bytes) -> None:
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        self._send_bytes(status, body, "", headers)

    def _proxy(self) -> None:
        method = self.command.upper()
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        status, content_type, response_body, response_headers = self._call_upstream_full(
            method, self.path, headers, body
        )
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for key, value in response_headers:
            self.send_header(key, value)
        if status not in {204, 304}:
            self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if method != "HEAD" and response_body:
            self.wfile.write(response_body)

    def _call_upstream_full(
        self,
        method: str,
        target_path: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, str, bytes, list[tuple[str, str]]]:
        target = self.server.upstream + target_path
        request = urllib.request.Request(
            target,
            data=body if method not in {"GET", "HEAD"} else None,
            headers=headers,
            method=method,
        )
        try:
            with _OPENER.open(request, timeout=120) as response:
                return (
                    response.status,
                    response.headers.get("Content-Type", ""),
                    response.read(),
                    self._response_headers(response.headers),
                )
        except urllib.error.HTTPError as exc:
            return (
                exc.code,
                exc.headers.get("Content-Type", ""),
                exc.read(),
                self._response_headers(exc.headers),
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            payload = json.dumps({"detail": f"无法连接服务器：{reason}"}, ensure_ascii=False)
            return 502, "application/json; charset=utf-8", payload.encode("utf-8"), []

    @staticmethod
    def _response_headers(headers: Any) -> list[tuple[str, str]]:
        skip = HOP_BY_HOP | {"content-type"}
        return [(key, value) for key, value in headers.items() if key.lower() not in skip]

    # ------------------------------------------------------------------ static
    def _serve_static(self, path: str, *, head_only: bool = False) -> bool:
        root = self.server.spa_dir
        index = root / "index.html"
        if not index.is_file():
            return False
        root_resolved = root.resolve()
        relative = urllib.parse.unquote(path.lstrip("/"))
        candidate = (root / relative).resolve()
        if candidate.is_file() and (candidate == root_resolved or root_resolved in candidate.parents):
            self._serve_file(candidate, head_only=head_only)
            return True
        self._serve_file(index, head_only=head_only)
        return True

    def _serve_file(self, path: Path, *, head_only: bool = False) -> None:
        data = path.read_bytes()
        if path.name == "index.html":
            marker = b"</head>"
            injection = b'<script src="/__client/token.js"></script></head>'
            if marker in data:
                data = data.replace(marker, injection, 1)
            else:
                data = data.replace(
                    b"</body>", b'<script src="/__client/token.js"></script></body>', 1
                )
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        extra = {"Cache-Control": "no-cache"} if path.name == "index.html" else {}
        self._send_bytes(200, data, content_type, extra, head_only=head_only)


def start_gateway(
    upstream: str,
    *,
    port: int = 0,
    spa_dir: str | Path = DEFAULT_SPA_DIR,
    token: str,
    proxy_enabled: bool = True,
) -> ThreadingHTTPServer:
    """创建（但不启动）本地静态服务/网关，返回 ThreadingHTTPServer。"""
    if not upstream.startswith(("http://", "https://")):
        raise ValueError("upstream 必须以 http:// 或 https:// 开头")
    return _GatewayServer(
        ("127.0.0.1", port), upstream, Path(spa_dir), token, proxy_enabled=proxy_enabled
    )


def run_desktop(server: str, *, port: int = 0, open_browser: bool = True) -> None:
    """启动桌面网关并阻塞到 Ctrl+C。"""
    token = secrets.token_urlsafe(24)
    httpd = start_gateway(server, port=port, spa_dir=DEFAULT_SPA_DIR, token=token)
    host, actual_port = httpd.server_address[:2]
    url = f"http://{host}:{actual_port}/"
    thread = threading.Thread(
        target=httpd.serve_forever, name="bidcraft-desktop-gateway", daemon=True
    )
    thread.start()
    print(f"BidCraft 桌面网关已启动：{url}（本地文件桥已启用）", flush=True)
    print(f"上游服务器：{server}", flush=True)
    print(f"客户端令牌：{token}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        httpd.shutdown()
        httpd.server_close()


class _DesktopApi:
    """暴露给窗口 JS 的原生桥：所有 C/S 通信直连 REST API。

    GET 失败时回放本机只读缓存，并把 ``offline`` 标志带回前端。
    """

    def __init__(self, server: str) -> None:
        self.client = BidCraftClient(server)

    def info(self) -> dict[str, Any]:
        return {"server": self.client.server, "version": __version__, "bridge": "api"}

    def api_request(
        self,
        method: str,
        path: str,
        body_b64: str = "",
        content_type: str = "",
    ) -> dict[str, Any]:
        return self.client.bridge_request(
            method, path, body_b64=body_b64, content_type=content_type
        )

    def pick_and_upload(self, kind: str = "doc", project: str = "") -> dict[str, Any]:
        """原生文件对话框选择本机文件并直接上传。"""
        try:
            import webview
        except ImportError:  # pragma: no cover - 打包环境必有
            return {"ok": False, "error": "当前环境不支持原生文件选择"}
        windows = getattr(webview, "windows", [])
        if not windows:
            return {"ok": False, "error": "窗口尚未就绪"}
        try:
            selected = windows[0].create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        except Exception as exc:  # noqa: BLE001 - 平台差异
            return {"ok": False, "error": f"文件选择失败：{exc}"}
        if not selected:
            return {"ok": False, "cancelled": True}
        path = selected[0]
        try:
            if kind == "project_source":
                payload = self.client.upload_source(project, path)
            else:
                payload = self.client.upload_doc(path)
        except ClientError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "payload": payload, "path": path}


def run_window(
    server: str,
    *,
    port: int = 0,
    width: int = 1280,
    height: int = 860,
    api: _DesktopApi | None = None,
) -> None:
    """原生窗口模式：本地静态服务 + pywebview 窗口 + API 桥。"""
    try:
        import webview
    except ImportError:
        print("未安装 pywebview，回退到浏览器模式（uv pip install pywebview）", flush=True)
        run_desktop(server, port=port, open_browser=True)
        return

    token = secrets.token_urlsafe(24)
    httpd = start_gateway(server, port=port, spa_dir=DEFAULT_SPA_DIR, token=token, proxy_enabled=False)
    host, actual_port = httpd.server_address[:2]
    url = f"http://{host}:{actual_port}/"
    thread = threading.Thread(target=httpd.serve_forever, name="bidcraft-desktop-static", daemon=True)
    thread.start()

    bridge = api or _DesktopApi(server)
    print(f"BidCraft 客户端已启动：{url}", flush=True)
    print(f"上游服务器：{server}（API 直连）", flush=True)
    try:
        webview.create_window(
            "BidCraft 投标工作台",
            url,
            width=width,
            height=height,
            min_size=(960, 640),
            js_api=bridge,
        )
        webview.start()
    except Exception as exc:  # noqa: BLE001 - 平台缺少 WebView 时回退
        httpd.shutdown()
        httpd.server_close()
        print(f"原生窗口不可用（{exc}），回退浏览器模式", flush=True)
        print("Linux 需安装 PySide6 或 python3-gi + WebKitGTK；Windows/macOS 开箱即用", flush=True)
        run_desktop(server, port=actual_port, open_browser=True)
    finally:
        httpd.shutdown()
