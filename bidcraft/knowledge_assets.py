"""v0.32 Phase 2：结构化知识资产引擎（backend DB/service）。

资产不是第二套知识库：结构化字段挂在既有 ``KbDocument.structured`` 上，
溯源只用库里真实存在的 ``KbDocument.id`` / ``KbChunk.id``，抽取完全由正则
决定（无模型参与、同输入同输出）。资产必须走审核状态机，只有 legacy
（``review_status IS NULL``）和 published 的块参与检索；重复候选按同库内的
文件哈希与规范化题录聚合，由人工裁决，裁决可重复执行。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from . import clock, material_policy
from .models import (
    KbAssetDuplicateGroup,
    KbAssetDuplicateMember,
    KbChunk,
    KbDocument,
    utcnow,
)

DRAFT = "draft"
REVIEW = "review"
APPROVED = "approved"
PUBLISHED = "published"
REJECTED = "rejected"
ARCHIVED = "archived"

STATES = frozenset({DRAFT, REVIEW, APPROVED, PUBLISHED, REJECTED, ARCHIVED})

#: 审核状态机： rejects 可回到 draft，archive 只能从 published 进入。
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({REVIEW, REJECTED}),
    REVIEW: frozenset({APPROVED, REJECTED, DRAFT}),
    APPROVED: frozenset({PUBLISHED, REJECTED}),
    PUBLISHED: frozenset({ARCHIVED}),
    REJECTED: frozenset({DRAFT}),
    ARCHIVED: frozenset({DRAFT}),
}

#: 审核动作的别名，便于调用方用动词或状态名传入 decision。
_DECISION_ALIASES = {
    "submit": REVIEW,
    "submit_for_review": REVIEW,
    "for_review": REVIEW,
    "approve": APPROVED,
    "publish": PUBLISHED,
    "reject": REJECTED,
    "archive": ARCHIVED,
    "reopen": DRAFT,
    "withdraw": DRAFT,
}
#: 裁决动作的别名。
_RESOLUTION_ALIASES = {"resolve": "resolved", "dismiss": "dismissed"}
_RESOLUTIONS = frozenset({"pending", "resolved", "dismissed"})

#: 结构化抽取的字段顺序即 JSON 顺序（稳定输出，便于比对与哈希）。
FIELD_KEYS = (
    "title",
    "cert_no",
    "contract_no",
    "amount",
    "valid_to",
    "owner",
    "source_url",
    "note",
)
_PATTERNS = {
    "title": re.compile(r"(?:标题|资料名称|文件标题|项目名称)\s*[:：]\s*([^\n\r，,。；;]+)"),
    "cert_no": re.compile(
        r"(?:证书编号|证书号|证号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{1,39})"
    ),
    "contract_no": re.compile(
        r"(?:合同编号|合同号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{1,39})"
    ),
    "amount": re.compile(
        r"(?:合同金额|预算金额|项目金额|金额|造价)\s*[:：]?\s*(?:人民币\s*)?"
        r"([0-9]+(?:\.[0-9]+)?\s*(?:亿元|万元|元))"
    ),
    "valid_to": re.compile(
        r"(?:有效期至|有效期到|有效期|到期日|截止日期)\s*[:：]?\s*"
        r"((?:19|20)\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?)"
    ),
    "owner": re.compile(
        r"(?:业主|招标人|采购人|建设单位|委托方)\s*[:：]?\s*([^\n\r，,。；;]+)"
    ),
    "source_url": re.compile(r"(https?://[^\s<>\u3000，,。；;）)]+)"),
    "note": re.compile(r"(?:备注|说明)\s*[:：]\s*([^\n\r]+)"),
}
_DATE_PARTS = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")
_NORMALIZE = re.compile(r"[\s\-_()（）【】\[\]，,。.、/\\:：·]+")
_KIND_ALIASES = {
    "license": "qualification",
    "cert": "qualification",
    "qualification": "qualification",
    "contract": "performance",
    "performance": "performance",
    "personnel": "personnel",
    "standard": "standard",
}

#: 自动生组的来源标识；没有人工 actor 时才使用。
SYSTEM_ACTOR = "system"


class _AttrDict(dict):
    """同时支持 ``obj["key"]`` 与 ``obj.key`` 的只读视图。"""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - 属性探测的常规路径
            raise AttributeError(name) from exc


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n#*")


def _iso_date(value: str) -> str:
    """把中文/斜杠日期规范化为 YYYY-MM-DD；缺日不补造日期。"""

    match = _DATE_PARTS.search(value or "")
    if match is None:
        return ""
    year, month, day = (int(item) for item in match.groups())
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return ""


def _normalize(value: Any) -> str:
    return _NORMALIZE.sub("", _text(value)).casefold()


def _policy_kind(value: Any, rules: dict[str, Any]) -> str:
    kind = _text(value)
    if kind in rules:
        return kind
    return _KIND_ALIASES.get(kind, "other")


def _chunk_query(row: KbDocument):
    return (
        select(KbChunk)
        .where(KbChunk.document_id == row.id, KbChunk.kb_id == row.kb_id)
        .order_by(KbChunk.ordinal, KbChunk.chunk_index, KbChunk.id)
    )


def _extract_fields(chunks: list[KbChunk]) -> dict[str, str]:
    """从真实块文本中按固定优先级抽取字段；没有匹配就留空字符串。"""

    fields = {key: "" for key in FIELD_KEYS}
    # 保留原始换行：备注/题名的回退按行取，不能先压成一行。
    texts = [str(chunk.text or "") for chunk in chunks]
    for key in FIELD_KEYS:
        pattern = _PATTERNS[key]
        for text in texts:
            match = pattern.search(text)
            if match is None:
                continue
            value = _text(match.group(1))
            if key == "valid_to":
                value = _iso_date(value)
            if value:
                fields[key] = value[:500]
                break

    if not fields["title"]:
        # 题名回退也必须来自真实块：优先页内标题，其次首个无标签文本行。
        for chunk in chunks:
            heading = _text(chunk.heading)
            if heading:
                fields["title"] = heading[:200]
                break
        if not fields["title"]:
            for text in texts:
                for line in text.splitlines():
                    line = _text(line)
                    if line and ":" not in line and "：" not in line:
                        fields["title"] = line[:200]
                        break
                if fields["title"]:
                    break
    return fields


def asset_payload(row: KbDocument, db=None) -> dict[str, Any]:
    """序列化知识资产：结构化字段 + 真实块溯源 + 素材可信度裁决。"""

    fields = row.structured if isinstance(row.structured, dict) else {}
    fields = {key: fields.get(key, "") for key in FIELD_KEYS}

    chunks: list[dict[str, Any]] = []
    if db is not None:
        try:
            for chunk in db.scalars(_chunk_query(row)):
                chunks.append(
                    {
                        "id": chunk.id,
                        "page": chunk.page_no,
                        "heading": chunk.heading or "",
                        "chunk_index": int(chunk.chunk_index or 0),
                        "source": chunk.source or "",
                    }
                )
        except Exception:  # noqa: BLE001 - 兼容轻量假 DB（没有 KbChunk 表）
            chunks = []

    meta: dict[str, Any] = {}
    if db is not None:
        try:
            _, found = material_policy.find_meta(
                db, row.source, kb_id=row.kb_id, document_id=row.id
            )
            meta = dict(found or {})
        except Exception:  # noqa: BLE001 - 兼容轻量假 DB
            meta = {}
    meta.update({key: value for key, value in fields.items() if value not in (None, "")})
    rules = material_policy.load_policy()
    meta["kind"] = _policy_kind(meta.get("kind") or row.kind, rules)
    verdict = material_policy.assess(meta, rules)

    return {
        "document_id": row.id,
        "kb_id": row.kb_id,
        "source": row.source or "",
        "sha256": row.sha256 or "",
        "review_status": row.review_status,
        "status": row.status or "ready",
        "kind": row.kind or "other",
        "source_file_id": row.source_file_id or "",
        "fields": fields,
        "chunks": chunks,
        "assessment": {
            "ok": verdict.ok,
            "reason": verdict.reason,
            "kind": verdict.kind,
            "missing": list(verdict.missing),
            "expired": verdict.expired,
        },
        # 平铺一份，便于旧调用方直接读取裁决结论。
        "ok": verdict.ok,
        "reason": verdict.reason,
        "reviewed_by": row.reviewed_by or "",
        "reviewed_at": clock.iso_beijing(row.reviewed_at),
        "published_at": clock.iso_beijing(row.published_at),
        "rejected_reason": row.rejected_reason or "",
    }


def propose_document_assets(db, kb_id: str, document_id: str) -> dict[str, Any]:
    """对已有文档做确定性结构化抽取，写入 structured 并置为 draft。"""

    row = db.get(KbDocument, str(document_id or ""))
    if row is None:
        raise LookupError("文档不存在")
    scope = str(kb_id or "")
    if scope and row.kb_id != scope:
        raise ValueError("文档不属于该知识库")

    chunks = list(db.scalars(_chunk_query(row)))
    fields = _extract_fields(chunks)
    row.structured = fields
    row.review_status = DRAFT
    row.reviewed_by = None
    row.reviewed_at = None
    row.published_at = None
    row.rejected_reason = None
    db.flush()
    return asset_payload(row, db)


def _decision_state(decision: Any) -> str:
    value = str(decision or "").strip().lower()
    value = _DECISION_ALIASES.get(value, value)
    if value not in STATES:
        raise ValueError(f"未知审核动作：{decision}")
    return value


def review_asset(
    db,
    kb,
    document_id: str,
    decision: str,
    note: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """按状态机推进资产审核；非法跃迁直接 ValueError，不写库。"""

    kb_id = getattr(kb, "id", kb)
    row = db.get(KbDocument, str(document_id or ""))
    if row is None:
        raise LookupError("文档不存在")
    if kb_id and row.kb_id != str(kb_id):
        raise ValueError("文档不属于该知识库")

    target = _decision_state(decision)
    current = row.review_status or DRAFT  # legacy NULL 视作 draft，不提前落库
    if target not in VALID_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"非法资产状态转换：{current} -> {target}")

    now = utcnow()
    row.review_status = target
    row.reviewed_by = str(actor or "")[:64] or None
    row.reviewed_at = now
    row.rejected_reason = str(note or "") if target == REJECTED else ""
    if target == PUBLISHED:
        row.published_at = now
    db.flush()
    return asset_payload(row, db)


def _doc_order(row: KbDocument):
    return (row.created_at or datetime.min, row.source or "", row.id)


def _group_payload(db, group: KbAssetDuplicateGroup) -> _AttrDict:
    rows = list(
        db.scalars(
            select(KbAssetDuplicateMember)
            .where(KbAssetDuplicateMember.group_id == group.id)
            .order_by(KbAssetDuplicateMember.role, KbAssetDuplicateMember.id)
        )
    )
    members = [
        _AttrDict(
            {
                "id": member.id,
                "group_id": member.group_id,
                "document_id": member.document_id,
                "score": float(member.score or 0.0),
                "role": member.role or "candidate",
            }
        )
        for member in rows
    ]
    return _AttrDict(
        {
            "id": group.id,
            "kb_id": group.kb_id,
            "fingerprint": group.fingerprint,
            "similarity": float(group.similarity or 0.0),
            "status": group.status,
            "reason": group.reason or "",
            "created_by": group.created_by or "",
            "created_at": clock.iso_beijing(group.created_at),
            "reviewed_by": group.reviewed_by or "",
            "reviewed_at": clock.iso_beijing(group.reviewed_at),
            "members": members,
        }
    )


def _upsert_group(
    db,
    kb_id: str,
    fingerprint: str,
    similarity: float,
    reason: str,
    documents: list[KbDocument],
) -> _AttrDict:
    group = db.scalar(
        select(KbAssetDuplicateGroup).where(
            KbAssetDuplicateGroup.kb_id == kb_id,
            KbAssetDuplicateGroup.fingerprint == fingerprint,
        )
    )
    if group is None:
        group = KbAssetDuplicateGroup(
            kb_id=kb_id,
            fingerprint=fingerprint,
            similarity=similarity,
            status="pending",
            reason=reason,
            created_by=SYSTEM_ACTOR,
            created_at=utcnow(),
        )
        db.add(group)
        db.flush()
    else:
        # 已裁决的组保留状态/裁决人，只更新判定依据与相似度。
        group.similarity = similarity
        group.reason = reason

    existing = {
        member.document_id: member
        for member in db.scalars(
            select(KbAssetDuplicateMember).where(
                KbAssetDuplicateMember.group_id == group.id
            )
        )
    }
    wanted = {row.id for row in documents}
    for index, doc in enumerate(documents):
        member = existing.get(doc.id)
        if member is None:
            db.add(
                KbAssetDuplicateMember(
                    group_id=group.id,
                    document_id=doc.id,  # 只引用真实文档 ID
                    score=similarity,
                    role="primary" if index == 0 else "candidate",
                )
            )
        else:
            member.score = similarity
            member.role = "primary" if index == 0 else "candidate"
    if group.status == "pending":
        for doc_id, member in existing.items():
            if doc_id not in wanted:
                db.delete(member)
    db.flush()
    return _group_payload(db, group)


def find_duplicate_candidates(db, kb_id: str) -> list[_AttrDict]:
    """同库内按精确 SHA256 + 规范化题录聚合成候选组，重复执行结果稳定。"""

    scope = str(kb_id or "")
    documents = list(
        db.scalars(
            select(KbDocument)
            .where(KbDocument.kb_id == scope)
            .order_by(KbDocument.created_at, KbDocument.source, KbDocument.id)
        )
    )
    documents.sort(key=_doc_order)

    by_sha: dict[str, list[KbDocument]] = {}
    for row in documents:
        digest = _text(row.sha256)
        if digest:
            by_sha.setdefault(digest, []).append(row)

    candidates: list[tuple[str, float, str, list[KbDocument]]] = []
    claimed: set[str] = set()
    for digest in sorted(by_sha):
        members = by_sha[digest]
        if len(members) < 2:
            continue
        claimed.update(row.id for row in members)
        fingerprint = hashlib.sha256(f"sha|{digest}".encode()).hexdigest()
        candidates.append((fingerprint, 1.0, "文件 SHA256 完全一致", members))

    # 精确哈希已覆盖的文档不再进题录组，避免同一对文档出现两个候选组。
    by_meta: dict[tuple[str, str], list[KbDocument]] = {}
    for row in documents:
        if row.id in claimed:
            continue
        fields = row.structured if isinstance(row.structured, dict) else {}
        title = _normalize(fields.get("title"))
        cert = _normalize(fields.get("cert_no"))
        if not title or not cert:
            continue
        by_meta.setdefault((title, cert), []).append(row)
    for key in sorted(by_meta):
        members = by_meta[key]
        if len(members) < 2:
            continue
        fingerprint = hashlib.sha256(
            f"meta|{key[0]}|{key[1]}".encode()
        ).hexdigest()
        candidates.append((fingerprint, 0.9, "规范化标题与证书编号一致", members))

    return [
        _upsert_group(db, scope, fingerprint, similarity, reason, members)
        for fingerprint, similarity, reason, members in sorted(
            candidates, key=lambda item: item[0]
        )
    ]


def _resolution(decision: Any) -> str:
    value = str(decision or "").strip().lower()
    value = _RESOLUTION_ALIASES.get(value, value)
    if value not in _RESOLUTIONS:
        raise ValueError(f"未知查重裁决：{decision}")
    return value


def resolve_duplicate_group(
    db,
    kb_id: str,
    group_id: str,
    decision: str,
    note: str = "",
    actor: str = "",
) -> _AttrDict:
    """裁决查重候选组；重复裁决保持幂等（不覆盖已有裁决时间）。"""

    scope = str(kb_id or "")
    group = db.get(KbAssetDuplicateGroup, str(group_id or ""))
    if group is None or (scope and group.kb_id != scope):
        raise LookupError("候选组不存在")

    target = _resolution(decision)
    if group.status != target:
        group.status = target
        group.reviewed_by = str(actor or "")[:64] or None
        group.reviewed_at = utcnow()
        if note:
            group.reason = str(note)
        db.flush()
    return _group_payload(db, group)


__all__ = [
    "APPROVED",
    "ARCHIVED",
    "DRAFT",
    "FIELD_KEYS",
    "PUBLISHED",
    "REJECTED",
    "REVIEW",
    "STATES",
    "VALID_TRANSITIONS",
    "asset_payload",
    "find_duplicate_candidates",
    "propose_document_assets",
    "resolve_duplicate_group",
    "review_asset",
]
