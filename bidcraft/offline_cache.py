"""桌面客户端的离线只读缓存：按服务器 + 路径缓存 GET 响应。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

CACHE_MAX_BYTES = 40 * 1024 * 1024
CACHEABLE_PREFIXES = (
    "/projects",
    "/my/docs",
    "/approvals/pending",
    "/health",
    "/auth/me",
)


class OfflineCache:
    """落盘缓存，仅在网络不可用时回放（只读）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def cacheable(path: str) -> bool:
        clean = path.split("?", 1)[0]
        return any(clean == prefix or clean.startswith(prefix + "/") for prefix in CACHEABLE_PREFIXES)

    def _base(self, server: str) -> Path:
        digest = hashlib.sha256(server.rstrip("/").encode()).hexdigest()[:16]
        base = self.root / digest
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _paths(self, server: str, path: str) -> tuple[Path, Path]:
        base = self._base(server)
        key = hashlib.sha256(path.encode()).hexdigest()
        return base / f"{key}.body", base / f"{key}.json"

    def put(self, server: str, path: str, body: bytes, content_type: str) -> None:
        if len(body) > CACHE_MAX_BYTES:
            return
        body_path, meta_path = self._paths(server, path)
        try:
            body_path.write_bytes(body)
            meta_path.write_text(
                json.dumps(
                    {"path": path, "content_type": content_type, "saved_at": time.time()},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            return

    def get(self, server: str, path: str) -> tuple[bytes, str] | None:
        body_path, meta_path = self._paths(server, path)
        if not body_path.is_file() or not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return body_path.read_bytes(), str(meta.get("content_type") or "application/json")
        except (OSError, ValueError):
            return None

    def saved_at(self, server: str, path: str) -> float | None:
        _body, meta_path = self._paths(server, path)
        try:
            return float(json.loads(meta_path.read_text(encoding="utf-8")).get("saved_at"))
        except (OSError, ValueError, TypeError):
            return None
