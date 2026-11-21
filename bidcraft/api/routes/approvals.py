"""API routes: /approvals (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import Any, boq, current_user, session_scope  # noqa: F401

router = APIRouter()


@router.get("/approvals/pending")
def approvals_pending() -> list[dict[str, Any]]:
    """R20 待我审批：跨项目聚合当前用户待审的报价版本（平铺数组）。"""
    with session_scope() as db:
        return boq.pending_for_user(db, current_user() or {})


