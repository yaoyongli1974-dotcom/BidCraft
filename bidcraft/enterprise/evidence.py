"""企业资产证据描述。

匹配结果必须能回溯到「哪一条资产、哪份证书、哪页文档」。本模块只负责把
``Qualification`` / ``Personnel`` / ``KbDocument`` 拍平成统一证据结构，不做
任何匹配判定。证据哈希用文件内容计算；文件缺失时保留条目并把 ``sha256``
置空，绝不因为存证文件丢失而伪造证据。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ..models import KbDocument, Personnel, Qualification


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.replace(microsecond=0).isoformat()


def file_sha256(path: str | Path) -> str:
    """Content hash for one evidence file; ``""`` when it cannot be read."""

    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ""


def qualification_evidence(row: Qualification) -> list[dict]:
    items: list[dict] = []
    for entry in row.evidence or []:
        if not isinstance(entry, dict) or not entry.get("filename"):
            continue
        raw_path = str(entry.get("path") or "")
        digest = str(entry.get("sha256") or "") or (
            file_sha256(raw_path) if raw_path else ""
        )
        items.append(
            {
                "asset_type": "qualification",
                "asset_id": row.id,
                "filename": entry.get("filename") or "",
                "path": raw_path,
                "size": int(entry.get("size") or 0),
                "mime": entry.get("mime") or "",
                "uploaded_at": entry.get("uploaded_at"),
                "sha256": digest,
            }
        )
    return items


def personnel_evidence(row: Personnel) -> list[dict]:
    """人员本身没有附件，证据即结构化证书字段（可追溯到台账行）。"""

    return [
        {
            "asset_type": "personnel",
            "asset_id": row.id,
            "filename": "",
            "path": "",
            "size": 0,
            "mime": "",
            "uploaded_at": _iso(row.updated_at),
            "sha256": hashlib.sha256(
                json.dumps(
                    {
                        "name": row.name or "",
                        "cert_no": row.cert_no or "",
                        "specialty": row.specialty or "",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "cert_no": row.cert_no or "",
        }
    ]


def document_evidence(row: KbDocument) -> list[dict]:
    return [
        {
            "asset_type": "document",
            "asset_id": row.id,
            "filename": row.source or "",
            "path": "",
            "size": int(row.size or 0),
            "mime": row.mime or "",
            "uploaded_at": _iso(row.created_at),
            "sha256": row.sha256 or "",
            "pages": int(row.pages or 0),
            "version": int(row.version or 0),
        }
    ]


def qualification_descriptor(row: Qualification, *, status: str = "") -> dict:
    return {
        "asset_type": "qualification",
        "id": row.id,
        "kind": row.kind or "",
        "name": row.name or "",
        "holder": row.holder or "",
        "cert_no": row.cert_no or "",
        "valid_to": _iso(row.valid_to),
        "status": status,
        "enterprise_id": row.enterprise_id or "",
        "shared": bool(row.enterprise_id),
        "evidence": qualification_evidence(row),
    }


def personnel_descriptor(row: Personnel) -> dict:
    return {
        "asset_type": "personnel",
        "id": row.id,
        "name": row.name or "",
        "title": row.title or "",
        "specialty": row.specialty or "",
        "cert_no": row.cert_no or "",
        "social_months": int(row.social_months or 0),
        "locked": bool(row.locked),
        "enterprise_id": row.enterprise_id or "",
        "shared": bool(row.enterprise_id),
        "evidence": personnel_evidence(row),
    }


def document_descriptor(row: KbDocument, *, enterprise_id: str = "") -> dict:
    return {
        "asset_type": "document",
        "id": row.id,
        "kind": row.kind or "",
        "name": row.source or "",
        "source": row.source or "",
        "tags": list(row.tags or []),
        "valid_to": _iso(row.valid_to),
        "status": row.status or "",
        "version": int(row.version or 0),
        "sha256": row.sha256 or "",
        "enterprise_id": enterprise_id,
        "shared": bool(enterprise_id),
        "evidence": document_evidence(row),
    }


def aggregate(documents: list[dict]) -> list[dict]:
    """Flatten descriptor evidence into one deduplicated list for a package."""

    seen: set[tuple[str, str, str]] = set()
    items: list[dict] = []
    for descriptor in documents:
        for entry in descriptor.get("evidence") or []:
            key = (
                str(entry.get("asset_type") or ""),
                str(entry.get("asset_id") or ""),
                str(entry.get("filename") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            items.append(entry)
    return items


__all__ = [
    "aggregate",
    "document_descriptor",
    "document_evidence",
    "file_sha256",
    "personnel_descriptor",
    "personnel_evidence",
    "qualification_descriptor",
    "qualification_evidence",
]
