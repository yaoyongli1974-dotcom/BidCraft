"""项目要求 ↔ 企业资产匹配与企业材料包。

判定原则（宁可转人工，也不冒判）：

1. **过期即无效**：``valid_to`` 早于判定基准日（默认北京时间今天）的资质/
   文档一律 ``valid=False``，即使名称完全一致也不能自动判满足；
2. **名称相似不等于满足**：只有「要求文本包含资产名称/证书号」或反向包含时
   才记 ``match="exact"``；词面相似只作为候选提示（``match="similar"``），
   ``matched`` 始终为 ``False``；
3. **跨企业隔离**：只读取当前用户有成员关系的企业，以及当前用户自己的私有
   资产；不会因为传入 enterprise_id 就绕过成员校验。
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from sqlalchemy import or_, select

from .. import clock
from ..knowledge import tokens_for
from ..models import KbChunk, KbDocument, Personnel, Qualification
from ..qualifications import KIND_LABELS, status_of
from . import assets, evidence

_ASSET_KINDS = ("performance", "equipment", "device", "机械", "设备", "仪器")
_PUNCT = re.compile(r"[\s\-—_/\\,，。；;：:、（）()【】\[\]{}<>《》'\"“”‘’]+")
_CATEGORY_RULES = (
    ("personnel", re.compile(r"人员|项目经理|建造师|监理工程师|技术负责人|社保|职称")),
    ("performance", re.compile(r"业绩|案例|合同|中标|类似项目|同类项目|已完成")),
    ("equipment", re.compile(r"设备|机械|仪器|机具|检测仪|车辆")),
    ("qualification", re.compile(r"资质|资格|许可证|证书|营业执照|安全生产|认证|等级")),
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.replace(microsecond=0).isoformat()


def _today(today: date | datetime | None = None) -> date:
    if today is None:
        return clock.today()
    if isinstance(today, datetime):
        return today.date()
    return today


def _norm(text: str) -> str:
    return _PUNCT.sub("", str(text or "").lower())


def _text_of(requirement: str) -> str:
    value = str(requirement or "").strip()
    if not value:
        raise ValueError("匹配要求不能为空")
    return value


def _scope_ids(db, user, enterprise_ids: list[str] | None) -> list[str]:
    allowed = set(assets.accessible_enterprise_ids(db, user))
    if enterprise_ids is None:
        return sorted(allowed)
    if isinstance(enterprise_ids, str):
        enterprise_ids = [enterprise_ids]
    return [item for item in enterprise_ids if item in allowed]


def _requested_scope(
    enterprise_ids: list[str] | str | None, enterprise_id: str | None
) -> list[str] | None:
    if enterprise_id:
        return [enterprise_id]
    if isinstance(enterprise_ids, str):
        return [enterprise_ids]
    return enterprise_ids


def normalize_kind(kind: str | None) -> str:
    """Map a human label (``资质`` / ``业绩``…) onto the ledger kind code."""

    value = str(kind or "").strip()
    if not value:
        return ""
    for code, label in KIND_LABELS.items():
        if value == code or value == label:
            return code
    aliases = {
        "资质": "license",
        "资格": "license",
        "许可证": "license",
        "营业执照": "license",
        "安全生产": "safety",
        "人员": "person",
        "人员证书": "person",
        "业绩": "performance",
        "业绩证明": "performance",
        "信用": "credit",
    }
    return aliases.get(value, value)


def _similarity(requirement: str, haystack: str) -> tuple[float, str]:
    """Return ``(score, kind)`` where kind is exact/similar/none."""

    req_norm = _norm(requirement)
    hay_norm = _norm(haystack)
    if not req_norm or not hay_norm:
        return 0.0, "none"
    if hay_norm in req_norm or req_norm in hay_norm:
        return 1.0, "exact"
    wanted = set(tokens_for(requirement))
    have = set(tokens_for(haystack))
    if not wanted or not have:
        return 0.0, "none"
    overlap = len(wanted & have)
    if not overlap:
        return 0.0, "none"
    score = overlap / (len(wanted) ** 0.5 * max(1, len(have)) ** 0.5)
    # 相似只作候选：不允许达到 1.0，避免下游把候选当满足。
    return min(0.99, round(score, 3)), ("similar" if score >= 0.3 else "none")


def _identity_match(requirement: str, name: str, cert_no: str = "") -> tuple[float, str]:
    """Exact identity: requirement contains the asset name or certificate no."""

    req_norm = _norm(requirement)
    name_norm = _norm(name)
    cert_norm = _norm(cert_no)
    if name_norm and len(name_norm) >= 2 and name_norm in req_norm:
        return 1.0, "exact"
    if cert_norm and len(cert_norm) >= 4 and cert_norm in req_norm:
        return 1.0, "exact"
    return _similarity(requirement, f"{name} {cert_no}")


def _qualification_rows(
    db, user, enterprise_ids: list[str] | None, *, kind: str = ""
) -> list[Qualification]:
    uid = assets._user_id(user)
    eids = _scope_ids(db, user, enterprise_ids)
    conditions = [Qualification.owner_id == uid]
    if eids:
        conditions.append(Qualification.enterprise_id.in_(eids))
    stmt = select(Qualification).where(or_(*conditions))
    if kind:
        stmt = stmt.where(Qualification.kind == kind)
    return list(
        db.scalars(
            stmt.order_by(
                Qualification.valid_to.is_(None),
                Qualification.valid_to.asc(),
                Qualification.name.asc(),
            )
        )
    )


def _personnel_rows(
    db, user, enterprise_ids: list[str] | None
) -> list[Personnel]:
    uid = assets._user_id(user)
    eids = _scope_ids(db, user, enterprise_ids)
    conditions = [Personnel.owner_id == uid]
    if eids:
        conditions.append(Personnel.enterprise_id.in_(eids))
    return list(
        db.scalars(
            select(Personnel)
            .where(or_(*conditions))
            .order_by(Personnel.name.asc(), Personnel.created_at.asc())
        )
    )


def _document_rows(
    db, user, enterprise_ids: list[str] | None, *, kinds: tuple[str, ...] = ()
) -> list[tuple[KbDocument, str]]:
    eids = _scope_ids(db, user, enterprise_ids)
    rows: list[tuple[KbDocument, str]] = []
    for enterprise_id in eids:
        enterprise = db.get(assets.Enterprise, enterprise_id)
        if enterprise is None:  # pragma: no cover - defensive
            continue
        kb = assets.enterprise_kb(db, enterprise, create=False)
        if kb is None:
            continue
        statement = select(KbDocument).where(KbDocument.kb_id == kb.id)
        if kinds:
            statement = statement.where(KbDocument.kind.in_(kinds))
        for row in db.scalars(statement.order_by(KbDocument.created_at)):
            rows.append((row, enterprise_id))
    return rows


def _document_text(db, document: KbDocument, limit: int = 1200) -> str:
    chunks = list(
        db.scalars(
            select(KbChunk)
            .where(KbChunk.document_id == document.id)
            .order_by(KbChunk.ordinal)
        )
    )
    return " ".join((row.text or "") for row in chunks)[:limit]


def _document_valid(row: KbDocument, today: date) -> tuple[bool, str]:
    state = status_of(row.valid_to, today=today)
    if state == "expired":
        return False, "expired"
    if row.status and row.status != "ready":
        return False, row.status
    return True, state


def match_qualification(
    db,
    user,
    requirement: str,
    *,
    kind: str = "",
    enterprise_ids: list[str] | str | None = None,
    enterprise_id: str | None = None,
    today: date | datetime | None = None,
) -> dict:
    """Match one requirement against qualification/licence assets."""

    text = _text_of(requirement)
    current = _today(today)
    wanted_kind = normalize_kind(kind)
    rows = _qualification_rows(
        db,
        user,
        _requested_scope(enterprise_ids, enterprise_id),
        kind=wanted_kind if wanted_kind in KIND_LABELS else "",
    )
    results = []
    for row in rows:
        state = status_of(row.valid_to, today=current)
        valid = state != "expired"
        score, match = _identity_match(text, row.name or "", row.cert_no or "")
        matched = bool(valid and match == "exact")
        reason = ""
        if not valid:
            reason = "资质已过期，不能自动判定满足"
        elif match == "similar":
            reason = "名称相近但未确认同一证书，需人工复核"
        elif match == "none":
            reason = "未在要求文本中找到该资产名称或证书号"
        results.append(
            {
                **evidence.qualification_descriptor(row, status=state),
                "valid": valid,
                "status": state,
                "matched": matched,
                "match": match,
                "score": score,
                "reason": reason,
            }
        )
    results.sort(
        key=lambda item: (
            not item["matched"],
            not item["valid"],
            -float(item.get("score") or 0),
            str(item.get("name") or ""),
        )
    )
    unmet = [
        f"资质未满足：{item['name']}（{item['reason']}）"
        for item in results
        if not item["matched"]
    ]
    if not results:
        label = KIND_LABELS.get(wanted_kind, wanted_kind or "资质")
        unmet.append(f"企业资产中没有可用的{label}记录")
    return {
        "requirement": text,
        "kind": wanted_kind,
        "results": results,
        "matches": results,
        "matched": any(item["matched"] for item in results),
        "satisfied": any(item["matched"] for item in results),
        "evidence": evidence.aggregate(results),
        "unmet": unmet,
    }


def match_performance(
    db,
    user,
    requirement: str,
    *,
    enterprise_ids: list[str] | str | None = None,
    enterprise_id: str | None = None,
    today: date | datetime | None = None,
) -> dict:
    """Match past-performance assets: ledger rows plus enterprise documents."""

    text = _text_of(requirement)
    current = _today(today)
    scope = _requested_scope(enterprise_ids, enterprise_id)
    results = []
    for row in _qualification_rows(db, user, scope, kind="performance"):
        state = status_of(row.valid_to, today=current)
        valid = state != "expired"
        score, match = _identity_match(text, row.name or "", row.cert_no or "")
        matched = bool(valid and match == "exact")
        reason = ""
        if not valid:
            reason = "业绩证明已过期，不能自动判定满足"
        elif match == "similar":
            reason = "业绩名称相近但身份未确认，需人工复核"
        elif match == "none":
            reason = "要求文本未指向该业绩记录"
        results.append(
            {
                **evidence.qualification_descriptor(row, status=state),
                "valid": valid,
                "status": state,
                "matched": matched,
                "match": match,
                "score": score,
                "reason": reason,
            }
        )
    for row, owner_enterprise_id in _document_rows(
        db, user, scope, kinds=("performance",)
    ):
        valid, state = _document_valid(row, current)
        haystack = " ".join(
            [row.source or "", " ".join(str(tag) for tag in row.tags or [])]
            + [_document_text(db, row, 400)]
        )
        score, match = _similarity(text, haystack)
        matched = bool(valid and match == "exact")
        reason = "" if matched else "业绩文档未与要求形成明确同一性"
        if not valid:
            reason = "业绩文档已过期或未就绪"
        results.append(
            {
                **evidence.document_descriptor(row, enterprise_id=owner_enterprise_id),
                "valid": valid,
                "matched": matched,
                "match": match,
                "score": score,
                "reason": reason,
            }
        )
    results.sort(
        key=lambda item: (
            not item["matched"],
            not item["valid"],
            -float(item.get("score") or 0),
        )
    )
    unmet = [
        f"业绩未满足：{item.get('name') or item.get('source')}（{item['reason']}）"
        for item in results
        if not item["matched"]
    ]
    if not results:
        unmet.append("企业资产中没有可用的业绩记录")
    return {
        "requirement": text,
        "kind": "performance",
        "results": results,
        "matches": results,
        "matched": any(item["matched"] for item in results),
        "satisfied": any(item["matched"] for item in results),
        "evidence": evidence.aggregate(results),
        "unmet": unmet,
    }


def match_personnel(
    db,
    user,
    requirement: str,
    *,
    enterprise_ids: list[str] | str | None = None,
    enterprise_id: str | None = None,
    today: date | datetime | None = None,
) -> dict:
    """Match personnel; ``locked`` people are reported but never auto-satisfy."""

    text = _text_of(requirement)
    results = []
    for row in _personnel_rows(
        db, user, _requested_scope(enterprise_ids, enterprise_id)
    ):
        valid = not bool(row.locked)
        score, match = _identity_match(text, row.name or "", row.cert_no or "")
        matched = bool(valid and match == "exact")
        reason = ""
        if not valid:
            reason = "人员处于在建锁定状态，不能自动判定可用"
        elif match == "similar":
            reason = "人员姓名/专业相近但未确认，需人工复核"
        elif match == "none":
            reason = "要求文本未指向该人员"
        results.append(
            {
                **evidence.personnel_descriptor(row),
                "valid": valid,
                "matched": matched,
                "match": match,
                "score": score,
                "reason": reason,
            }
        )
    results.sort(
        key=lambda item: (
            not item["matched"],
            not item["valid"],
            -float(item.get("score") or 0),
            str(item.get("name") or ""),
        )
    )
    unmet = [
        f"人员未满足：{item['name']}（{item['reason']}）"
        for item in results
        if not item["matched"]
    ]
    if not results:
        unmet.append("企业资产中没有可用的人员记录")
    return {
        "requirement": text,
        "kind": "personnel",
        "results": results,
        "matches": results,
        "matched": any(item["matched"] for item in results),
        "satisfied": any(item["matched"] for item in results),
        "evidence": evidence.aggregate(results),
        "unmet": unmet,
    }


def match_equipment(
    db,
    user,
    requirement: str,
    *,
    enterprise_ids: list[str] | str | None = None,
    enterprise_id: str | None = None,
    today: date | datetime | None = None,
) -> dict:
    """Match equipment/device documents (设备台账以文档实体承载)."""

    text = _text_of(requirement)
    current = _today(today)
    scope = _requested_scope(enterprise_ids, enterprise_id)
    results = []
    for row, owner_enterprise_id in _document_rows(
        db, user, scope, kinds=_ASSET_KINDS
    ):
        valid, _state = _document_valid(row, current)
        haystack = " ".join(
            [row.source or "", " ".join(str(tag) for tag in row.tags or [])]
            + [_document_text(db, row, 400)]
        )
        score, match = _similarity(text, haystack)
        matched = bool(valid and match == "exact")
        reason = "" if matched else "设备文档未与要求形成明确同一性"
        if not valid:
            reason = "设备文档已过期或未就绪"
        results.append(
            {
                **evidence.document_descriptor(row, enterprise_id=owner_enterprise_id),
                "valid": valid,
                "matched": matched,
                "match": match,
                "score": score,
                "reason": reason,
            }
        )
    results.sort(
        key=lambda item: (
            not item["matched"],
            not item["valid"],
            -float(item.get("score") or 0),
        )
    )
    unmet = [
        f"设备未满足：{item.get('name')}（{item['reason']}）"
        for item in results
        if not item["matched"]
    ]
    if not results:
        unmet.append("企业资产中没有可用的设备记录")
    return {
        "requirement": text,
        "kind": "equipment",
        "results": results,
        "matches": results,
        "matched": any(item["matched"] for item in results),
        "satisfied": any(item["matched"] for item in results),
        "evidence": evidence.aggregate(results),
        "unmet": unmet,
    }


def classify_requirement(text: str) -> str:
    for category, pattern in _CATEGORY_RULES:
        if pattern.search(text or ""):
            return category
    return ""


def match_any(
    db,
    user,
    requirement: str,
    *,
    kind: str = "",
    enterprise_ids: list[str] | str | None = None,
    enterprise_id: str | None = None,
    today: date | datetime | None = None,
) -> dict:
    """Dispatch by explicit ``kind`` or by requirement wording."""

    category = normalize_kind(kind) or classify_requirement(requirement)
    scope = _requested_scope(enterprise_ids, enterprise_id)
    if category in ("performance", "业绩", "业绩证明"):
        return match_performance(
            db, user, requirement, enterprise_ids=scope, today=today
        )
    if category in ("person", "personnel", "人员", "人员证书"):
        return match_personnel(
            db, user, requirement, enterprise_ids=scope, today=today
        )
    if category in ("equipment", "device", "设备", "机械"):
        return match_equipment(
            db, user, requirement, enterprise_ids=scope, today=today
        )
    return match_qualification(
        db,
        user,
        requirement,
        kind=category if category in KIND_LABELS else "",
        enterprise_ids=scope,
        today=today,
    )


def build_project_asset_package(
    db,
    user,
    project,
    *,
    today: date | datetime | None = None,
) -> dict:
    """Assemble the enterprise material package for one project's requirements."""

    from ..models import Project, Requirement

    current = _today(today)
    if isinstance(project, str):
        loaded = db.get(Project, project)
        if loaded is None:
            raise LookupError("项目不存在")
        project = loaded
    enterprise_ids = assets.accessible_enterprise_ids(db, user)
    enterprises = [
        {
            "id": row.id,
            "name": row.name,
            "role": assets.role_in(db, user, row.id),
        }
        for row in assets.list_enterprises(db, user)
    ]
    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.project_id == project.id)
            .order_by(Requirement.id)
        )
    )
    items = []
    for requirement in requirements:
        category = classify_requirement(requirement.text or "")
        if not category:
            continue
        item = match_any(
            db,
            user,
            requirement.text or "",
            kind=category,
            enterprise_ids=enterprise_ids,
            today=current,
        )
        item["category"] = category
        item["requirement_id"] = requirement.id
        item["mandatory"] = bool(requirement.mandatory)
        items.append(item)
    documents = [
        {
            **evidence.document_descriptor(row, enterprise_id=owner_enterprise_id),
            "valid": _document_valid(row, current)[0],
        }
        for row, owner_enterprise_id in _document_rows(db, user, enterprise_ids)
    ]
    satisfied = sum(1 for item in items if item["matched"])
    unmet = [problem for item in items for problem in item["unmet"]]
    return {
        "project_id": project.id,
        "project_name": project.name,
        "generated_at": _iso(clock.utc_now()),
        "today": current.isoformat(),
        "enterprises": enterprises,
        "items": items,
        "documents": documents,
        "evidence": evidence.aggregate(items + documents),
        "summary": {
            "requirements": len(items),
            "satisfied": satisfied,
            "unmet": len(items) - satisfied,
            "documents": len(documents),
            "enterprises": len(enterprises),
        },
        "unmet": unmet,
    }


__all__ = [
    "build_project_asset_package",
    "classify_requirement",
    "match_any",
    "match_equipment",
    "match_performance",
    "match_personnel",
    "match_qualification",
    "normalize_kind",
]
