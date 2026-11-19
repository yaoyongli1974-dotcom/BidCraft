"""Per-user document uploads backed by one personal local knowledge base.

Each regular user owns a single :class:`~bidcraft.models.KnowledgeBase` named
``个人知识库-{user_id}``.  Uploaded files live under
``<BIDCRAFT_HOME>/user_docs/{user_id}/`` and are indexed by the existing local
knowledge-base worker, so generation can reuse the normal retrieval path
without any cross-user visibility.
"""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .models import KnowledgeBase, UserDoc

USER_DOCS_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md", ".xlsx", ".xlsm"})
MAX_USER_DOC_BYTES = 50 * 1024 * 1024
PERSONAL_KB_PREFIX = "个人知识库-"


def _user_id(user) -> str:
    if isinstance(user, dict):
        value = user.get("id")
    else:
        value = getattr(user, "id", None)
    return str(value or "")


def user_dir(user_id: str) -> Path:
    """Return the fixed server-side directory for one user's uploads."""

    safe = Path(str(user_id or "")).name
    return Path(settings.home) / "user_docs" / safe


def personal_kb(db, user_id: str) -> KnowledgeBase:
    """Return the user's personal knowledge base, creating it on first use."""

    uid = str(user_id or "")
    name = f"{PERSONAL_KB_PREFIX}{uid}"
    kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
    if kb is not None:
        return kb
    root = user_dir(uid)
    root.mkdir(parents=True, exist_ok=True)
    kb = KnowledgeBase(
        name=name,
        kind="local",
        provider="local",
        location=str(root),
        enabled=True,
        is_default=False,
        options={"personal": True, "user_id": uid},
    )
    db.add(kb)
    db.flush()
    return kb


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def doc_payload(row: UserDoc) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "size": int(row.size or 0),
        "mime": row.mime or "",
        "status": row.status,
        "error": row.error or "",
        "kb_id": row.kb_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _safe_filename(filename: str | None) -> str:
    value = Path((filename or "").replace("\\", "/")).name.strip()
    if not value or value in {".", ".."}:
        raise ValueError("文件名不能为空")
    return value


def _unique_path(root: Path, filename: str) -> Path:
    target = root / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = root / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("同名文件过多，请先删除旧文件")


def save_upload(db, user, filename: str | None, content: bytes) -> UserDoc:
    """Validate an upload, write it to the user's directory and record its row.

    Indexing is intentionally left to the API layer so it can enqueue the
    existing background ``kb_reindex`` job.
    """

    uid = _user_id(user)
    name = _safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in USER_DOCS_SUFFIXES:
        raise ValueError("文档仅支持 PDF/DOCX/TXT/MD/XLSX/XLSM 格式")
    data = content or b""
    if len(data) > MAX_USER_DOC_BYTES:
        raise ValueError("文档不能超过 50MB")

    root = user_dir(uid)
    root.mkdir(parents=True, exist_ok=True)
    target = _unique_path(root, name)
    target.write_bytes(data)

    kb = personal_kb(db, uid)
    mime = mimetypes.guess_type(name)[0] or ""
    row = UserDoc(
        user_id=uid,
        kb_id=kb.id,
        filename=name,
        stored_path=str(target),
        size=len(data),
        mime=mime,
        status="ready",
    )
    db.add(row)
    db.flush()
    return row


def list_docs(db, user_id: str) -> list[UserDoc]:
    return list(
        db.scalars(
            select(UserDoc)
            .where(UserDoc.user_id == str(user_id or ""))
            .order_by(UserDoc.created_at.desc(), UserDoc.id.desc())
        )
    )


def get_doc(db, user_id: str, doc_id: str) -> UserDoc:
    row = db.get(UserDoc, doc_id)
    if row is None or row.user_id != str(user_id or ""):
        raise LookupError("文档不存在")
    return row


def delete_doc(db, user, doc_id: str) -> UserDoc:
    """Delete one of the user's own files and its metadata row."""

    uid = _user_id(user)
    row = get_doc(db, uid, doc_id)
    root = user_dir(uid).resolve()
    try:
        path = Path(row.stored_path).resolve()
    except OSError:
        path = None
    if path is not None and (path == root or root in path.parents):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    db.delete(row)
    db.flush()
    return row
