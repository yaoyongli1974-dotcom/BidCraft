"""开票申请与开具（发票文件由管理员上传，系统不接第三方开票服务）。"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InvoiceRequest, User, new_id, utcnow

MAX_INVOICE_BYTES = 10 * 1024 * 1024
INVOICE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
STATUSES = ("requested", "issued", "rejected")
STATUS_LABELS = {"requested": "待开具", "issued": "已开具", "rejected": "已驳回"}


class InvoiceError(ValueError):
    """Invalid invoice request."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def invoice_payload(row: InvoiceRequest, *, username: str = "") -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": username,
        "title": row.title,
        "tax_no": row.tax_no or "",
        "email": row.email or "",
        "amount_cents": int(row.amount_cents or 0),
        "note": row.note or "",
        "status": row.status,
        "status_label": STATUS_LABELS.get(row.status, row.status),
        "invoice_no": row.invoice_no or "",
        "reject_reason": row.reject_reason or "",
        "has_file": bool(row.file_path),
        "issued_at": _iso(row.issued_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _validate(title: str, amount_cents: int, tax_no: str, email: str, note: str) -> tuple[str, int, str, str, str]:
    title = (title or "").strip()
    if not title:
        raise InvoiceError("发票抬头不能为空")
    if len(title) > 200:
        raise InvoiceError("发票抬头不能超过 200 字")
    try:
        amount = int(amount_cents)
    except (TypeError, ValueError) as exc:
        raise InvoiceError("开票金额必须为整数（分）") from exc
    if amount <= 0:
        raise InvoiceError("开票金额必须大于 0")
    tax_no = (tax_no or "").strip()
    if len(tax_no) > 80:
        raise InvoiceError("税号不能超过 80 字")
    email = (email or "").strip()
    if email and ("@" not in email or len(email) > 200):
        raise InvoiceError("收票邮箱格式不正确")
    note = (note or "").strip()
    if len(note) > 500:
        raise InvoiceError("备注不能超过 500 字")
    return title, amount, tax_no, email, note


def create_request(
    db: Session,
    user: User,
    *,
    title: str,
    amount_cents: int,
    tax_no: str = "",
    email: str = "",
    note: str = "",
) -> InvoiceRequest:
    title, amount, tax_no, email, note = _validate(title, amount_cents, tax_no, email, note)
    row = InvoiceRequest(
        id=new_id(),
        user_id=user.id,
        title=title,
        tax_no=tax_no,
        email=email,
        amount_cents=amount,
        note=note,
        status="requested",
    )
    db.add(row)
    db.flush()
    return row


def list_requests(db: Session, user_id: str, limit: int = 50) -> list[InvoiceRequest]:
    return list(
        db.scalars(
            select(InvoiceRequest)
            .where(InvoiceRequest.user_id == user_id)
            .order_by(InvoiceRequest.created_at.desc())
            .limit(max(1, min(200, limit)))
        )
    )


def list_all(db: Session, status: str = "", limit: int = 200) -> list[InvoiceRequest]:
    statement = select(InvoiceRequest).order_by(InvoiceRequest.created_at.desc())
    if status:
        if status not in STATUSES:
            raise InvoiceError("状态不合法")
        statement = statement.where(InvoiceRequest.status == status)
    return list(db.scalars(statement.limit(max(1, min(500, limit)))))


def get_request(db: Session, invoice_id: str) -> InvoiceRequest:
    row = db.get(InvoiceRequest, invoice_id)
    if row is None:
        raise LookupError("开票申请不存在")
    return row


def file_dir(user_id: str) -> Path:
    from .config import settings

    return settings.home / "invoices" / user_id


def issue_request(
    db: Session,
    invoice_id: str,
    *,
    invoice_no: str,
    filename: str,
    content: bytes,
) -> InvoiceRequest:
    row = get_request(db, invoice_id)
    if row.status == "issued":
        raise InvoiceError("该申请已开具")
    if row.status == "rejected":
        raise InvoiceError("该申请已驳回，无法开具")
    invoice_no = (invoice_no or "").strip()
    if not invoice_no:
        raise InvoiceError("发票号不能为空")
    if len(invoice_no) > 80:
        raise InvoiceError("发票号不能超过 80 字")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in INVOICE_SUFFIXES:
        raise InvoiceError("发票文件仅支持 PDF / PNG / JPG")
    if not content:
        raise InvoiceError("发票文件为空")
    if len(content) > MAX_INVOICE_BYTES:
        raise InvoiceError("发票文件不能超过 10MB")
    target_dir = file_dir(row.user_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{row.id}{suffix}"
    target.write_bytes(content)
    row.status = "issued"
    row.invoice_no = invoice_no
    row.file_path = str(target)
    row.reject_reason = ""
    row.issued_at = utcnow()
    db.flush()
    return row


def reject_request(db: Session, invoice_id: str, reason: str) -> InvoiceRequest:
    row = get_request(db, invoice_id)
    if row.status != "requested":
        raise InvoiceError("仅待开具的申请可以驳回")
    reason = (reason or "").strip()
    if not reason:
        raise InvoiceError("请填写驳回原因")
    if len(reason) > 500:
        raise InvoiceError("驳回原因不能超过 500 字")
    row.status = "rejected"
    row.reject_reason = reason
    db.flush()
    return row


def invoice_file(db: Session, invoice_id: str, user_id: str | None = None) -> Path:
    row = get_request(db, invoice_id)
    if user_id is not None and row.user_id != user_id:
        raise LookupError("开票申请不存在")
    if not row.file_path:
        raise LookupError("发票文件不存在")
    path = Path(row.file_path)
    if not path.is_file():
        raise LookupError("发票文件不存在")
    return path


def delete_user_files(user_id: str) -> None:
    shutil.rmtree(file_dir(user_id), ignore_errors=True)
