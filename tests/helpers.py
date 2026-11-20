"""测试辅助：绕过 UI 直接把章节置为「已确认」。

章节复核是硬检查点（未确认的正文不允许导出），但这道关卡的 **UI 流转**
有专门用例覆盖；其它导出相关用例只需一个「已经确认过」的前提，用这里
的辅助函数一步到位，避免每个用例都去 POST 五次 review。
"""

from __future__ import annotations

from sqlalchemy import select

from bidcraft.db import session_scope
from bidcraft.models import OutlineNode, Section


def confirm_project_chapters(project_id: str) -> int:
    """把项目内所有有正文的章节标为已确认，返回处理条数。"""

    with session_scope() as session:
        node_ids = list(
            session.scalars(
                select(OutlineNode.id).where(OutlineNode.project_id == project_id)
            )
        )
        if not node_ids:
            return 0
        rows = list(
            session.scalars(select(Section).where(Section.node_id.in_(node_ids)))
        )
        count = 0
        for row in rows:
            if (row.body or "").strip() and row.review_status != "confirmed":
                row.review_status = "confirmed"
                count += 1
        return count
