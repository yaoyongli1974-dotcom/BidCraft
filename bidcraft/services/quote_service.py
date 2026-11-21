"""报价域应用服务：把 HTTP / CLI 共用的「解析包件 → 改价 → 审计」编排收敛到一处。

本模块是 Service 层（介于 API 与业务模块之间），职责边界：

- 接受调用方已打开的 ``db: Session``，只 ``flush`` 不 ``commit``——提交点仍是
  入口的 ``session_scope``，保证与上传/建项目等多步写入同一事务原子性。
- **不 import HTTP 层**（``bidcraft.api``），避免循环依赖；领域异常
  （``BoqConflictError`` / ``ValueError`` / ``LookupError``）原样上抛，由各入口
  映射成 409/400/404 或 CLI/MCP 的错误文本。
- 用显式 ``actor`` 承载入口差异：HTTP 传真实用户字典，CLI/MCP 传 ``None``
  （审计记 ``system``），不再依赖仅 HTTP 中间件会写入的 ContextVar。

这样同一业务规则只有一份实现，HTTP 与 CLI 不会再因「是否透传 package_id」等
细节产生行为分叉（旧 CLI 漏传 package_id，多包件项目改价落到 IS NULL 快照）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .. import boq
from ..audit import record_audit
from ..models import Project

#: 审计动作名，与既有 HTTP 路由写入的动作保持一致。
PRICE_AUDIT_ACTION = "boq.price"


def set_unit_price(
    db: Session,
    project: Project,
    boq_row: int,
    unit_price: float | None,
    *,
    package_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析包件、写入单价并留审计——HTTP / CLI 共用同一编排口径。

    ``package_id`` 必须一并传给 ``boq.resolve_package`` 与 ``boq.set_unit_price``：
    旧 CLI 漏传导致多包件项目改价落到 ``package_id IS NULL`` 的旧快照上（静默
    失败）。统一走这里后两个入口行为一致。

    异常原样上抛（``BoqConflictError`` / ``ValueError`` / ``LookupError``），
    由调用入口映射成各自的响应格式；本函数不 commit，交给入口的 session_scope。
    """
    package = boq.resolve_package(db, project, package_id)
    resolved = package.id if package else None
    result = boq.set_unit_price(
        db, project, boq_row, unit_price, package_id=resolved
    )
    record_audit(
        db,
        project.id,
        PRICE_AUDIT_ACTION,
        {"row": boq_row, "unit_price": unit_price, "package_id": resolved},
        actor=actor,
    )
    return result
