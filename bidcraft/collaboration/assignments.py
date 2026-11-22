"""章节责任分工（ChapterAssignment）。

只做「谁负责哪一章、进度如何」的协作信息，不改变章节正文或审核状态。
所有写操作记录审计，``assigned_by`` 由调用方传入当前用户名。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..clock import iso_beijing, utc_now
from ..models import ChapterAssignment, OutlineNode, Project, ProjectMember, User

ASSIGNMENT_STATUSES = ("assigned", "in_progress", "done")


class AssignmentError(ValueError):
    """分工参数不合法（如指派人不是项目成员）。"""


def _actor_name(actor: Any) -> str:
    if isinstance(actor, dict):
        return str(actor.get("username") or actor.get("display_name") or "system")
    return str(actor or "system")


def _audit_actor(actor: Any) -> dict[str, Any]:
    if isinstance(actor, dict):
        return actor
    return {"username": _actor_name(actor)}


def _is_project_member(db: Session, project_id: str, user_id: str) -> bool:
    project = db.get(Project, project_id)
    if project is None:
        return False
    if project.owner_id and project.owner_id == user_id:
        return True
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    return member is not None


def _parse_deadline(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AssignmentError("截止时间格式不合法") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _validate_status(status: str) -> str:
    value = (status or "").strip()
    if value not in ASSIGNMENT_STATUSES:
        raise AssignmentError(
            "分工状态只能是 assigned/in_progress/done"
        )
    return value


def assignment_payload(db: Session, row: ChapterAssignment) -> dict[str, Any]:
    node = db.get(OutlineNode, row.node_id)
    user = db.get(User, row.assignee_id)
    return {
        "id": row.id,
        "project_id": row.project_id,
        "node_id": row.node_id,
        "heading": node.heading if node is not None else "",
        "assignee_id": row.assignee_id,
        "assignee_username": user.username if user is not None else "",
        "assignee_display_name": (
            (user.display_name or user.username) if user is not None else ""
        ),
        "assigned_by": row.assigned_by or "",
        "status": row.status or "assigned",
        "deadline": iso_beijing(row.deadline),
        "note": row.note or "",
        "created_at": iso_beijing(row.created_at),
        "updated_at": iso_beijing(row.updated_at),
    }


def assign_chapter(
    db: Session,
    project_id: str,
    node_id: str,
    assignee_id: str,
    assigned_by: Any,
    *,
    deadline: Any = None,
    note: str = "",
) -> ChapterAssignment:
    """创建或更新某章节的责任人（一章一条分工记录）。"""

    node = db.get(OutlineNode, node_id)
    if node is None or node.project_id != project_id:
        raise LookupError(f"章节不存在: {node_id}")
    if not _is_project_member(db, project_id, assignee_id):
        raise AssignmentError("指派人必须是项目成员")

    parsed_deadline = _parse_deadline(deadline)
    row = db.scalar(
        select(ChapterAssignment).where(
            ChapterAssignment.project_id == project_id,
            ChapterAssignment.node_id == node_id,
        )
    )
    if row is None:
        row = ChapterAssignment(
            project_id=project_id,
            node_id=node_id,
            assignee_id=assignee_id,
            assigned_by=_actor_name(assigned_by),
            status="assigned",
            deadline=parsed_deadline,
            note=note or "",
            created_at=utc_now(),
        )
        db.add(row)
        action = "chapter.assign"
    else:
        row.assignee_id = assignee_id
        row.assigned_by = _actor_name(assigned_by)
        row.status = "assigned"
        row.deadline = parsed_deadline
        row.note = note or ""
        action = "chapter.reassign"
    db.flush()
    record_audit(
        db,
        project_id,
        action,
        {
            "assignment_id": row.id,
            "node_id": node_id,
            "heading": node.heading,
            "assignee_id": assignee_id,
            "status": row.status,
            "deadline": iso_beijing(row.deadline),
            "note": row.note,
        },
        actor=_audit_actor(assigned_by),
    )
    db.flush()
    return row


def update_assignment(
    db: Session,
    assignment_id: str,
    *,
    status: str | None = None,
    note: str | None = None,
    actor: Any = None,
) -> ChapterAssignment:
    row = db.get(ChapterAssignment, assignment_id)
    if row is None:
        raise LookupError(f"分工不存在: {assignment_id}")
    if status is not None:
        row.status = _validate_status(status)
    if note is not None:
        row.note = note
    row.updated_at = utc_now()
    db.flush()
    record_audit(
        db,
        row.project_id,
        "chapter.assignment.update",
        {
            "assignment_id": row.id,
            "node_id": row.node_id,
            "status": row.status,
            "note": row.note,
        },
        actor=_audit_actor(actor),
    )
    db.flush()
    return row


def complete_assignment(
    db: Session, assignment_id: str, actor: Any = None
) -> ChapterAssignment:
    return update_assignment(db, assignment_id, status="done", actor=actor)


def list_assignments(db: Session, project_id: str) -> list[ChapterAssignment]:
    return list(
        db.scalars(
            select(ChapterAssignment)
            .where(ChapterAssignment.project_id == project_id)
            .order_by(ChapterAssignment.created_at, ChapterAssignment.id)
        )
    )
