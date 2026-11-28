"""Per-user document uploads backed by one personal local knowledge base.

Each regular user owns a single :class:`~bidcraft.models.KnowledgeBase` named
``个人知识库-{user_id}``.  Uploaded files live under
``<BIDCRAFT_HOME>/user_docs/{user_id}/`` and are indexed by the existing local
knowledge-base worker, so generation can reuse the normal retrieval path
without any cross-user visibility.
"""

from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from .config import settings
from .models import KnowledgeBase, UserDoc
from .pipeline import stage_file, validate_declared_type

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

    validate_declared_type(data, suffix)

    root = user_dir(uid)
    root.mkdir(parents=True, exist_ok=True)
    target = _unique_path(root, name)
    digest = stage_file(db, target, data)

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
    # checksum 由 0018 迁移添加到 user_docs；模型尚未声明该列，用显式 SQL 写入，
    # 与行在同一事务内提交，避免文件与元数据不一致。
    db.execute(
        text("UPDATE user_docs SET checksum = :digest WHERE id = :id"),
        {"digest": digest, "id": row.id},
    )
    return row


def stored_checksum(db, doc_id: str) -> str:
    """返回已记录的 sha256；空串表示历史文件未记录（读取端跳过校验）。"""

    value = db.execute(
        text("SELECT checksum FROM user_docs WHERE id = :id"), {"id": doc_id}
    ).scalar()
    return str(value or "")


def verify_stored_file(db, row: UserDoc) -> Path:
    """解析并校验一个用户文档：越界路径、缺文件、哈希不符一律 404。"""

    root = user_dir(row.user_id).resolve()
    try:
        path = Path(row.stored_path).resolve()
    except OSError:
        raise LookupError(f"文件不存在：{row.filename}") from None
    if root not in path.parents or not path.is_file():
        raise LookupError(f"文件不存在：{row.filename}")
    expected = stored_checksum(db, row.id)
    if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise LookupError(f"文件校验失败：{row.filename}")
    return path


def delete_user_files(user_id: str) -> int:
    """Remove every file under one user's upload directory (metadata handled by caller)."""

    root = user_dir(user_id)
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.iterdir():
        if path.is_file() or path.is_symlink():
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


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
