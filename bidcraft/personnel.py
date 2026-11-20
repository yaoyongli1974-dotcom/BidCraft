"""投标人员台账：校验、CRUD 与素材元数据要求。

人员是资格自查与素材登记的共同基础：同一名人员既有职务/注册专业，也有
证书号、社保缴纳月份和在建锁定状态。本模块只做 owner 私有的结构化台账，
不联网核验证书，也不推断在建状态。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from . import material_policy
from .models import Personnel

MAX_NAME = 80
MAX_TITLE = 80
MAX_SPECIALTY = 80
MAX_CERT_NO = 80
MAX_NOTE = 200
SOCIAL_MIN = 0
SOCIAL_MAX = 600


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.replace(microsecond=0).isoformat()


def _user_id(value) -> str:
    if isinstance(value, dict):
        raw = value.get("id")
    else:
        raw = getattr(value, "id", None)
    return str(raw or "")


def person_payload(row: Personnel) -> dict:
    """Flatten one personnel row for the API/SPA."""

    return {
        "id": row.id,
        "name": row.name or "",
        "title": row.title or "",
        "specialty": row.specialty or "",
        "cert_no": row.cert_no or "",
        "social_months": int(row.social_months or 0),
        "locked": bool(row.locked),
        "note": row.note or "",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


#: 契约里的短名别名。
payload = person_payload


def policies() -> dict:
    """素材元数据对 ``personnel`` 的要求字段（复用默认策略）。"""

    spec = material_policy.DEFAULT_POLICY.get("personnel", {})
    return {
        "label": str(spec.get("label") or ""),
        "required": list(spec.get("required") or []),
        "hint": str(spec.get("hint") or ""),
    }


def _check_text(value, label: str, limit: int) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 字")
    return text


def _check_social_months(value) -> int:
    if isinstance(value, bool):
        raise ValueError("社保缴纳月份必须为整数")  # noqa: TRY004 - API 统一转 400
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("社保缴纳月份必须为整数")
        value = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError("社保缴纳月份必须为整数") from exc
    elif not isinstance(value, int):
        raise ValueError("社保缴纳月份必须为整数")  # noqa: TRY004 - API 统一转 400
    if value < SOCIAL_MIN or value > SOCIAL_MAX:
        raise ValueError(f"社保缴纳月份应在 {SOCIAL_MIN}–{SOCIAL_MAX} 之间")
    return value


def _check_locked(value) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError("在建锁定必须为布尔值")


def _values(data: dict, *, existing: Personnel | None) -> dict:
    """Validate a create/update payload and return column values to assign."""

    values: dict = {}
    if existing is None or "name" in data:
        name = _check_text(data.get("name"), "姓名", MAX_NAME)
        if not name:
            raise ValueError("姓名不能为空")
        values["name"] = name
    for field, label, limit in (
        ("title", "职务", MAX_TITLE),
        ("specialty", "注册专业", MAX_SPECIALTY),
        ("cert_no", "证书号", MAX_CERT_NO),
        ("note", "备注", MAX_NOTE),
    ):
        if field in data:
            values[field] = _check_text(data.get(field), label, limit)
    if "social_months" in data:
        values["social_months"] = _check_social_months(data.get("social_months"))
    if "locked" in data:
        values["locked"] = _check_locked(data.get("locked"))
    return values


def list_personnel(db, owner_id) -> list[Personnel]:
    """Owner-scoped list ordered by ``name`` ascending."""

    uid = _user_id(owner_id) if not isinstance(owner_id, str) else str(owner_id)
    stmt = select(Personnel).where(Personnel.owner_id == uid).order_by(
        Personnel.name.asc(), Personnel.created_at.asc()
    )
    return list(db.scalars(stmt))


def find_personnel(db, owner_id, person_id: str) -> Personnel:
    uid = _user_id(owner_id) if not isinstance(owner_id, str) else str(owner_id)
    row = db.get(Personnel, str(person_id or ""))
    if row is None or row.owner_id != uid:
        raise LookupError("人员不存在")
    return row


#: ``find_personnel`` 的短名别名。
get_personnel = find_personnel


def create_personnel(db, owner_id, data: dict) -> Personnel:
    uid = _user_id(owner_id) if not isinstance(owner_id, str) else str(owner_id)
    row = Personnel(owner_id=uid, **_values(data, existing=None))
    db.add(row)
    db.flush()
    return row


def update_personnel(db, owner_id, person_id: str, data: dict) -> Personnel:
    row = find_personnel(db, owner_id, person_id)
    for field, value in _values(data, existing=row).items():
        setattr(row, field, value)
    db.flush()
    return row


def delete_personnel(db, owner_id, person_id: str) -> Personnel:
    row = find_personnel(db, owner_id, person_id)
    db.delete(row)
    db.flush()
    return row
