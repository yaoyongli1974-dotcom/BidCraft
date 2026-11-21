"""API routes: /packages (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import Any, _page_bounds, list_packages  # noqa: F401

router = APIRouter()


@router.get("/packages")
def packages(limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    """Selectable industry packages (the abstract base package is hidden)."""
    capped, start = _page_bounds(limit, offset)
    return [item for item in list_packages() if not item.get("abstract")][
        start : start + capped
    ]


