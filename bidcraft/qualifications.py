"""资质/业绩台账、证据存证与投标前自查。

程序不联网查询信用中国等站点（无公开 API 且禁止抓取）。这里只做结构化
台账 + 官方查询截图留档 + 到期预警：用户在 :class:`Qualification` 中登记
证书信息，上传证据文件到 ``<BIDCRAFT_HOME>/qualifications/<user_id>/``，
开标前按 :data:`DEFAULT_ITEMS` 生成自查清单。
"""

from __future__ import annotations

import mimetypes
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .models import Qualification, utcnow

KINDS = ("license", "safety", "person", "performance", "credit", "other")
KIND_LABELS = {
    "license": "资质证照",
    "safety": "安全生产",
    "person": "人员证书",
    "performance": "业绩证明",
    "credit": "信用记录",
    "other": "其他",
}
EVIDENCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".pdf", ".webp"})
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
EXPIRING_DAYS = 30
STATUSES = ("valid", "expiring", "expired", "none")

DEFAULT_ITEMS = (
    {
        "key": "business_license",
        "label": "营业执照",
        "kinds": ("license",),
        "hint": "需在有效期内",
    },
    {
        "key": "qualification_cert",
        "label": "资质证书",
        "kinds": ("license",),
        "hint": "等级与承包范围覆盖本项目",
    },
    {
        "key": "safety_license",
        "label": "安全生产许可证",
        "kinds": ("safety",),
        "hint": "施工类项目必备",
    },
    {
        "key": "project_manager",
        "label": "项目负责人注册证书",
        "kinds": ("person",),
        "hint": "建造师/监理工程师，注意注册单位一致",
    },
    {
        "key": "performance",
        "label": "类似业绩证明",
        "kinds": ("performance",),
        "hint": "合同/中标通知书/验收单",
    },
    {
        "key": "credit",
        "label": "信用记录（信用中国/执行信息/严重违法失信）",
        "kinds": ("credit",),
        "hint": "开标当日查询并截图存证",
    },
)


def _user_id(user) -> str:
    if isinstance(user, dict):
        value = user.get("id")
    else:
        value = getattr(user, "id", None)
    return str(value or "")


def evidence_dir(user_id: str) -> Path:
    """Return (and create) the fixed evidence directory for one user."""

    safe = Path(str(user_id or "")).name
    root = Path(settings.home) / "qualifications" / safe
    root.mkdir(parents=True, exist_ok=True)
    return root


def _as_date(value) -> date | None:
    """Coerce a stored value or request string to a date (``None`` when empty)."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError("日期格式无效，应为 YYYY-MM-DD") from exc


def _as_datetime(value) -> datetime | None:
    parsed = _as_date(value)
    if parsed is None:
        return None
    return datetime(parsed.year, parsed.month, parsed.day)


def _iso_date(value: datetime | date | None) -> str | None:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed is not None else None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat()


def _today(today: date | datetime | None = None) -> date:
    if today is None:
        return date.today()
    if isinstance(today, datetime):
        return today.date()
    return today


def status_of(valid_to, *, today: date | datetime | None = None) -> str:
    """``none`` (no expiry) / ``expired`` / ``expiring`` (≤30d) / ``valid``."""

    due = _as_date(valid_to)
    if due is None:
        return "none"
    current = _today(today)
    if due < current:
        return "expired"
    if (due - current).days <= EXPIRING_DAYS:
        return "expiring"
    return "valid"


def days_left(valid_to, *, today: date | datetime | None = None) -> int | None:
    due = _as_date(valid_to)
    if due is None:
        return None
    return (due - _today(today)).days


def qualification_payload(row: Qualification, *, today: date | None = None) -> dict:
    """Flatten one qualification row for the API/SPA."""

    due = _as_date(row.valid_to)
    evidence = [
        entry
        for entry in (row.evidence or [])
        if isinstance(entry, dict) and entry.get("filename")
    ]
    return {
        "id": row.id,
        "kind": row.kind,
        "kind_label": KIND_LABELS.get(row.kind, KIND_LABELS["other"]),
        "name": row.name or "",
        "holder": row.holder or "",
        "cert_no": row.cert_no or "",
        "issuer": row.issuer or "",
        "valid_from": _iso_date(row.valid_from),
        "valid_to": due.isoformat() if due is not None else None,
        "note": row.note or "",
        "status": status_of(due, today=today),
        "days_left": days_left(due, today=today),
        "evidence": evidence,
        "evidence_count": len(evidence),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


#: 契约里的短名别名。
payload = qualification_payload


def _check_kind(kind: str) -> str:
    value = str(kind or "").strip()
    if value not in KINDS:
        raise ValueError(f"资质类型无效：{value or '（空）'}")
    return value


def _check_text(value, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 字")
    return text


def list_qualifications(
    db, user, *, kind: str = "", status: str = ""
) -> list[Qualification]:
    """Owner-scoped list ordered by ``valid_to`` ascending, no-expiry last."""

    uid = _user_id(user)
    stmt = select(Qualification).where(Qualification.owner_id == uid)
    wanted_kind = str(kind or "").strip()
    if wanted_kind:
        stmt = stmt.where(Qualification.kind == _check_kind(wanted_kind))
    stmt = stmt.order_by(
        Qualification.valid_to.is_(None),
        Qualification.valid_to.asc(),
        Qualification.created_at.asc(),
    )
    wanted_status = str(status or "").strip()
    if wanted_status and wanted_status not in STATUSES:
        raise ValueError(f"状态筛选无效：{wanted_status}")
    rows = list(db.scalars(stmt))
    if wanted_status:
        rows = [row for row in rows if status_of(row.valid_to) == wanted_status]
    return rows


def get_qualification(db, user, qualification_id: str) -> Qualification:
    row = db.get(Qualification, str(qualification_id or ""))
    if row is None or row.owner_id != _user_id(user):
        raise LookupError("资质不存在")
    return row


def _values(data: dict, *, existing: Qualification | None) -> dict:
    """Validate a create/update payload and return column values to assign."""

    values: dict = {}
    if existing is None or "kind" in data:
        raw_kind = data.get("kind") if "kind" in data else ""
        values["kind"] = _check_kind(raw_kind or "other")
    if existing is None or "name" in data:
        name = _check_text(data.get("name"), "名称", 120)
        if not name:
            raise ValueError("名称不能为空")
        values["name"] = name
    for field, label, limit in (
        ("holder", "持有人", 120),
        ("cert_no", "证书号", 80),
        ("issuer", "发证机关", 120),
        ("note", "备注", 500),
    ):
        if field in data:
            values[field] = _check_text(data.get(field), label, limit)
    for field in ("valid_from", "valid_to"):
        if field in data:
            values[field] = _as_datetime(data.get(field))

    start = values.get("valid_from", existing.valid_from if existing else None)
    end = values.get("valid_to", existing.valid_to if existing else None)
    if start is not None and end is not None and end < start:
        raise ValueError("到期日不能早于起始日")
    return values


def create_qualification(db, user, data: dict) -> Qualification:
    row = Qualification(owner_id=_user_id(user), **_values(data, existing=None))
    db.add(row)
    db.flush()
    return row


def update_qualification(db, user, qualification_id: str, data: dict) -> Qualification:
    row = get_qualification(db, user, qualification_id)
    for field, value in _values(data, existing=row).items():
        setattr(row, field, value)
    db.flush()
    return row


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


def add_evidence(db, row: Qualification, filename: str | None, content: bytes) -> dict:
    """Store one certificate/screenshot and append it to ``row.evidence``."""

    name = _safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in EVIDENCE_SUFFIXES:
        raise ValueError("证据仅支持 PNG/JPG/JPEG/WEBP/PDF 格式")
    data = content or b""
    if not data:
        raise ValueError("证据文件不能为空")
    if len(data) > MAX_EVIDENCE_BYTES:
        raise ValueError("证据文件不能超过 10MB")

    root = evidence_dir(row.owner_id)
    target = _unique_path(root, name)
    target.write_bytes(data)
    entry = {
        "filename": target.name,
        "path": str(target),
        "size": len(data),
        "mime": mimetypes.guess_type(name)[0] or "",
        "uploaded_at": _iso(utcnow()),
    }
    row.evidence = list(row.evidence or []) + [entry]
    db.flush()
    return entry


def evidence_path(db, row: Qualification, filename: str) -> Path:
    """Resolve one evidence file, rejecting unknown names and path escapes."""

    name = Path(str(filename or "").replace("\\", "/")).name
    known = {
        str(entry.get("filename"))
        for entry in (row.evidence or [])
        if isinstance(entry, dict)
    }
    if not name or name not in known:
        raise LookupError("证据文件不存在")
    root = evidence_dir(row.owner_id).resolve()
    target = (root / name).resolve()
    if root not in target.parents or not target.is_file():
        raise LookupError("证据文件不存在")
    return target


def delete_evidence(db, row: Qualification, filename: str) -> str:
    """Remove one evidence entry and its file; a missing file is still a success."""

    name = Path(str(filename or "").replace("\\", "/")).name
    items = list(row.evidence or [])
    known = {
        str(entry.get("filename"))
        for entry in items
        if isinstance(entry, dict)
    }
    if not name or name not in known:
        raise LookupError("证据文件不存在")
    root = evidence_dir(row.owner_id).resolve()
    target = (root / name).resolve()
    if root not in target.parents:
        raise LookupError("证据文件不存在")
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
    row.evidence = [
        entry
        for entry in items
        if not (isinstance(entry, dict) and entry.get("filename") == name)
    ]
    db.flush()
    return name


def _remove_evidence(row: Qualification) -> None:
    root = evidence_dir(row.owner_id).resolve()
    for entry in row.evidence or []:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("path") or "")
        if not raw:
            continue
        try:
            target = Path(raw).resolve()
        except OSError:
            continue
        if root in target.parents:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass


def delete_qualification(db, user, qualification_id: str) -> Qualification:
    row = get_qualification(db, user, qualification_id)
    _remove_evidence(row)
    db.delete(row)
    db.flush()
    return row


def _pick_latest(rows: list[Qualification]) -> Qualification | None:
    if not rows:
        return None
    # None (永不失效) 视为最晚；其余按日期取最大。
    return max(
        rows,
        key=lambda row: (row.valid_to is None, _as_date(row.valid_to) or date.min),
    )


def _item_status(row: Qualification, deadline: date) -> str:
    due = _as_date(row.valid_to)
    if due is None:
        return "ok"
    if due < deadline:
        return "expired"
    if (due - deadline).days <= EXPIRING_DAYS:
        return "expiring"
    return "ok"


def preflight(db, user, *, deadline: date | None = None) -> dict:
    """Build the pre-bid self-check list against one opening date."""

    due = _today(deadline)
    rows = list_qualifications(db, user)
    items: list[dict] = []
    counts = {"ok": 0, "warn": 0, "missing": 0, "total": len(DEFAULT_ITEMS)}
    for spec in DEFAULT_ITEMS:
        matching = [row for row in rows if row.kind in spec["kinds"]]
        picked = _pick_latest(matching)
        if picked is None:
            status = "missing"
            evidence_count = 0
        else:
            status = _item_status(picked, due)
            evidence_count = len(
                [
                    entry
                    for entry in (picked.evidence or [])
                    if isinstance(entry, dict) and entry.get("filename")
                ]
            )
        if status == "ok":
            counts["ok"] += 1
        elif status == "missing":
            counts["missing"] += 1
        else:
            counts["warn"] += 1
        items.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "kinds": list(spec["kinds"]),
                "hint": spec["hint"],
                "status": status,
                "qualification_id": picked.id if picked is not None else None,
                "name": (picked.name if picked is not None else ""),
                "valid_to": (
                    _iso_date(picked.valid_to) if picked is not None else None
                ),
                "evidence_count": evidence_count,
            }
        )
    return {
        "deadline": due.isoformat(),
        "items": items,
        "summary": counts,
    }
