"""章节正文修订快照、版本对比与回滚。

与 ``Section.revision`` 的契约
------------------------------
保存正文时 ``routes.projects.save_chapter`` 先把 ``Section.revision`` 加一，
随后调用 :func:`snapshot_on_save` 落一条同号快照。因此快照号与乐观锁版本号
始终一致；同一 ``(node_id, revision)`` 重复写入会被幂等跳过。

回滚 **不覆盖历史**：以当前 ``Section.revision + 1`` 生成一条
``change_type="restore"`` 的新快照，正文取目标历史版本。已确认/已锁定的
章节必须先按现有审核机制 ``revert`` 为草稿，否则抛 :class:`RestoreBlocked`
由调用方（HTTP 层）映射为 409。
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import provenance
from ..audit import record_audit
from ..clock import iso_beijing, utc_now
from ..generation.writer import count_words
from ..models import OutlineNode, Section, SectionRevision

CHANGE_TYPES = ("edit", "restore", "generate")
FROZEN_REVIEW_STATUSES = {"confirmed", "locked"}


class RestoreBlocked(Exception):
    """章节已确认/锁定，必须先撤回为草稿才能回滚。"""


def _body_hash(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _actor_name(actor: Any) -> str:
    if isinstance(actor, dict):
        return str(actor.get("username") or actor.get("display_name") or "system")
    return str(actor or "system")


def _audit_actor(actor: Any) -> dict[str, Any]:
    if isinstance(actor, dict):
        return actor
    return {"username": _actor_name(actor)}


def revision_payload(row: SectionRevision) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "node_id": row.node_id,
        "revision": int(row.revision or 0),
        "body": row.body or "",
        "body_hash": row.body_hash or "",
        "changed_by": row.changed_by or "",
        "change_type": row.change_type or "edit",
        "change_note": row.change_note or "",
        "created_at": iso_beijing(row.created_at),
    }


def snapshot_on_save(
    db: Session,
    section: Section | None,
    project_id: str,
    actor: Any,
    change_note: str = "",
    *,
    change_type: str = "edit",
) -> SectionRevision | None:
    """为当前 ``section.revision`` 落一条快照（幂等，只 flush 不 commit）。

    返回新快照；同 ``(node_id, revision)`` 已存在时返回 ``None``。该函数与
    ``save_chapter`` 同事务，调用方提交或回滚即可。
    """

    if section is None or not section.node_id:
        return None
    revision = int(section.revision or 0)
    existing = db.scalar(
        select(SectionRevision).where(
            SectionRevision.node_id == section.node_id,
            SectionRevision.revision == revision,
        )
    )
    if existing is not None:
        return None
    body = section.body or ""
    row = SectionRevision(
        project_id=project_id,
        node_id=section.node_id,
        revision=revision,
        body=body,
        body_hash=_body_hash(body),
        changed_by=_actor_name(actor),
        change_type=change_type if change_type in CHANGE_TYPES else "edit",
        change_note=change_note or "",
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def save_section_revision(
    db: Session,
    section: Section | None,
    project_id: str,
    actor: Any,
    change_note: str = "",
    *,
    change_type: str = "edit",
) -> SectionRevision | None:
    """:func:`snapshot_on_save` 的语义化别名。"""

    return snapshot_on_save(
        db,
        section,
        project_id,
        actor,
        change_note,
        change_type=change_type,
    )


def list_section_revisions(db: Session, node_id: str) -> list[SectionRevision]:
    """章节修订历史，最新版本在前。"""

    return list(
        db.scalars(
            select(SectionRevision)
            .where(SectionRevision.node_id == node_id)
            .order_by(SectionRevision.revision.desc(), SectionRevision.created_at.desc())
        )
    )


def get_section_revision(
    db: Session, node_id: str, revision: int
) -> SectionRevision | None:
    return db.scalar(
        select(SectionRevision).where(
            SectionRevision.node_id == node_id,
            SectionRevision.revision == int(revision),
        )
    )


def _extract(value: Any) -> tuple[str, int | None, str]:
    if value is None:
        return "", None, _body_hash("")
    if isinstance(value, str):
        return value, None, _body_hash(value)
    body = getattr(value, "body", None)
    if body is None:
        body = str(value)
    revision = getattr(value, "revision", None)
    return body or "", (int(revision) if revision is not None else None), _body_hash(body or "")


def diff_section_revision(old: Any, new: Any) -> dict[str, Any]:
    """对比两个版本（``SectionRevision`` / ``Section`` / 正文 str 均可）。

    返回行级差异：``lines`` 中每项含 ``type``（equal/added/removed）、
    ``text`` 以及 1-based 的 ``old_line`` / ``new_line``。纯函数，不触碰 DB。
    """

    old_body, old_revision, old_hash = _extract(old)
    new_body, new_revision, new_hash = _extract(new)
    old_lines = old_body.splitlines()
    new_lines = new_body.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    lines: list[dict[str, Any]] = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                lines.append(
                    {
                        "type": "equal",
                        "text": old_lines[i1 + offset],
                        "old_line": i1 + offset + 1,
                        "new_line": j1 + offset + 1,
                    }
                )
        elif tag == "delete":
            for index in range(i1, i2):
                lines.append(
                    {
                        "type": "removed",
                        "text": old_lines[index],
                        "old_line": index + 1,
                        "new_line": None,
                    }
                )
                removed += 1
        elif tag == "insert":
            for index in range(j1, j2):
                lines.append(
                    {
                        "type": "added",
                        "text": new_lines[index],
                        "old_line": None,
                        "new_line": index + 1,
                    }
                )
                added += 1
        else:  # replace
            for index in range(i1, i2):
                lines.append(
                    {
                        "type": "removed",
                        "text": old_lines[index],
                        "old_line": index + 1,
                        "new_line": None,
                    }
                )
                removed += 1
            for index in range(j1, j2):
                lines.append(
                    {
                        "type": "added",
                        "text": new_lines[index],
                        "old_line": None,
                        "new_line": index + 1,
                    }
                )
                added += 1
    return {
        "from_revision": old_revision,
        "to_revision": new_revision,
        "from_hash": old_hash,
        "to_hash": new_hash,
        "changed": old_body != new_body,
        "added_lines": added,
        "removed_lines": removed,
        "lines": lines,
    }


def restore_section_revision(
    db: Session,
    node_id: str,
    revision: int,
    actor: Any,
    note: str = "",
) -> SectionRevision:
    """把历史版本回滚为当前正文，生成**新修订号**而非覆盖历史。

    已确认/锁定章节拒绝回滚（``RestoreBlocked``），调用方应先走审核
    ``revert``。返回新生成的 ``change_type="restore"`` 快照。
    """

    target = get_section_revision(db, node_id, revision)
    if target is None:
        raise LookupError(f"修订版本不存在: {revision}")
    section = db.scalar(select(Section).where(Section.node_id == node_id))
    if section is None:
        raise LookupError("章节正文不存在")
    review_status = (section.review_status or "draft").lower()
    if review_status in FROZEN_REVIEW_STATUSES:
        raise RestoreBlocked("本章已确认/锁定，请先撤回为草稿后再回滚")

    node = db.get(OutlineNode, node_id)
    if node is None:
        raise LookupError("章节不存在")

    body = target.body or ""
    previous_revision = int(section.revision or 0)
    new_revision = previous_revision + 1
    section.body = body
    section.words = count_words(body)
    section.status = "edited"
    section.revision = new_revision
    # 待复核正文被替换后，复核对象已变，退回草稿（与手动保存一致）。
    if review_status == "review":
        section.review_status = "draft"
        section.review_requested_at = None
        section.review_requested_by = None
    node.status = "edited"
    db.flush()

    provenance.refresh_node(db, node.project_id, node_id, body)

    row = snapshot_on_save(
        db,
        section,
        node.project_id,
        actor,
        note,
        change_type="restore",
    )
    if row is None:  # pragma: no cover - 同号并发插入的防御分支
        row = get_section_revision(db, node_id, new_revision)
    record_audit(
        db,
        node.project_id,
        "chapter.restore",
        {
            "node_id": node_id,
            "heading": node.heading,
            "from_revision": previous_revision,
            "restored_revision": int(revision),
            "to_revision": new_revision,
            "note": note or "",
        },
        actor=_audit_actor(actor),
    )
    db.flush()
    assert row is not None
    return row
