"""BidCraft 命令行/脚本客户端（纯客户端，不依赖服务端模块）。

配置文件与 Cookie 默认存放在 ``$BIDCRAFT_CLIENT_HOME`` 或
``~/.config/bidcraft-client``；Cookie 文件权限为 0600。
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import httpx

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0

from .offline_cache import OfflineCache


def _default_home() -> Path:
    raw = os.getenv("BIDCRAFT_CLIENT_HOME") or "~/.config/bidcraft-client"
    return Path(raw).expanduser()


CLIENT_HOME = _default_home()


class ClientError(RuntimeError):
    """客户端操作失败（网络、鉴权、服务端错误）。"""


@dataclass(slots=True)
class ClientConfig:
    server: str = ""
    username: str = ""
    home: Path = field(default_factory=_default_home)


def load_config(home: str | Path | None = None) -> ClientConfig:
    """读取已保存的客户端配置；server 缺省时回退到 ``BIDCRAFT_SERVER``。"""
    base = Path(home).expanduser().resolve() if home else CLIENT_HOME
    config = ClientConfig(home=base)
    path = base / "config.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            config.server = str(raw.get("server") or "")
            config.username = str(raw.get("username") or "")
    if not config.server:
        config.server = os.getenv("BIDCRAFT_SERVER", "").strip()
    config.server = config.server.rstrip("/")
    return config


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:  # pragma: no cover - 权限受限的文件系统
        pass
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - 权限受限的文件系统
        pass


class BidCraftClient:
    """基于 httpx 的轻量客户端，自动持久化登录 Cookie。"""

    def __init__(
        self,
        server: str | None = None,
        home: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.home = Path(home).expanduser().resolve() if home else CLIENT_HOME
        self.config_path = self.home / "config.json"
        self.cookies_path = self.home / "cookies.json"
        self.timeout = timeout
        self._config = self._load_config()
        self.server = (server or self._config.server or "").rstrip("/")
        self._username = self._config.username
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)
        self._saved_cookies = self._load_cookies()

    # ------------------------------------------------------------------ config
    @property
    def config(self) -> ClientConfig:
        return ClientConfig(server=self.server, username=self._username, home=self.home)

    def _load_config(self) -> ClientConfig:
        if not self.config_path.is_file():
            return ClientConfig(home=self.home)
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ClientConfig(home=self.home)
        if not isinstance(raw, dict):
            return ClientConfig(home=self.home)
        return ClientConfig(
            server=str(raw.get("server") or ""),
            username=str(raw.get("username") or ""),
            home=self.home,
        )

    def _save_config(self) -> None:
        payload = {"server": self.server, "username": self._username}
        _write_private(
            self.config_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    # ------------------------------------------------------------------ cookies
    def _read_cookie_file(self) -> list[dict[str, str]]:
        if not self.cookies_path.is_file():
            return []
        try:
            raw = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if isinstance(raw, dict):
            raw = [{"name": k, "value": v} for k, v in raw.items()]
        if not isinstance(raw, list):
            return []
        cookies: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": str(item.get("domain") or ""),
                    "path": str(item.get("path") or "/"),
                }
            )
        return cookies

    def _load_cookies(self) -> list[dict[str, str]]:
        cookies = self._read_cookie_file()
        for item in cookies:
            self._http.cookies.set(
                item["name"], item["value"], domain=item["domain"], path=item["path"]
            )
        return cookies

    def _current_cookies(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for cookie in self._http.cookies.jar:
            result.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain or "",
                    "path": cookie.path or "/",
                }
            )
        return result

    def _persist_cookies(self) -> None:
        current = self._current_cookies()
        if current == self._saved_cookies:
            return
        self._saved_cookies = current
        if not current:
            if self.cookies_path.is_file():
                self.cookies_path.unlink()
            return
        _write_private(
            self.cookies_path, json.dumps(current, ensure_ascii=False, indent=2) + "\n"
        )

    # ------------------------------------------------------------------ http
    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if not self.server:
            raise ClientError("尚未配置服务器，请先运行：bidcraft client login --server URL")
        return f"{self.server}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, self._url(path), **kwargs)
        except httpx.HTTPError as exc:
            raise ClientError(f"无法连接服务器：{exc}") from exc
        self._persist_cookies()
        return response

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            text = response.text.strip()
            return text[:200] if text else response.reason_phrase
        if isinstance(body, dict):
            detail = body.get("detail")
            if detail:
                return str(detail)
        return json.dumps(body, ensure_ascii=False)[:200]

    def _fail(self, response: httpx.Response, action: str) -> None:
        raise ClientError(f"{action}失败（HTTP {response.status_code}）：{self._detail(response)}")

    # ------------------------------------------------------------------ api
    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/health")
        if response.status_code >= 400:
            self._fail(response, "读取服务状态")
        body = response.json()
        return body if isinstance(body, dict) else {"ok": True, "body": body}

    def login(self, username: str, password: str) -> dict[str, Any]:
        response = self._http.post(  # 登录前无需持久化旧 cookie，直接提交
            self._url("/auth/login"),
            json={"username": username, "password": password},
        )
        self._persist_cookies()
        if response.status_code == 401:
            raise ClientError("用户名或密码错误")
        if response.status_code >= 400:
            self._fail(response, "登录")
        body = response.json() if response.content else {}
        user = body.get("user") if isinstance(body, dict) else None
        if not isinstance(user, dict):
            user = {}
        self._username = str(user.get("username") or username)
        self._save_config()
        return user

    def register(
        self, username: str, password: str, display_name: str = ""
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"username": username, "password": password}
        if display_name:
            payload["display_name"] = display_name
        response = self._http.post(  # 注册成功即建立会话
            self._url("/auth/register"),
            json=payload,
        )
        self._persist_cookies()
        if response.status_code >= 400:
            self._fail(response, "注册")
        body = response.json() if response.content else {}
        user = body.get("user") if isinstance(body, dict) else None
        if not isinstance(user, dict):
            user = {}
        self._username = str(user.get("username") or username)
        self._save_config()
        return user

    def me(self) -> dict[str, Any]:
        response = self._request("GET", "/auth/me")
        if response.status_code == 401:
            raise ClientError("未登录或登录已过期，请先运行：bidcraft client login --server URL")
        if response.status_code >= 400:
            self._fail(response, "读取当前用户")
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("user"), dict):
            return body["user"]
        return body if isinstance(body, dict) else {}

    def projects(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/projects")
        if response.status_code == 401:
            raise ClientError("未登录或登录已过期，请先运行：bidcraft client login --server URL")
        if response.status_code >= 400:
            self._fail(response, "读取项目列表")
        body = response.json()
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if isinstance(body, dict):
            for key in ("items", "projects"):
                value = body.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def create_project(self, name: str, package: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if package:
            payload["package"] = package
        response = self._request("POST", "/projects", json=payload)
        if response.status_code >= 400:
            self._fail(response, "创建项目")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def _check_local_file(self, path: str | Path) -> Path:
        local = Path(path).expanduser()
        if not local.is_file():
            raise ClientError(f"文件不存在：{local}")
        size = local.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ClientError("文件不能超过 50MB")
        return local

    def upload_doc(self, path: str | Path) -> dict[str, Any]:
        local = self._check_local_file(path)
        with local.open("rb") as handle:
            response = self._request(
                "POST",
                "/my/docs",
                files={"file": (local.name, handle, "application/octet-stream")},
            )
        if response.status_code >= 400:
            self._fail(response, "上传文档")
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("doc"), dict):
            return body["doc"]
        return body if isinstance(body, dict) else {}

    def list_docs(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/my/docs")
        if response.status_code == 401:
            raise ClientError("未登录或登录已过期，请先登录")
        if response.status_code >= 400:
            self._fail(response, "读取文档列表")
        body = response.json()
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if isinstance(body, dict):
            for key in ("docs", "items"):
                value = body.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def delete_project(self, project_id: str) -> dict[str, Any]:
        response = self._request("DELETE", f"/projects/{project_id}")
        if response.status_code >= 400:
            self._fail(response, "删除项目")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def billing(self) -> dict[str, Any]:
        response = self._request("GET", "/billing/summary")
        if response.status_code >= 400:
            self._fail(response, "读取账单")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def redeem(self, code: str) -> dict[str, Any]:
        response = self._request("POST", "/billing/redeem", json={"code": code})
        if response.status_code >= 400:
            self._fail(response, "兑换")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def delete_doc(self, doc_id: str) -> dict[str, Any]:
        response = self._request("DELETE", f"/my/docs/{doc_id}")
        if response.status_code >= 400:
            self._fail(response, "删除文档")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def upload_source(self, project_ref: str, path: str | Path) -> dict[str, Any]:
        local = self._check_local_file(path)
        with local.open("rb") as handle:
            response = self._request(
                "POST",
                f"/projects/{project_ref}/sources",
                files={"file": (local.name, handle, "application/octet-stream")},
            )
        if response.status_code >= 400:
            self._fail(response, "上传项目原件")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def bridge_request(
        self,
        method: str,
        path: str,
        *,
        body_b64: str = "",
        content_type: str = "",
    ) -> dict[str, Any]:
        """桌面窗口 JS 桥：直连 API；GET 失败时回放离线只读缓存。"""
        method = method.upper()
        raw = base64.b64decode(body_b64) if body_b64 else None
        headers = {"Content-Type": content_type} if content_type else None
        cache = OfflineCache(self.home / "cache")
        try:
            response = self._request(method, path, content=raw, headers=headers)
        except ClientError as exc:
            if method == "GET" and cache.cacheable(path):
                cached = cache.get(self.server, path)
                if cached is not None:
                    payload, cached_type = cached
                    return {
                        "status": 200,
                        "content_type": cached_type,
                        "body": base64.b64encode(payload).decode("ascii"),
                        "offline": True,
                        "cached_at": cache.saved_at(self.server, path),
                    }
            detail = json.dumps({"detail": str(exc)}, ensure_ascii=False).encode()
            return {
                "status": 503,
                "content_type": "application/json; charset=utf-8",
                "body": base64.b64encode(detail).decode("ascii"),
                "offline": True,
            }
        payload = response.content
        ctype = response.headers.get("Content-Type", "application/json")
        if method == "GET" and response.status_code == 200 and cache.cacheable(path):
            cache.put(self.server, path, payload, ctype)
        return {
            "status": response.status_code,
            "content_type": ctype,
            "body": base64.b64encode(payload).decode("ascii"),
            "offline": False,
        }

    def download(self, url: str, dest: str | Path) -> Path:
        target = Path(dest).expanduser()
        request_url = self._url(url)
        try:
            with self._http.stream("GET", request_url) as response:
                if response.status_code >= 400:
                    response.read()
                    self._fail(response, "下载")
                target = self._resolve_dest(target, url, response)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            raise ClientError(f"下载失败：{exc}") from exc
        finally:
            self._persist_cookies()
        return target

    @staticmethod
    def _resolve_dest(dest: Path, url: str, response: httpx.Response) -> Path:
        if not (dest.exists() and dest.is_dir()):
            return dest
        disposition = response.headers.get("content-disposition", "")
        marker = "filename="
        if marker in disposition:
            name = disposition.split(marker, 1)[1].strip().strip('"')
            if name:
                return dest / Path(name).name
        name = Path(url.split("?", 1)[0]).name or "download.bin"
        return dest / name

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
