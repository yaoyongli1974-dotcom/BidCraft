"""Enterprise terminology store (企业术语库) and response-body scanner.

Two kinds of entries are supported:

``forbidden``
    A 禁用词 that must never appear in the response body; every hit is a
    ``major`` finding.
``replace``
    A wording (``source``) that should be unified to ``target``; a hit is a
    ``minor`` finding. ``target`` is mandatory for this kind.

The scanner never invents text: findings quote the body and the configured
term only. Entries are global (not per project) and only ``enabled`` rows are
used when checking a project.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .db import session_scope
from .models import TermItem

KINDS = ("replace", "forbidden")
CATEGORY = "术语规范"
EVIDENCE_LIMIT = 80
MAX_SCAN_FINDINGS = 200
SOURCE_MAX = 120
TARGET_MAX = 120
NOTE_MAX = 200


class TermError(ValueError):
    """Invalid term payload (bad kind, empty source, missing target, …)."""


class TermExists(TermError):
    """Another term already uses the same ``(kind, source)`` pair."""


def validate_kind(value: str | None) -> str:
    kind = (value or "").strip()
    if kind not in KINDS:
        raise TermError("kind 只能是 replace/forbidden")
    return kind


def normalize_source(value: str | None) -> str:
    source = (value or "").strip()
    if not source:
        raise TermError("术语原文不能为空")
    if len(source) > SOURCE_MAX:
        raise TermError(f"术语原文不能超过 {SOURCE_MAX} 个字符")
    return source


def normalize_target(kind: str, value: str | None) -> str:
    target = (value or "").strip()
    if len(target) > TARGET_MAX:
        raise TermError(f"替换词不能超过 {TARGET_MAX} 个字符")
    if kind == "replace" and not target:
        raise TermError("replace 术语必须填写替换词")
    return target


def normalize_note(value: str | None) -> str:
    note = (value or "").strip()
    if len(note) > NOTE_MAX:
        raise TermError(f"备注不能超过 {NOTE_MAX} 个字符")
    return note


def term_payload(row: TermItem) -> dict[str, Any]:
    created = row.created_at
    updated = row.updated_at
    return {
        "id": row.id,
        "kind": row.kind,
        "source": row.source or "",
        "target": row.target or "",
        "note": row.note or "",
        "enabled": bool(row.enabled),
        "created_at": clock.iso_utc(created),
        "updated_at": clock.iso_utc(updated),
    }


def load_enabled(db: Session) -> list[TermItem]:
    return list(
        db.scalars(
            select(TermItem)
            .where(TermItem.enabled.is_(True))
            .order_by(TermItem.kind, TermItem.source, TermItem.id)
        )
    )


def list_terms(
    db: Session,
    search: str = "",
    kind: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[TermItem]:
    statement = select(TermItem)
    text = (search or "").strip()
    if text:
        pattern = f"%{text}%"
        statement = statement.where(
            TermItem.source.ilike(pattern)
            | TermItem.target.ilike(pattern)
            | TermItem.note.ilike(pattern)
        )
    if kind:
        statement = statement.where(TermItem.kind == validate_kind(kind))
    capped = max(1, min(int(limit), 500))
    start = max(0, int(offset))
    return list(
        db.scalars(
            statement.order_by(TermItem.kind, TermItem.source, TermItem.id)
            .limit(capped)
            .offset(start)
        )
    )


def find_term(db: Session, kind: str, source: str) -> TermItem | None:
    return db.scalar(
        select(TermItem).where(TermItem.kind == kind, TermItem.source == source)
    )


def _ensure_unique(
    db: Session, kind: str, source: str, exclude_id: str | None = None
) -> None:
    row = find_term(db, kind, source)
    if row is not None and row.id != exclude_id:
        raise TermExists(f"术语已存在：{kind}/{source}")


def create_term(
    db: Session,
    *,
    kind: str,
    source: str,
    target: str = "",
    note: str = "",
    enabled: bool = True,
) -> TermItem:
    kind = validate_kind(kind)
    source = normalize_source(source)
    _ensure_unique(db, kind, source)
    row = TermItem(
        kind=kind,
        source=source,
        target=normalize_target(kind, target),
        note=normalize_note(note),
        enabled=bool(enabled),
    )
    db.add(row)
    db.flush()
    return row


def update_term(db: Session, row: TermItem, **changes: Any) -> TermItem:
    kind = validate_kind(changes["kind"]) if "kind" in changes else row.kind
    source = normalize_source(changes["source"]) if "source" in changes else row.source
    with db.no_autoflush:
        _ensure_unique(db, kind, source, exclude_id=row.id)
    target = changes.get("target", row.target)
    note = changes.get("note", row.note)
    row.kind = kind
    row.source = source
    row.target = normalize_target(kind, target)
    row.note = normalize_note(note)
    if "enabled" in changes:
        row.enabled = bool(changes["enabled"])
    db.flush()
    return row


def delete_term(db: Session, row: TermItem) -> None:
    db.delete(row)
    db.flush()


def _quote(text: str, index: int) -> str:
    return text[index : index + EVIDENCE_LIMIT]


def scan_body(
    db: Session | None,
    body: str,
    *,
    heading: str = "",
    items: list[TermItem] | None = None,
    start: int = 1,
) -> list[dict[str, Any]]:
    """Scan one response body against the enabled terminology entries.

    Each hit yields a finding shaped like the rule engine's issues: a
    sequential ``TERM-###`` id (numbering starts at ``start`` so callers can
    append several chapters), ``level`` (``major`` for 禁用词, ``minor`` for
    wording to unify), the fixed category ``术语规范``, ``location`` set to
    ``heading``, a ``recommendation`` and an ``evidence`` quote of at most 80
    characters.

    ``items`` lets a caller reuse one query for many sections. When omitted the
    enabled rows are loaded from ``db`` (or a short-lived session when ``db``
    is ``None``).
    """

    text = body or ""
    if not text.strip():
        return []
    if items is None:
        if db is not None:
            items = load_enabled(db)
        else:
            with session_scope() as session:
                items = load_enabled(session)
    findings: list[dict[str, Any]] = []
    for row in sorted(items, key=lambda item: (item.kind, item.source, item.id)):
        if not row.enabled:
            continue
        source = (row.source or "").strip()
        if not source:
            continue
        index = text.find(source)
        if index < 0:
            continue
        target = (row.target or "").strip()
        if row.kind == "forbidden":
            level = "major"
            message = f"出现禁用词「{source}」"
            recommendation = f"删除或改写禁用词「{source}」"
        elif row.kind == "replace":
            if not target:  # defensive: replace rows always carry a target
                continue
            level = "minor"
            message = f"术语不统一：仍使用「{source}」，建议统一为「{target}」"
            recommendation = f"将「{source}」统一为「{target}」"
        else:  # pragma: no cover - guarded by the API/model contract
            continue
        findings.append(
            {
                "id": f"TERM-{start + len(findings):03d}",
                "level": level,
                "category": CATEGORY,
                "message": message,
                "location": heading,
                "recommendation": recommendation,
                "evidence": {
                    "source": source,
                    "target": target,
                    "quote": _quote(text, index),
                },
            }
        )
    return findings


def scan_sections(
    db: Session,
    sections: list[dict[str, Any]],
    *,
    limit: int = MAX_SCAN_FINDINGS,
) -> list[dict[str, Any]]:
    """Append ``scan_body`` results chapter by chapter, numbered continuously.

    The enabled terms are loaded once and reused for every section; the total
    is capped at ``limit`` findings so a huge document cannot flood the issue
    ledger.
    """

    items = load_enabled(db)
    findings: list[dict[str, Any]] = []
    for section in sections:
        body = str(section.get("body") or "")
        if not body.strip():
            continue
        heading = str(section.get("heading") or "")
        remaining = limit - len(findings)
        if remaining <= 0:
            break
        findings.extend(
            scan_body(db, body, heading=heading, items=items, start=len(findings) + 1)[
                :remaining
            ]
        )
    return findings
