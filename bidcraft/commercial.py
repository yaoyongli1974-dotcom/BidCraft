"""商务标自动编制：确定性模板 + 已审批报价版本装配。

商务标不是生成式内容：投标函、开标一览、分项报价、资格响应、业绩、人员与
商务偏离表全部来自项目事实台账、已审批的 ``BoqQuoteVersion``（
``status == "approved"``）以及 ``Qualification`` / ``Personnel`` 台账。缺失字段
一律写 ``{{待填:字段}}``，绝不使用活动清单快照冒充定稿，也不编造价格、资质或
人员。

状态机与报价版本对齐（draft → submitted → approved / rejected），审批链读取
``project.meta["approval_chain"]``，复用 :mod:`bidcraft.boq` 的链归一化与阶段
判定函数，不复制另一套链语义。
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, boq, clock, personnel, qualifications
from .audit import record_audit
from .models import (
    BoqQuoteVersion,
    CommercialDoc,
    Personnel,
    Project,
    ProjectMember,
    Qualification,
    utcnow,
)
from .services import money

COMMERCIAL_STATUSES = ("draft", "submitted", "approved", "rejected")
REVIEW_DECISIONS = ("approve", "reject")
NAME_MAX = 120
NOTE_MAX = 200
MONEY_TOLERANCE = 0.01
FORCE_REASON_MIN_CHARS = 4
PENDING_APPROVALS_LIMIT = 100

QUALIFICATION_STATUS_LABELS = {
    "valid": "有效",
    "expiring": "即将到期",
    "expired": "已过期",
    "none": "长期有效",
}

SECTION_ORDER = (
    "bid_letter",
    "opening_sheet",
    "price_schedule",
    "qualification_checklist",
    "performance_table",
    "personnel_table",
    "deviation_table",
)

SECTION_TITLES = {
    "bid_letter": "投标函",
    "opening_sheet": "开标一览表",
    "price_schedule": "分项报价表",
    "qualification_checklist": "资格响应表",
    "performance_table": "业绩表",
    "personnel_table": "人员表",
    "deviation_table": "商务偏离表",
}

QUALIFICATION_KINDS = {
    "license": "资质证书",
    "safety": "安全生产",
    "person": "人员证书",
    "performance": "业绩",
    "credit": "信用",
    "other": "其他",
}

#: 商务标必填字段：``(section, field, label)``。表格类字段取自 section.fields。
REQUIRED_FIELDS = (
    ("bid_letter", "project_name", "项目名称"),
    ("bid_letter", "project_number", "项目编号"),
    ("bid_letter", "buyer", "招标人"),
    ("bid_letter", "bidder", "投标人"),
    ("bid_letter", "bid_amount", "投标报价（含税）"),
    ("bid_letter", "currency", "币种"),
    ("opening_sheet", "total_amount", "投标报价"),
    ("opening_sheet", "total_with_tax", "含税合计"),
    ("opening_sheet", "currency", "币种"),
    ("opening_sheet", "bid_validity", "投标有效期"),
    ("price_schedule", "total_amount", "报价合计"),
    ("qualification_checklist", "requirements", "资格要求响应"),
    ("personnel_table", "project_manager", "项目负责人"),
    ("deviation_table", "response", "商务偏离说明"),
)


class CommercialConflictError(ValueError):
    """商务标状态或数据冲突（HTTP 409）。"""


class CommercialForbiddenError(Exception):
    """当前用户无权执行该商务标操作（HTTP 403）。"""


# ------------------------------------------------------------------ helpers


def _placeholder(label: str) -> str:
    return "{{待填:" + label + "}}"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _missing(value: Any) -> bool:
    text = _text(value)
    return not text or text.startswith("{{待填:")


def _iso(value) -> str | None:
    return clock.iso_utc(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _same_money(left: Any, right: Any) -> bool:
    a = _number(left)
    b = _number(right)
    if a is None or b is None:
        return a is b
    return abs(a - b) <= MONEY_TOLERANCE


def _clean_name(name: str | None) -> str:
    value = _text(name)
    if not value:
        raise ValueError("商务标名称不能为空")
    if len(value) > NAME_MAX:
        raise ValueError(f"商务标名称不能超过 {NAME_MAX} 个字符")
    return value


def _clean_note(note: str | None) -> str:
    return _text(note)[:NOTE_MAX]


def _actor_name(actor: dict | None) -> str:
    if not isinstance(actor, dict):
        return ""
    return _text(actor.get("display_name") or actor.get("username"))[:80]


def _audit_actor(actor: dict | str | None) -> dict | None:
    if isinstance(actor, dict):
        return actor
    return None


def _facts(db: Session, project_id: str) -> dict[str, str]:
    from .models import Fact

    rows = db.scalars(select(Fact).where(Fact.project_id == project_id))
    return {row.key: _text(row.value) for row in rows if _text(row.value)}


def _fact(facts: dict[str, str], key: str, label: str) -> str:
    value = _text(facts.get(key))
    return value or _placeholder(label)


def _owner_user(project: Project) -> dict[str, str]:
    """项目负责人的用户身份，用于复用「私有 + 企业共享」可见性规则。"""

    return {"id": project.owner_id or ""}


def _qualifications(db: Session, project: Project) -> list[Qualification]:
    """项目负责人私有资质 + 其可访问企业的共享资质/业绩。"""

    if not project.owner_id:
        return []
    return list(qualifications.list_visible_qualifications(db, _owner_user(project)))


def _personnel(db: Session, project: Project) -> list[Personnel]:
    """项目负责人私有人员 + 其可访问企业的共享人员。"""

    if not project.owner_id:
        return []
    return list(personnel.list_visible_personnel(db, _owner_user(project)))


def _qualification_status(row: Qualification) -> str:
    return qualifications.status_of(row.valid_to)


def _qualification_warning(row: Qualification) -> str | None:
    status = _qualification_status(row)
    if status == "expired":
        return f"资质「{_text(row.name)}」已过期，不得用于投标响应"
    if status == "expiring":
        return f"资质「{_text(row.name)}」即将到期（{qualifications.days_left(row.valid_to)} 天）"
    return None


def _personnel_status(row: Personnel) -> str:
    return "locked" if row.locked else "available"


def _personnel_warning(row: Personnel) -> str | None:
    if row.locked:
        return f"人员「{_text(row.name)}」在建锁定，不可作为本项目负责人"
    return None


def _asset_warnings(sections: dict[str, Any]) -> list[str]:
    """Collect the qualification/personnel flags shown in the section tables."""

    warnings: list[str] = []
    for key in (
        "qualification_checklist",
        "performance_table",
        "personnel_table",
    ):
        section = sections.get(key) or {}
        if not isinstance(section, dict):
            continue
        for note in section.get("notes") or []:
            text = _text(note)
            if text and text not in warnings:
                warnings.append(text)
    return warnings


def _latest_approved_version(
    db: Session, project_id: str, package_id: str | None
) -> BoqQuoteVersion | None:
    statement = select(BoqQuoteVersion).where(
        BoqQuoteVersion.project_id == project_id,
        BoqQuoteVersion.status == "approved",
    )
    if package_id is None:
        statement = statement.where(BoqQuoteVersion.package_id.is_(None))
    else:
        statement = statement.where(BoqQuoteVersion.package_id == package_id)
    statement = statement.order_by(
        BoqQuoteVersion.reviewed_at.desc(),
        BoqQuoteVersion.created_at.desc(),
        BoqQuoteVersion.id.desc(),
    )
    return db.scalars(statement).first()


def _quote_reference(
    version: BoqQuoteVersion | None, *, policy: dict[str, Any], package: Any
) -> dict[str, Any]:
    """Build the immutable quote reference embedded in the assembled sections.

    Prices and totals are exposed only for an approved version; an unapproved
    version keeps its identity (for the explicit marker) but never its amounts.
    """
    if version is None:
        return {
            "version_id": None,
            "name": "",
            "status": "",
            "is_approved": False,
            "available": False,
            "package_id": package.id if package else None,
            "package_name": package.name if package else "",
            "currency": str(policy.get("currency") or "CNY"),
            "tax_rate": _number(policy.get("tax_rate")),
            "total_amount": None,
            "tax_amount": None,
            "total_with_tax": None,
            "cny_total_with_tax": None,
            "reviewed_by": "",
            "reviewed_at": None,
            "quantities": {},
            "blocked_reason": "尚无已审批报价版本，不得以活动清单报价定稿",
        }
    status = version.status or "draft"
    approved = status == "approved"
    basis = dict(version.basis or {})
    summary = dict(version.summary or {})
    currency = _text(basis.get("currency")) or _text(policy.get("currency")) or "CNY"
    tax_rate = _number(basis.get("tax_rate"))
    if tax_rate is None:
        tax_rate = _number(policy.get("tax_rate"))
    quote = {
        "version_id": version.id,
        "name": version.name,
        "status": status,
        "is_approved": approved,
        "available": approved,
        "package_id": version.package_id,
        "package_name": package.name if package else "",
        "currency": currency,
        "tax_rate": tax_rate,
        "total_amount": _number(summary.get("total_amount")) if approved else None,
        "tax_amount": _number(summary.get("tax_amount")) if approved else None,
        "total_with_tax": _number(summary.get("total_with_tax")) if approved else None,
        "cny_total_with_tax": (
            _number(summary.get("cny_total_with_tax")) if approved else None
        ),
        "reviewed_by": _text(version.reviewed_by),
        "reviewed_at": _iso(version.reviewed_at),
        "quantities": dict(basis.get("qty") or {}),
        "blocked_reason": (
            ""
            if approved
            else f"引用的报价版本「{version.name}」状态为 {status}，未审批，"
            "不得作为商务标定稿依据"
        ),
    }
    return quote


def _price_rows(
    db: Session, project: Project, quote: dict[str, Any]
) -> list[dict[str, Any]]:
    if not quote.get("is_approved"):
        return []
    prices = _price_map(db, project.id, quote["version_id"])
    if not prices:
        return []
    snapshot = boq.get_snapshot(db, project.id, quote.get("package_id"))
    items = list(getattr(snapshot, "items", None) or [])
    by_row = {item.get("row"): item for item in items if isinstance(item, dict)}
    quantities = quote.get("quantities") or {}
    rows: list[dict[str, Any]] = []
    for key, unit_price in prices.items():
        sheet, _, row_text = str(key).rpartition("!")
        try:
            row_no = int(row_text)
        except (TypeError, ValueError):
            row_no = None
        item = by_row.get(row_no) if row_no is not None else None
        item = item or {}
        qty = _number(quantities.get(key))
        if qty is None:
            qty = _number(item.get("qty"))
        price = _number(unit_price)
        amount = money.line_amount(qty, price) if qty is not None else None
        rows.append(
            {
                "seq": item.get("seq") or len(rows) + 1,
                "sheet": sheet,
                "row": row_no,
                "name": _text(item.get("name")) or _placeholder("清单名称"),
                "spec": _text(item.get("spec")),
                "unit": _text(item.get("unit")),
                "qty": qty,
                "unit_price": price,
                "amount": amount,
                "note": _text(item.get("note")),
            }
        )
    rows.sort(key=lambda row: (str(row.get("sheet") or ""), row.get("row") or 0))
    for index, row in enumerate(rows, start=1):
        row["seq"] = index
    return rows


def _price_map(db: Session, project_id: str, version_id: str | None) -> dict[str, Any]:
    if not version_id:
        return {}
    version = boq.get_version(db, project_id, version_id)
    if version is None or (version.status or "draft") != "approved":
        return {}
    return dict(version.prices or {})


def _quote_total(quote: dict[str, Any], rows: list[dict[str, Any]]) -> float | None:
    total = _number(quote.get("total_amount"))
    if total is not None:
        return total
    if not rows:
        return None
    return money.sum_amounts(
        [row["amount"] for row in rows if _number(row.get("amount")) is not None]
    )


# --------------------------------------------------------------- section builders


def build_bid_letter(
    db: Session,
    project: Project,
    *,
    facts: dict[str, str] | None = None,
    quote: dict[str, Any],
    package: Any = None,
) -> dict[str, Any]:
    """投标函：项目/投标人/金额均取台账或已审批报价，缺失写占位符。"""
    facts = facts if facts is not None else _facts(db, project.id)
    bid_amount = quote.get("total_with_tax")
    if _number(bid_amount) is None:
        bid_amount = _placeholder("投标报价（须引用已审批报价版本）")
    tax_rate = quote.get("tax_rate")
    fields = [
        {"key": "project_name", "label": "项目名称", "value": _text(facts.get("project.name")) or project.name},
        {"key": "project_number", "label": "项目编号", "value": _fact(facts, "project.number", "项目编号")},
        {"key": "buyer", "label": "招标人", "value": _text(facts.get("project.buyer")) or _text(project.buyer) or _placeholder("招标人")},
        {"key": "bidder", "label": "投标人", "value": _fact(facts, "bidder.name", "投标人名称")},
        {"key": "package_name", "label": "包件", "value": (package.name if package else "整体项目")},
        {"key": "bid_amount", "label": "投标报价（含税）", "value": bid_amount},
        {"key": "total_amount", "label": "报价合计（不含税）", "value": quote.get("total_amount") if _number(quote.get("total_amount")) is not None else _placeholder("报价合计")},
        {"key": "tax_rate", "label": "税率", "value": tax_rate if tax_rate is not None else _placeholder("税率")},
        {"key": "currency", "label": "币种", "value": _text(quote.get("currency")) or _placeholder("币种")},
        {"key": "duration", "label": "工期", "value": _fact(facts, "project.duration", "工期")},
        {"key": "bid_validity", "label": "投标有效期", "value": _fact(facts, "project.bid_validity", "投标有效期")},
        {"key": "warranty", "label": "质保期", "value": _fact(facts, "project.warranty", "质保期")},
        {"key": "date", "label": "日期", "value": clock.today().strftime("%Y 年 %m 月 %d 日")},
    ]
    return {
        "key": "bid_letter",
        "kind": "letter",
        "title": SECTION_TITLES["bid_letter"],
        "fields": fields,
        "rows": [],
        "columns": [],
        "notes": [],
    }


def build_opening_sheet(
    db: Session,
    project: Project,
    *,
    facts: dict[str, str] | None = None,
    quote: dict[str, Any],
    package: Any = None,
    price_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """开标一览表：一包件一行，金额只来自已审批报价版本。"""
    facts = facts if facts is not None else _facts(db, project.id)
    rows = price_rows or []
    total = _quote_total(quote, rows)
    total_with_tax = quote.get("total_with_tax")
    if _number(total_with_tax) is None:
        total_with_tax = _placeholder("含税合计")
    row = {
        "package": package.name if package else "整体项目",
        "bidder": _fact(facts, "bidder.name", "投标人名称"),
        "total_amount": total if total is not None else _placeholder("投标报价"),
        "tax_rate": quote.get("tax_rate") if _number(quote.get("tax_rate")) is not None else _placeholder("税率"),
        "total_with_tax": total_with_tax,
        "currency": _text(quote.get("currency")) or _placeholder("币种"),
        "duration": _fact(facts, "project.duration", "工期"),
        "bid_validity": _fact(facts, "project.bid_validity", "投标有效期"),
    }
    return {
        "key": "opening_sheet",
        "kind": "table",
        "title": SECTION_TITLES["opening_sheet"],
        "columns": [
            {"key": "package", "label": "包件"},
            {"key": "bidder", "label": "投标人"},
            {"key": "total_amount", "label": "投标报价（元）"},
            {"key": "tax_rate", "label": "税率"},
            {"key": "total_with_tax", "label": "含税合计（元）"},
            {"key": "currency", "label": "币种"},
            {"key": "duration", "label": "工期"},
            {"key": "bid_validity", "label": "投标有效期"},
        ],
        "rows": [row],
        "fields": [],
        "totals": {
            "total_amount": total,
            "tax_rate": quote.get("tax_rate"),
            "total_with_tax": _number(quote.get("total_with_tax")),
            "currency": _text(quote.get("currency")),
            "package_id": quote.get("package_id"),
        },
        "notes": [] if quote.get("is_approved") else [quote.get("blocked_reason") or "尚无已审批报价版本"],
    }


def build_price_schedule(
    db: Session,
    project: Project,
    *,
    quote: dict[str, Any],
    package: Any = None,
) -> dict[str, Any]:
    """分项报价表：逐行来自已审批版本的单价与冻结数量。"""
    rows = _price_rows(db, project, quote)
    total = _quote_total(quote, rows)
    return {
        "key": "price_schedule",
        "kind": "table",
        "title": SECTION_TITLES["price_schedule"],
        "columns": [
            {"key": "seq", "label": "序号"},
            {"key": "name", "label": "名称"},
            {"key": "spec", "label": "规格型号"},
            {"key": "unit", "label": "单位"},
            {"key": "qty", "label": "数量"},
            {"key": "unit_price", "label": "单价（元）"},
            {"key": "amount", "label": "合价（元）"},
            {"key": "note", "label": "备注"},
        ],
        "rows": rows,
        "fields": [],
        "totals": {
            "total_amount": total,
            "tax_rate": quote.get("tax_rate"),
            "total_with_tax": _number(quote.get("total_with_tax")),
            "currency": _text(quote.get("currency")),
            "package_id": quote.get("package_id"),
        },
        "notes": [] if rows else ["尚无已审批报价明细，分项报价表待填"],
    }


def build_qualification_checklist(
    db: Session,
    project: Project,
    *,
    facts: dict[str, str] | None = None,
    rows: list[Qualification] | None = None,
) -> dict[str, Any]:
    """资格响应表：招标资格要求（台账）+ 已登记资质证书。"""
    facts = facts if facts is not None else _facts(db, project.id)
    rows = rows if rows is not None else _qualifications(db, project)
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        if row.kind == "performance":
            continue
        status = _qualification_status(row)
        warning = _qualification_warning(row)
        if warning:
            warnings.append(warning)
        items.append(
            {
                "seq": len(items) + 1,
                "kind": QUALIFICATION_KINDS.get(row.kind, row.kind or "其他"),
                "name": _text(row.name),
                "holder": _text(row.holder),
                "cert_no": _text(row.cert_no) or _placeholder("证书编号"),
                "issuer": _text(row.issuer),
                "valid_to": _iso(row.valid_to) or _placeholder("有效期"),
                "status": status,
                "status_label": QUALIFICATION_STATUS_LABELS.get(status, status),
                "shared": bool(row.enterprise_id),
                "note": _text(row.note),
            }
        )
    return {
        "key": "qualification_checklist",
        "kind": "table",
        "title": SECTION_TITLES["qualification_checklist"],
        "fields": [
            {
                "key": "requirements",
                "label": "资格要求响应",
                "value": _fact(facts, "qualification.requirements", "资格要求响应"),
            }
        ],
        "columns": [
            {"key": "seq", "label": "序号"},
            {"key": "kind", "label": "类别"},
            {"key": "name", "label": "资质名称"},
            {"key": "holder", "label": "持有人"},
            {"key": "cert_no", "label": "证书编号"},
            {"key": "issuer", "label": "发证机关"},
            {"key": "valid_to", "label": "有效期至"},
            {"key": "status", "label": "状态"},
            {"key": "note", "label": "备注"},
        ],
        "rows": items,
        "notes": warnings
        or ([] if items else ["资质台账为空，请先登记资质或填写资格要求响应"]),
    }


def build_performance_table(
    db: Session,
    project: Project,
    *,
    rows: list[Qualification] | None = None,
) -> dict[str, Any]:
    """业绩表：只取 ``Qualification.kind == "performance"`` 的台账记录。"""
    rows = rows if rows is not None else _qualifications(db, project)
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        if row.kind != "performance":
            continue
        status = _qualification_status(row)
        warning = _qualification_warning(row)
        if warning:
            warnings.append(warning)
        items.append(
            {
                "seq": len(items) + 1,
                "name": _text(row.name),
                "holder": _text(row.holder) or _placeholder("发包人"),
                "cert_no": _text(row.cert_no),
                "valid_to": _iso(row.valid_to),
                "status": status,
                "status_label": QUALIFICATION_STATUS_LABELS.get(status, status),
                "shared": bool(row.enterprise_id),
                "note": _text(row.note),
            }
        )
    return {
        "key": "performance_table",
        "kind": "table",
        "title": SECTION_TITLES["performance_table"],
        "columns": [
            {"key": "seq", "label": "序号"},
            {"key": "name", "label": "项目名称"},
            {"key": "holder", "label": "发包人"},
            {"key": "cert_no", "label": "合同/证明材料编号"},
            {"key": "valid_to", "label": "日期"},
            {"key": "status", "label": "状态"},
            {"key": "note", "label": "备注"},
        ],
        "rows": items,
        "fields": [],
        "notes": warnings or ([] if items else ["业绩台账为空，请先登记业绩"]),
    }


def build_personnel_table(
    db: Session,
    project: Project,
    *,
    facts: dict[str, str] | None = None,
    rows: list[Personnel] | None = None,
) -> dict[str, Any]:
    """人员表：项目负责人取台账字段，缺失写占位符；人员明细来自台账。"""
    facts = facts if facts is not None else _facts(db, project.id)
    rows = rows if rows is not None else _personnel(db, project)
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        warning = _personnel_warning(row)
        if warning:
            warnings.append(warning)
        status = _personnel_status(row)
        items.append(
            {
                "seq": len(items) + 1,
                "name": _text(row.name),
                "title": _text(row.title),
                "specialty": _text(row.specialty),
                "cert_no": _text(row.cert_no) or _placeholder("证书编号"),
                "social_months": row.social_months,
                "locked": "是" if row.locked else "否",
                "status": status,
                "status_label": "在建锁定" if status == "locked" else "可用",
                "shared": bool(row.enterprise_id),
                "note": _text(row.note),
            }
        )
    return {
        "key": "personnel_table",
        "kind": "table",
        "title": SECTION_TITLES["personnel_table"],
        "fields": [
            {
                "key": "project_manager",
                "label": "项目负责人",
                "value": _fact(facts, "personnel.project_manager", "项目负责人"),
            }
        ],
        "columns": [
            {"key": "seq", "label": "序号"},
            {"key": "name", "label": "姓名"},
            {"key": "title", "label": "职务"},
            {"key": "specialty", "label": "专业"},
            {"key": "cert_no", "label": "证书编号"},
            {"key": "social_months", "label": "社保月份"},
            {"key": "locked", "label": "在建锁定"},
            {"key": "status", "label": "状态"},
            {"key": "note", "label": "备注"},
        ],
        "rows": items,
        "notes": warnings or ([] if items else ["人员台账为空，请先登记投标人员"]),
    }


def build_deviation_table(
    db: Session,
    project: Project,
    *,
    facts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """商务偏离表：只填台账已确认的偏离说明，缺失保持待填。"""
    facts = facts if facts is not None else _facts(db, project.id)
    response = _text(facts.get("commercial.deviation"))
    rows = [
        {
            "seq": 1,
            "item": "商务条款",
            "response": response or _placeholder("商务偏离说明"),
            "note": _text(facts.get("commercial.deviation_note")),
        }
    ]
    return {
        "key": "deviation_table",
        "kind": "table",
        "title": SECTION_TITLES["deviation_table"],
        "fields": [
            {
                "key": "response",
                "label": "商务偏离说明",
                "value": response or _placeholder("商务偏离说明"),
            }
        ],
        "columns": [
            {"key": "seq", "label": "序号"},
            {"key": "item", "label": "条款"},
            {"key": "response", "label": "响应/偏离"},
            {"key": "note", "label": "说明"},
        ],
        "rows": rows,
        "notes": [] if response else ["未登记商务偏离说明，请人工确认后填写"],
    }


# ------------------------------------------------------------------- assembly


def build_commercial_payload(
    db: Session,
    project: Project,
    *,
    package_id: str | None = None,
    quote_version_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the commercial bid from approved data only.

    ``quote_version_id`` pins a version; without it the latest approved version
    of the project/package is used. An explicit unapproved version is reported
    with ``ready=False`` and a clear blocked reason — its prices are never
    copied into the sections.
    """
    policy = boq.get_price_policy(project)
    requested = _text(quote_version_id) or None
    pinned: BoqQuoteVersion | None = None
    if requested:
        pinned = boq.get_version(db, project.id, requested)
        if pinned is None:
            raise LookupError("报价版本不存在")
        if not _text(package_id) and pinned.package_id:
            # 指定版本时可由版本自身推断包件，避免包件模式项目重复传参。
            package_id = pinned.package_id
    package = boq.resolve_package(db, project, package_id)
    resolved = package.id if package else None
    version: BoqQuoteVersion | None
    if requested:
        version = pinned
        if resolved is not None and version.package_id != resolved:
            raise ValueError("报价版本不属于所选包件")
    else:
        version = _latest_approved_version(db, project.id, resolved)
    quote = _quote_reference(version, policy=policy, package=package)
    facts = _facts(db, project.id)
    qualifications = _qualifications(db, project)
    people = _personnel(db, project)
    price_schedule = build_price_schedule(db, project, quote=quote, package=package)
    price_rows = list(price_schedule.get("rows") or [])
    sections = {
        "bid_letter": build_bid_letter(
            db, project, facts=facts, quote=quote, package=package
        ),
        "opening_sheet": build_opening_sheet(
            db,
            project,
            facts=facts,
            quote=quote,
            package=package,
            price_rows=price_rows,
        ),
        "price_schedule": price_schedule,
        "qualification_checklist": build_qualification_checklist(
            db, project, facts=facts, rows=qualifications
        ),
        "performance_table": build_performance_table(
            db, project, rows=qualifications
        ),
        "personnel_table": build_personnel_table(
            db, project, facts=facts, rows=people
        ),
        "deviation_table": build_deviation_table(db, project, facts=facts),
    }
    payload: dict[str, Any] = {
        "project": {
            "id": project.id,
            "name": project.name,
            "number": _text(facts.get("project.number")),
            "buyer": _text(project.buyer) or _text(facts.get("project.buyer")),
        },
        "package": (
            {"id": package.id, "name": package.name} if package else None
        ),
        "quote": quote,
        "sections": sections,
        "ready": bool(quote.get("is_approved")),
        "blocked_reason": "" if quote.get("is_approved") else quote.get("blocked_reason", ""),
        "generated_at": clock.iso_utc(utcnow()),
    }
    validation = validate_commercial_consistency(payload)
    validation["warnings"].extend(_asset_warnings(sections))
    payload["validation"] = validation
    payload["completeness"] = validate_commercial_completeness(payload)
    return payload


# ----------------------------------------------------------------- validation


def _section_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields = section.get("fields") or []
    result: dict[str, Any] = {}
    if isinstance(fields, list):
        for entry in fields:
            if isinstance(entry, dict) and entry.get("key"):
                result[str(entry["key"])] = entry.get("value")
    return result


def _first_row(section: dict[str, Any]) -> dict[str, Any]:
    rows = section.get("rows") or []
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def _check_money(
    errors: list[str], label: str, value: Any, expected: Any
) -> None:
    if _missing(value) or expected is None:
        return
    if not _same_money(value, expected):
        errors.append(f"{label}与报价版本不一致：表中 {value}，版本 {expected}")


def validate_commercial_consistency(
    payload: dict[str, Any], *, current_quote: dict[str, Any] | None = None
) -> dict[str, Any]:
    """校验商务标金额/税率/币种/包件与引用报价版本一致。

    ``current_quote`` 传入「当前已审批版本」时，还会检出商务标引用了被取代的
    旧版本（报价变更后旧值未重新汇编）。
    """
    errors: list[str] = []
    warnings: list[str] = []
    quote = payload.get("quote") or {}
    sections = payload.get("sections") or {}
    if not isinstance(sections, dict):
        sections = {}
    if not quote.get("is_approved"):
        errors.append(quote.get("blocked_reason") or "商务标未引用已审批报价版本")
    if current_quote is not None:
        current_id = _text(current_quote.get("version_id") or current_quote.get("id"))
        cited_id = _text(quote.get("version_id"))
        if current_id and cited_id != current_id:
            errors.append(
                "报价已变更：商务标引用了旧版本"
                f"（{cited_id or '无'}），当前已审批版本为 {current_id}，请重新汇编"
            )
    if quote.get("is_approved"):
        bid = _section_fields(sections.get("bid_letter") or {})
        opening = _first_row(sections.get("opening_sheet") or {})
        schedule = sections.get("price_schedule") or {}
        totals = schedule.get("totals") or {}
        expected_total = _number(quote.get("total_amount"))
        expected_tax = _number(quote.get("total_with_tax"))
        expected_rate = _number(quote.get("tax_rate"))
        expected_currency = _text(quote.get("currency"))
        expected_package = _text(quote.get("package_id"))
        _check_money(
            errors,
            "投标函含税报价",
            bid.get("bid_amount"),
            expected_tax,
        )
        _check_money(
            errors,
            "投标函报价合计",
            bid.get("total_amount"),
            expected_total,
        )
        _check_money(errors, "开标一览投标报价", opening.get("total_amount"), expected_total)
        _check_money(
            errors, "开标一览含税合计", opening.get("total_with_tax"), expected_tax
        )
        _check_money(errors, "分项报价合计", totals.get("total_amount"), expected_total)
        for label, value in (
            ("投标函税率", bid.get("tax_rate")),
            ("开标一览税率", opening.get("tax_rate")),
            ("分项报价税率", totals.get("tax_rate")),
        ):
            if _missing(value) or expected_rate is None:
                continue
            if not _same_money(value, expected_rate):
                errors.append(f"{label}与报价版本不一致：表中 {value}，版本 {expected_rate}")
        for label, value in (
            ("投标函币种", bid.get("currency")),
            ("开标一览币种", opening.get("currency")),
            ("分项报价币种", totals.get("currency")),
        ):
            text = _text(value)
            if not text or text.startswith("{{待填:"):
                continue
            if expected_currency and text != expected_currency:
                errors.append(f"{label}与报价版本不一致：表中 {text}，版本 {expected_currency}")
        for label, value in (
            ("开标一览包件", totals.get("package_id")),
            ("分项报价包件", totals.get("package_id")),
        ):
            text = _text(value)
            if text and expected_package and text != expected_package:
                errors.append(f"{label}与报价版本不一致：表中 {text}，版本 {expected_package}")
        rows = schedule.get("rows") or []
        amounts = [
            _number(row.get("amount"))
            for row in rows
            if isinstance(row, dict) and _number(row.get("amount")) is not None
        ]
        if amounts and expected_total is not None:
            row_total = money.sum_amounts(amounts)
            if not _same_money(row_total, expected_total):
                errors.append(
                    f"分项报价逐行合计（{row_total}）与报价版本合计"
                    f"（{expected_total}）不一致"
                )
        if not rows:
            warnings.append("分项报价表没有明细行")
    if not payload.get("ready"):
        warnings.append(payload.get("blocked_reason") or "商务标尚未满足定稿条件")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def validate_commercial_completeness(payload: dict[str, Any]) -> dict[str, Any]:
    """列出商务标必填字段的缺失清单（空值或 ``{{待填:...}}``）。"""
    missing: list[dict[str, str]] = []
    sections = payload.get("sections") or {}
    if not isinstance(sections, dict):
        sections = {}
    if not (payload.get("quote") or {}).get("is_approved"):
        missing.append(
            {
                "section": "quote",
                "field": "approved_version",
                "label": "已审批报价版本",
            }
        )
    for section_key, field_key, label in REQUIRED_FIELDS:
        section = sections.get(section_key) or {}
        if not isinstance(section, dict):
            section = {}
        values = dict(section.get("totals") or {})
        values.update(_section_fields(section))
        if _missing(values.get(field_key)):
            first = _first_row(section)
            if _missing(first.get(field_key)):
                missing.append(
                    {"section": section_key, "field": field_key, "label": label}
                )
    price_section = sections.get("price_schedule") or {}
    if not (price_section.get("rows") or []):
        missing.append(
            {"section": "price_schedule", "field": "rows", "label": "分项报价明细"}
        )
    return {"ok": not missing, "missing": missing, "checked": len(REQUIRED_FIELDS) + 2}


# ---------------------------------------------------------------- doc storage


def _validation_blob(payload: dict[str, Any]) -> dict[str, Any]:
    quote = dict(payload.get("quote") or {})
    quote.pop("quantities", None)
    return {
        "consistency": payload.get("validation") or {"ok": False, "errors": [], "warnings": []},
        "completeness": payload.get("completeness")
        or {"ok": False, "missing": [], "checked": 0},
        "quote": quote,
        "approvals": [],
        "review": {},
    }


def _doc_payload(doc: CommercialDoc) -> dict[str, Any]:
    return {
        "id": doc.id,
        "project_id": doc.project_id,
        "name": doc.name,
        "quote_version_id": doc.quote_version_id,
        "package_id": doc.package_id,
        "status": doc.status or "draft",
        "sections": dict(doc.sections or {}),
        "validation": dict(doc.validation or {}),
        "submit_note": doc.submit_note or "",
        "submitted_by": doc.submitted_by or "",
        "submitted_at": _iso(doc.submitted_at),
        "created_by": doc.created_by or "",
        "created_at": _iso(doc.created_at),
        "updated_at": _iso(doc.updated_at),
    }


def doc_payload(doc: CommercialDoc) -> dict[str, Any]:
    """Serialize one CommercialDoc for the API."""
    return _doc_payload(doc)


def list_commercial(
    db: Session, project: Project, package_id: str | None = None
) -> list[dict[str, Any]]:
    statement = select(CommercialDoc).where(CommercialDoc.project_id == project.id)
    if package_id is not None:
        statement = statement.where(CommercialDoc.package_id == package_id)
    statement = statement.order_by(
        CommercialDoc.updated_at.desc(), CommercialDoc.created_at.desc()
    )
    return [_doc_payload(row) for row in db.scalars(statement)]


def get_commercial(
    db: Session, project: Project, doc_id: str
) -> CommercialDoc | None:
    doc = db.get(CommercialDoc, doc_id)
    if doc is None or doc.project_id != project.id:
        return None
    return doc


def save_commercial(
    db: Session,
    project: Project,
    *,
    name: str = "商务标",
    quote_version_id: str | None = None,
    package_id: str | None = None,
    actor: dict | str | None = None,
) -> dict[str, Any]:
    """Assemble and persist a new draft commercial document."""
    clean_name = _clean_name(name)
    payload = build_commercial_payload(
        db, project, package_id=package_id, quote_version_id=quote_version_id
    )
    quote = payload.get("quote") or {}
    doc = CommercialDoc(
        project_id=project.id,
        name=clean_name,
        quote_version_id=quote.get("version_id") or _text(quote_version_id) or None,
        package_id=(payload.get("package") or {}).get("id"),
        status="draft",
        sections=payload.get("sections") or {},
        validation=_validation_blob(payload),
        created_by=_actor_name(actor if isinstance(actor, dict) else None),
    )
    db.add(doc)
    db.flush()
    record_audit(
        db,
        project.id,
        "commercial.save",
        {
            "doc_id": doc.id,
            "name": doc.name,
            "quote_version_id": doc.quote_version_id,
            "package_id": doc.package_id,
            "ready": payload.get("ready"),
        },
        actor=_audit_actor(actor),
    )
    return _doc_payload(doc)


def update_commercial(
    db: Session,
    project: Project,
    doc_id: str,
    *,
    name: str | None = None,
    sections: Any = None,
    actor: dict | str | None = None,
) -> dict[str, Any]:
    doc = get_commercial(db, project, doc_id)
    if doc is None:
        raise LookupError("商务标不存在")
    # 送审后的章节是审批对象：必须保持冻结，避免审批期间偷改内容。
    # 如需修改请先驳回（rejected）或撤销定稿（draft），重走提交流程会清空审批记录。
    if (doc.status or "draft") not in {"draft", "rejected"}:
        raise CommercialConflictError(
            "当前状态的商务标不可修改，请先驳回或撤销定稿后再编辑"
        )
    if name is not None:
        doc.name = _clean_name(name)
    if sections is not None:
        if not isinstance(sections, dict):
            raise ValueError("sections 必须是对象")
        doc.sections = sections
        _refresh_stored_validation(doc)
    db.flush()
    record_audit(
        db,
        project.id,
        "commercial.update",
        {"doc_id": doc.id, "name": doc.name},
        actor=_audit_actor(actor),
    )
    return _doc_payload(doc)


def _stored_payload(doc: CommercialDoc) -> dict[str, Any]:
    validation = dict(doc.validation or {})
    quote = dict(validation.get("quote") or {})
    return {
        "quote": quote,
        "sections": dict(doc.sections or {}),
        "package": {"id": doc.package_id} if doc.package_id else None,
        "ready": bool(quote.get("is_approved")),
        "blocked_reason": "" if quote.get("is_approved") else quote.get("blocked_reason", ""),
    }


def _refresh_stored_validation(doc: CommercialDoc) -> None:
    validation = dict(doc.validation or {})
    payload = _stored_payload(doc)
    consistency = validate_commercial_consistency(payload)
    consistency["warnings"].extend(_asset_warnings(payload.get("sections") or {}))
    validation["consistency"] = consistency
    validation["completeness"] = validate_commercial_completeness(payload)
    doc.validation = validation


def _submit_authorized(
    project: Project,
    *,
    actor_id: str,
    role: str,
    is_super: bool,
) -> None:
    if is_super or role == "owner":
        return
    chain = boq.normalize_chain((project.meta or {}).get("approval_chain"))
    approver_ids = {
        str(person.get("id") or "")
        for stage in chain
        for person in (stage.get("approvers") or [])
    }
    if actor_id and actor_id in approver_ids:
        return
    raise CommercialForbiddenError("仅项目负责人或审批链上的审批人可提交商务标")


def submit_commercial(
    db: Session,
    project: Project,
    doc_id: str,
    *,
    note: str = "",
    actor: dict | str | None = None,
    actor_id: str = "",
    role: str = "",
    is_super: bool = False,
    require_complete: bool = False,
) -> dict[str, Any]:
    """draft|rejected → submitted，提交前按当前已审批报价重新汇编。

    ``require_complete=True`` 时额外执行完整性硬门槛：必填项未填齐不得送审；
    缺省 False 保持既有行为（完整性仅作为提示）。
    """
    doc = get_commercial(db, project, doc_id)
    if doc is None:
        raise LookupError("商务标不存在")
    if (doc.status or "draft") not in {"draft", "rejected"}:
        raise CommercialConflictError("当前状态不可提交审批")
    _submit_authorized(project, actor_id=actor_id, role=role, is_super=is_super)
    payload = build_commercial_payload(
        db,
        project,
        package_id=doc.package_id,
        quote_version_id=doc.quote_version_id,
    )
    if not payload.get("ready"):
        raise CommercialConflictError(
            payload.get("blocked_reason") or "尚无已审批报价版本，不能提交商务标"
        )
    validation = payload.get("validation") or {}
    if not validation.get("ok"):
        detail = "；".join(validation.get("errors") or []) or "一致性校验未通过"
        raise CommercialConflictError(f"商务标一致性校验未通过：{detail}")
    if require_complete:
        completeness = payload.get("completeness") or validation.get("completeness") or {}
        if not completeness.get("ok", True):
            missing = completeness.get("missing") or []
            detail = "；".join(str(item) for item in missing[:8]) or "存在未填必填项"
            raise CommercialConflictError(f"商务标完整性校验未通过：{detail}")
    doc.sections = payload.get("sections") or {}
    doc.quote_version_id = (payload.get("quote") or {}).get("version_id")
    blob = _validation_blob(payload)
    blob["approvals"] = []
    blob["review"] = {}
    doc.validation = blob
    doc.status = "submitted"
    doc.submit_note = _clean_note(note)
    doc.submitted_by = _actor_name(actor if isinstance(actor, dict) else None)
    doc.submitted_at = utcnow()
    db.flush()
    record_audit(
        db,
        project.id,
        "commercial.submit",
        {
            "doc_id": doc.id,
            "name": doc.name,
            "quote_version_id": doc.quote_version_id,
            "note": doc.submit_note,
        },
        actor=_audit_actor(actor),
    )
    return _doc_payload(doc)


def _review_context(
    doc: CommercialDoc, project: Project
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[int, dict[str, Any]]], int]:
    validation = dict(doc.validation or {})
    chain = boq.normalize_chain((project.meta or {}).get("approval_chain"))
    approvals = list(validation.get("approvals") or [])
    quote = validation.get("quote") or {}
    total_amount = _number(quote.get("total_amount"))
    package_id = doc.package_id or quote.get("package_id")
    currency = _text(quote.get("currency"))
    applicable = boq.applicable_stages(chain, total_amount, package_id, currency)
    done = boq.completed_stages(chain, approvals, total_amount, package_id, currency)
    return chain, approvals, applicable, done


def review_commercial(
    db: Session,
    project: Project,
    doc_id: str,
    *,
    decision: str,
    note: str = "",
    actor: dict | str | None = None,
    actor_id: str = "",
    role: str = "",
    is_super: bool = False,
) -> dict[str, Any]:
    """Approve/reject a submitted commercial document.

    The chain semantics (all/any, amount/package/currency conditions) come from
    :mod:`bidcraft.boq`; only the status carrier differs (CommercialDoc).
    """
    if decision not in REVIEW_DECISIONS:
        raise ValueError("decision 只能是 approve 或 reject")
    doc = get_commercial(db, project, doc_id)
    if doc is None:
        raise LookupError("商务标不存在")
    chain, approvals, applicable, done = _review_context(doc, project)
    if not chain:
        if role != "owner" and not is_super:
            raise CommercialForbiddenError("仅项目负责人可审批商务标")
    else:
        if done < len(applicable):
            stage, current = applicable[done]
        else:
            stage, current = len(chain), {"approvers": [], "mode": "all"}
        approver_ids = {str(person.get("id") or "") for person in current["approvers"]}
        if not (is_super or role == "owner") and actor_id not in approver_ids:
            raise CommercialForbiddenError(
                f"当前需第 {done + 1} 级审批人审批，您不是该级审批人"
            )
        if current["mode"] == "all" and any(
            record.get("decision") == "approve"
            and record.get("stage") == stage
            and str(record.get("user_id") or "") == actor_id
            for record in approvals
        ):
            raise CommercialForbiddenError(f"您已完成本级审批（第 {done + 1} 级）")
    if (doc.status or "draft") != "submitted":
        raise CommercialConflictError("仅待审商务标可审批")
    now = utcnow()
    validation = dict(doc.validation or {})
    if chain:
        approvals.append(
            {
                "stage": stage,
                "user_id": actor_id,
                "name": _actor_name(actor if isinstance(actor, dict) else None),
                "decision": decision,
                "note": _clean_note(note),
                "at": clock.iso_utc(now),
            }
        )
        validation["approvals"] = approvals
        final_approve = decision == "approve" and boq.completed_stages(
            chain,
            approvals,
            _number((validation.get("quote") or {}).get("total_amount")),
            doc.package_id,
            _text((validation.get("quote") or {}).get("currency")),
        ) >= len(applicable)
    else:
        final_approve = decision == "approve"
    if decision == "approve" and not final_approve:
        doc.validation = validation
        db.flush()
        record_audit(
            db,
            project.id,
            "commercial.approve",
            {"doc_id": doc.id, "stage": stage, "final": False, "note": _clean_note(note)},
            actor=_audit_actor(actor),
        )
        return _doc_payload(doc)
    validation["review"] = {
        "decision": decision,
        "note": _clean_note(note),
        "by": _actor_name(actor if isinstance(actor, dict) else None),
        "at": clock.iso_utc(now),
    }
    doc.validation = validation
    doc.status = "approved" if decision == "approve" else "rejected"
    db.flush()
    record_audit(
        db,
        project.id,
        "commercial.approve" if decision == "approve" else "commercial.reject",
        {
            "doc_id": doc.id,
            "status": doc.status,
            "note": _clean_note(note),
            "stage": stage if chain else 0,
        },
        actor=_audit_actor(actor),
    )
    return _doc_payload(doc)


def unapprove_commercial(
    db: Session,
    project: Project,
    doc_id: str,
    *,
    actor: dict | str | None = None,
) -> dict[str, Any]:
    """Withdraw final approval: approved → draft."""
    doc = get_commercial(db, project, doc_id)
    if doc is None:
        raise LookupError("商务标不存在")
    if (doc.status or "draft") != "approved":
        raise CommercialConflictError("仅已审批商务标可撤销")
    doc.status = "draft"
    validation = dict(doc.validation or {})
    validation["review"] = {}
    doc.validation = validation
    db.flush()
    record_audit(
        db,
        project.id,
        "commercial.unapprove",
        {"doc_id": doc.id, "name": doc.name},
        actor=_audit_actor(actor),
    )
    return _doc_payload(doc)


def delete_commercial(
    db: Session,
    project: Project,
    doc_id: str,
    *,
    actor: dict | str | None = None,
) -> None:
    doc = get_commercial(db, project, doc_id)
    if doc is None:
        raise LookupError("商务标不存在")
    if (doc.status or "draft") == "approved":
        raise CommercialConflictError("已审批的商务标不可删除，请先撤销审批")
    db.delete(doc)
    db.flush()
    record_audit(
        db,
        project.id,
        "commercial.delete",
        {"doc_id": doc_id},
        actor=_audit_actor(actor),
    )


def ensure_commercial_export_allowed(
    db: Session,
    project: Project,
    doc: CommercialDoc,
    *,
    fmt: str = "",
    force: bool = False,
    force_reason: str = "",
    actor: dict | str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Commercial-export preflight: block stale/invalid quotes before rendering.

    The reusable gate revalidates the *stored* doc against the current approved
    quote, so a superseded or unapproved quote can never be exported silently.
    Without ``force`` it raises :class:`CommercialConflictError` (HTTP 409).
    ``force`` requires a reason (≥4 chars) and writes a dedicated audit; the
    caller must perform the owner authorization check itself (the HTTP route
    owns that decision).

    Returns the checked export payload so a route can render from it directly.
    """

    checked = payload if payload is not None else export_payload(db, project, doc)
    validation = dict(checked.get("validation") or {})
    if validation.get("ok"):
        return checked
    errors = [str(item) for item in (validation.get("errors") or []) if _text(item)]
    detail = "；".join(errors[:6]) or "商务标一致性校验未通过"
    if not force:
        raise CommercialConflictError(
            f"商务标一致性校验未通过，不能导出：{detail}。"
            "请重新汇编后再导出；确需导出请由项目负责人填写原因后强制导出"
        )
    reason = _text(force_reason).strip()
    if len(reason) < FORCE_REASON_MIN_CHARS:
        raise ValueError(f"强制导出商务标必须填写不少于 {FORCE_REASON_MIN_CHARS} 个字的原因")
    record_audit(
        db,
        project.id,
        "commercial.export_force",
        {
            "doc_id": doc.id,
            "format": fmt,
            "reason": reason,
            "errors": errors[:10],
            "quote_version_id": doc.quote_version_id,
        },
        actor=_audit_actor(actor),
    )
    return checked


def export_payload(
    db: Session, project: Project, doc: CommercialDoc
) -> dict[str, Any]:
    """Payload for exporters: stored sections + current-quote consistency check."""
    payload = _stored_payload(doc)
    payload["project"] = {
        "id": project.id,
        "name": project.name,
        "number": "",
        "buyer": project.buyer or "",
    }
    payload["package"] = {"id": doc.package_id} if doc.package_id else None
    payload["doc"] = _doc_payload(doc)
    current = _latest_approved_version(db, project.id, doc.package_id)
    current_quote = None
    if current is not None:
        current_quote = {
            "version_id": current.id,
            "name": current.name,
            "total_amount": (current.summary or {}).get("total_amount"),
        }
    validation = validate_commercial_consistency(
        payload, current_quote=current_quote
    )
    if doc.quote_version_id:
        cited = boq.get_version(db, project.id, doc.quote_version_id)
        if cited is None:
            validation["errors"].append("商务标引用的报价版本已删除，请重新汇编")
            validation["ok"] = False
        elif (cited.status or "draft") != "approved":
            validation["errors"].append(
                f"商务标引用的报价版本「{cited.name}」已不是已审批状态，请重新汇编"
            )
            validation["ok"] = False
    validation["warnings"].extend(_asset_warnings(payload.get("sections") or {}))
    payload["validation"] = validation
    payload["completeness"] = validate_commercial_completeness(payload)
    return payload


def pending_for_user(db: Session, user: dict | None) -> list[dict[str, Any]]:
    """Cross-project inbox of submitted commercial docs awaiting this user.

    Mirrors :func:`bidcraft.boq.pending_for_user` (visible projects, empty chain
    → owner, staged chain → current approvers) but reads the stored commercial
    validation quote for amount/package/currency. The ``version_id`` key is kept
    so the shared /approvals/pending payload stays shape-compatible; ``doc_id``
    and ``doc_name`` additionally identify the commercial doc for callers that
    want to link directly to the commercial tab.
    """

    user = user or {}
    role = user.get("role") or ""
    if role == auth.ROLE_VIEWER:
        return []
    is_super = role == auth.ROLE_SUPER_ADMIN
    user_id = str(user.get("id") or "")
    if is_super:
        projects = list(db.scalars(select(Project)))
    else:
        member_ids = [
            row.project_id
            for row in db.scalars(select(ProjectMember).where(ProjectMember.user_id == user_id))
        ]
        visible: dict[str, Project] = {
            item.id: item
            for item in db.scalars(select(Project).where(Project.owner_id == user_id))
        }
        if member_ids:
            for item in db.scalars(select(Project).where(Project.id.in_(member_ids))):
                visible.setdefault(item.id, item)
        projects = list(visible.values())

    items: list[dict[str, Any]] = []
    for project in projects:
        chain = boq.normalize_chain((project.meta or {}).get("approval_chain"))
        docs = db.scalars(
            select(CommercialDoc).where(
                CommercialDoc.project_id == project.id,
                CommercialDoc.status == "submitted",
            )
        )
        for doc in docs:
            validation = dict(doc.validation or {})
            approvals = list(validation.get("approvals") or [])
            quote = dict(validation.get("quote") or {})
            total_amount = _number(quote.get("total_amount"))
            package_id = doc.package_id or _text(quote.get("package_id")) or None
            currency = _text(quote.get("currency")) or boq.get_price_policy(project)["currency"]
            if not chain:
                if not is_super and project.owner_id != user_id:
                    continue
                pending_stage, stage_count, mode = 1, 1, ""
            else:
                applicable = boq.applicable_stages(
                    chain, total_amount, package_id, currency
                )
                done = boq.completed_stages(
                    chain, approvals, total_amount, package_id, currency
                )
                if done >= len(applicable):
                    continue
                stage, current = applicable[done]
                pending_stage, stage_count, mode = done + 1, len(applicable), current["mode"]
                if not is_super:
                    approver_ids = {
                        str(person.get("id") or "") for person in current["approvers"]
                    }
                    if user_id not in approver_ids:
                        continue
                    if mode == "all" and any(
                        record.get("decision") == "approve"
                        and record.get("stage") == stage
                        and str(record.get("user_id") or "") == user_id
                        for record in approvals
                    ):
                        continue
            items.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    # Shared inbox shape: the doc id occupies version_id so the
                    # flat /approvals/pending list stays consumable as-is.
                    "version_id": doc.id,
                    "version_name": f"商务标 · {_text(doc.name)}",
                    "doc_id": doc.id,
                    "doc_name": doc.name or "",
                    "package_id": package_id,
                    "stage": pending_stage,
                    "stage_count": stage_count,
                    "mode": mode,
                    "submitted_by": doc.submitted_by or "",
                    "submitted_at": _iso(doc.submitted_at),
                    "total_amount": total_amount,
                }
            )
    items.sort(key=lambda item: item["submitted_at"] or "", reverse=True)
    return items[:PENDING_APPROVALS_LIMIT]
