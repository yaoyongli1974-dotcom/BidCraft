"""章节规划引擎（Phase 1，backend）。

一章一条可版本化的规划记录：生成 → 校验（剔除编造引用）→ 人工确认 → 用于正文。
所有引用 ID 都从本项目真实行中取得；模型给出的 ID 也必须与真实行匹配，否则
直接丢弃并记入 ``validation_errors``，绝不把虚构的评分/事实/知识/章节 ID
传给正文生成。持久化全部走短事务，模型输入由调用方在事务外构造后传入。
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import (
    ChapterPlan,
    Fact,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    OutlineNode,
    Project,
    Requirement,
    ScoringItem,
)

#: 提示词版本：规划格式变化时必须 bump，便于追溯 prompt ↔ 规划版本。
#: v3：规划提示词新增「历史人工调整 / 未审核建议经验」两段显式 advisory。
PROMPT_VERSION = "chapter_plan_v3"

# 状态机（大写值便于日志/审计阅读）。
NOT_CREATED = "NOT_CREATED"
GENERATING = "GENERATING"
DRAFT = "DRAFT"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
CONFIRMED = "CONFIRMED"
USED_FOR_GENERATION = "USED_FOR_GENERATION"
REGENERATING = "REGENERATING"
UPDATED = "UPDATED"
REJECTED = "REJECTED"

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    NOT_CREATED: frozenset({GENERATING, DRAFT}),
    GENERATING: frozenset({DRAFT, REJECTED}),
    # DRAFT/UPDATED → USED_FOR_GENERATION 仅供 auto 模式（无人工确认）使用。
    DRAFT: frozenset(
        {REVIEW_REQUIRED, CONFIRMED, USED_FOR_GENERATION, REGENERATING, REJECTED, UPDATED}
    ),
    REVIEW_REQUIRED: frozenset({CONFIRMED, REGENERATING, REJECTED, UPDATED}),
    CONFIRMED: frozenset({USED_FOR_GENERATION, REGENERATING, REJECTED}),
    USED_FOR_GENERATION: frozenset({REGENERATING, UPDATED, REJECTED}),
    REGENERATING: frozenset({GENERATING, DRAFT, REJECTED}),
    UPDATED: frozenset(
        {REVIEW_REQUIRED, CONFIRMED, USED_FOR_GENERATION, REGENERATING, REJECTED, GENERATING}
    ),
    REJECTED: frozenset({REGENERATING, GENERATING}),
}

#: 可被正文生成使用的规划状态。
CONFIRMED_STATUSES = frozenset({CONFIRMED, USED_FOR_GENERATION})
#: load_active_plan 认为"仍有效"的状态（取最高版本）。
ACTIVE_STATUSES = frozenset({DRAFT, REVIEW_REQUIRED, CONFIRMED, USED_FOR_GENERATION, UPDATED})

JSON_FIELDS = (
    "score_points",
    "requirements",
    "conditions",
    "challenges",
    "strategy",
    "measures",
    "knowledge_refs",
    "fact_refs",
    "planned_tables",
    "planned_diagrams",
    "forbidden_assumptions",
    "quality_criteria",
    "user_adjustments",
    "validation_errors",
)
ID_REF_FIELDS = ("score_points", "requirements", "fact_refs", "knowledge_refs")
_ID_LABELS = {
    "score_points": "评分点",
    "requirements": "招标要求",
    "fact_refs": "事实引用",
    "knowledge_refs": "知识引用",
}

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_MAX_PLAN_CONTEXT = 6000


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ident(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return str(getattr(value, "id", "") or "")


def _iso(value: Any) -> str | None:
    return clock.iso_beijing(value)


def _bigrams(text: str) -> set[str]:
    value = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(text or ""))
    return {value[i : i + 2] for i in range(len(value) - 1)}


def _similarity(a: str, b: str) -> float:
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# ------------------------------------------------------------ 检索引用归一化
# 检索提供方（services.job_support）会给出 KnowledgeContext：text / downgrades /
# refs / diagnostics。旧提供方只给 (text, downgrades) 或纯文本，这里同时兼容，
# 且绝不在本模块按文本相似度重新"猜"知识 ID。

_REF_ATTRS = (
    "id", "kind", "chunk_id", "kb_id", "document_id", "page", "heading", "source",
    "snippet", "score", "rerank_score", "trust_tier", "degraded", "trace",
)


def _attr(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def retrieval_ref_payload(item: Any) -> dict[str, Any]:
    """把 provider 的真实 passage 引用转成可持久化/可审计的普通字典。"""

    ref: dict[str, Any] = {name: _jsonable(_attr(item, name)) for name in _REF_ATTRS}
    ref["id"] = _text(ref.get("id") or ref.get("chunk_id") or ref.get("document_id"))
    ref["page"] = _as_int(ref.get("page"), 0) if ref.get("page") is not None else None
    ref["score"] = _as_float(ref.get("score"))
    ref["rerank_score"] = (
        _as_float(ref.get("rerank_score")) if ref.get("rerank_score") is not None else None
    )
    # 未知降级状态保留 None，避免把“未知”误报为明确的“未降级”。
    ref["degraded"] = None if ref.get("degraded") is None else bool(ref["degraded"])
    trace = _text(ref.get("trace"))
    if not trace:
        bits = []
        if ref.get("document_id"):
            bits.append(f"文档 {ref['document_id']}")
        if ref.get("page"):
            bits.append(f"页码 {ref['page']}")
        if ref.get("heading"):
            bits.append(f"标题 {ref['heading']}")
        if ref.get("source"):
            bits.append(f"来源 {ref['source']}")
        trace = " · ".join(bits)
    ref["trace"] = trace
    return ref


def normalize_retrieval(retrieved: Any) -> tuple[str, int, list[dict[str, Any]], Any]:
    """统一 provider 返回值 → (文本, 降级数, 真实引用, 诊断)。

    对象有属性时优先读属性；否则退回旧的 ``(text, downgrades)`` / 文本元组。
    诊断保持 provider 的形态（KnowledgeContext 给出 dict）。
    """

    if retrieved is None:
        return "", 0, [], []
    if isinstance(retrieved, str):
        return retrieved, 0, [], []
    if isinstance(retrieved, dict) or hasattr(retrieved, "refs") or hasattr(retrieved, "text"):
        raw_refs = _list(_attr(retrieved, "refs"))
        raw_diagnostics = _attr(retrieved, "diagnostics")
        if isinstance(raw_diagnostics, dict):
            diagnostics: Any = _jsonable(raw_diagnostics)
        else:
            diagnostics = [_jsonable(item) for item in _list(raw_diagnostics)]
        return (
            _text(_attr(retrieved, "text")),
            _as_int(_attr(retrieved, "downgrades")),
            [retrieval_ref_payload(item) for item in raw_refs],
            diagnostics,
        )
    try:
        values = tuple(retrieved)
    except TypeError:
        values = (retrieved,)
    text = _text(values[0]) if values else ""
    downgrades = _as_int(values[1]) if len(values) > 1 else 0
    return text, downgrades, [], []


def scoped_knowledge_provider(
    factory: Callable[..., Any],
    db: Session,
    user_id: str | None,
    project_id: str,
    package_id: str | None,
) -> Any:
    """调用检索工厂并尽量传入项目/标段作用域；旧工厂保持兼容。"""

    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_scope = "project_id" in parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()
    )
    if accepts_scope:
        return factory(db, user_id, project_id=project_id, package_id=package_id)
    raise ValueError("知识检索工厂不支持项目/标段作用域，拒绝无作用域检索")


def chapter_plan_payload(row: ChapterPlan) -> dict[str, Any]:
    """把规划行序列化成 API/审计共用的字典（JSON 字段统一为 list）。"""

    payload: dict[str, Any] = {
        "id": row.id,
        "project_id": row.project_id,
        "node_id": row.node_id,
        "package_id": row.package_id or "",
        "version": int(row.version or 0),
        "status": row.status or DRAFT,
        "objective": row.objective or "",
        "raw_response": row.raw_response or "",
        "model_id": row.model_id or "",
        "prompt_version": row.prompt_version or "",
        "user_instruction": row.user_instruction or "",
        "created_by": row.created_by or "",
        "confirmed_by": row.confirmed_by or "",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "confirmed_at": _iso(row.confirmed_at),
        "used_at": _iso(row.used_at),
    }
    for field in JSON_FIELDS:
        payload[field] = _list(getattr(row, field, None))
    return payload


def _plan_query(db: Session, project_id: str, node_id: str):
    return (
        select(ChapterPlan)
        .where(
            ChapterPlan.project_id == project_id,
            ChapterPlan.node_id == node_id,
        )
        .order_by(ChapterPlan.version.desc())
    )


def load_active_plan(
    db: Session, project_id: str, node_id: str, package_id: str | None = None
) -> ChapterPlan | None:
    """取当前有效版本（最高版本号）；REJECTED/GENERATING/REGENERATING 不算有效。

    传 ``package_id`` 时，只接受同包件或项目级（``package_id`` 为空）的规划，
    跨标段规划按「不存在」处理。
    """

    for row in db.scalars(_plan_query(db, project_id, node_id)):
        # GENERATING/REGENERATING 是中间态，等下一次写入再判断；其余状态一旦出现
        # 就以该 newest meaningful 版本为准。REJECTED 必须阻断，不能回退到更旧的
        # CONFIRMED 版本——否则一次依赖失效会让旧规划继续被正文消费。
        if row.status in {GENERATING, REGENERATING}:
            continue
        if package_id and row.package_id and row.package_id != package_id:
            continue
        if row.status == REJECTED:
            return None
        if row.status in ACTIVE_STATUSES:
            return row
    return None


def load_plan_history(db: Session, project_id: str, node_id: str) -> list[ChapterPlan]:
    """按版本倒序返回一章的全部规划版本（含被拒绝/被取代的版本）。"""

    return list(db.scalars(_plan_query(db, project_id, node_id)))


def _kb_scope_allowed(kb: KnowledgeBase, project_id: str | None, package_id: str | None) -> bool:
    """与 knowledge.search 一致的项目/标段声明校验（缺声明即失败关闭）。"""

    options = kb.options if isinstance(kb.options, dict) else {}
    for key, requested in (("project_id", project_id), ("package_id", package_id)):
        declared = options.get(key)
        if declared in (None, ""):
            continue
        if isinstance(declared, bool) or not isinstance(declared, (str, int)):
            return False
        requested_text = str(requested or "").strip()
        if not requested_text or requested_text != str(declared).strip():
            return False
    return True


def _allowed_kb_ids(
    db: Session,
    user_id: str | None = None,
    project_id: str | None = None,
    package_id: str | None = None,
) -> set[str]:
    """本项目规划可引用的知识库范围。

    默认只有已启用的非个人共享库；仅当给出 ``user_id`` 时才追加该用户本人
    的个人库与所属企业的授权库，绝不按全局 ID 放行。带项目/标段声明的库
    必须与本次生成的作用域一致。
    """

    rows = list(db.scalars(select(KnowledgeBase).where(KnowledgeBase.enabled.is_(True))))
    uid = str(user_id or "")
    enterprise_ids: set[str] = set()
    if uid:
        try:
            from ..enterprise.assets import enterprise_kb_ids

            enterprise_ids = {str(item) for item in enterprise_kb_ids(db, uid)}
        except Exception:  # noqa: BLE001 - 企业库不可用时只保留共享与个人库
            import logging

            logging.getLogger(__name__).debug("解析授权企业知识库失败", exc_info=True)

    def _enterprise_allowed(options: dict) -> bool:
        enterprise_id = str(options.get("enterprise_id") or "")
        return not enterprise_id or enterprise_id in enterprise_ids

    allowed = {
        kb.id
        for kb in rows
        if not (kb.options or {}).get("personal")
        and _kb_scope_allowed(kb, project_id, package_id)
        and _enterprise_allowed(kb.options or {})
    }
    if not uid:
        return allowed
    for kb in rows:
        options = kb.options or {}
        if not options.get("personal") or not _kb_scope_allowed(kb, project_id, package_id):
            continue
        if str(options.get("user_id") or "") == uid or (
            str(options.get("enterprise_id") or "") in enterprise_ids
        ):
            allowed.add(kb.id)
    return allowed


def _project_valid_ids(
    db: Session,
    project_id: str,
    user_id: str | None = None,
    package_id: str | None = None,
) -> dict[str, set[str]]:
    scoring = set(db.scalars(select(ScoringItem.id).where(ScoringItem.project_id == project_id)))
    requirements = set(
        db.scalars(select(Requirement.id).where(Requirement.project_id == project_id))
    )
    facts = set(db.scalars(select(Fact.id).where(Fact.project_id == project_id)))
    fact_keys = set(db.scalars(select(Fact.key).where(Fact.project_id == project_id)))
    nodes = set(db.scalars(select(OutlineNode.id).where(OutlineNode.project_id == project_id)))
    kb_ids = _allowed_kb_ids(db, user_id, project_id, package_id)
    chunks = (
        set(db.scalars(select(KbChunk.id).where(KbChunk.kb_id.in_(kb_ids)))) if kb_ids else set()
    )
    documents = (
        set(db.scalars(select(KbDocument.id).where(KbDocument.kb_id.in_(kb_ids))))
        if kb_ids
        else set()
    )
    return {
        "scoring_ids": {str(item) for item in scoring if item},
        "requirement_ids": {str(item) for item in requirements if item},
        "fact_ids": {str(item) for item in facts if item},
        "fact_keys": {str(item) for item in fact_keys if item},
        "node_ids": {str(item) for item in nodes if item},
        "kb_ids": {str(item) for item in kb_ids if item},
        "knowledge_ids": {str(item) for item in chunks | documents if item},
    }


def _provider_knowledge_refs(
    refs: list[dict[str, Any]], valid: dict[str, set[str]], errors: list[str]
) -> list[dict[str, Any]]:
    """只保留 provider 给出的、落在授权知识库内的真实引用。"""

    kept: list[dict[str, Any]] = []
    for ref in refs:
        ident = _text(ref.get("id"))
        kb_id = _text(ref.get("kb_id"))
        if not _UUID_RE.fullmatch(ident) or (
            ident not in valid["knowledge_ids"] and kb_id not in valid["kb_ids"]
        ):
            continue
        kept.append(ref)
    dropped = len(refs) - len(kept)
    if dropped:
        errors.append(f"已剔除 {dropped} 条不属于本项目授权知识库的检索引用")
    return kept


def build_plan_context(
    db: Session,
    project_id: str,
    node: OutlineNode,
    user_instruction: str = "",
    knowledge_provider: Callable[[str], Any] | None = None,
    user_id: str | None = None,
    package_id: str | None = None,
    lessons_text: str = "",
) -> dict[str, Any]:
    """在短会话里构造规划输入；只收集真实行，不含任何生成的 ID。

    ``lessons_text`` 是 :func:`bidcraft.evaluation.learning.lessons_block` 产出的
    未审核建议经验，原样透传到上下文；它只作为提示词参考，不会被当作真实引用 ID
    来源，也不参与引用校验。
    """

    node_id = _ident(node)
    project = db.get(Project, project_id)
    anchor = f"{_text(getattr(node, 'heading', ''))} {_text(getattr(node, 'intent', ''))}".strip()
    needs = _list(getattr(node, "needs", None))
    scoring_rows = list(
        db.scalars(select(ScoringItem).where(ScoringItem.project_id == project_id))
    )
    scoring_rows.sort(
        key=lambda row: (
            _similarity(anchor, f"{row.name or ''} {row.section_hint or ''}"),
            float(row.score or 0),
        ),
        reverse=True,
    )
    requirement_rows = list(
        db.scalars(select(Requirement).where(Requirement.project_id == project_id))
    )
    requirement_rows.sort(
        key=lambda row: (
            row.node_id == node_id,
            bool(row.mandatory),
            _similarity(anchor, row.text or ""),
        ),
        reverse=True,
    )
    fact_rows = list(db.scalars(select(Fact).where(Fact.project_id == project_id)))
    fact_rows.sort(
        key=lambda row: (
            row.key in needs,
            bool(row.required),
            _similarity(anchor, f"{row.label or ''} {row.key or ''}"),
        ),
        reverse=True,
    )
    knowledge_text = ""
    knowledge_downgrades = 0
    knowledge_diagnostics: list[Any] = []
    provider_refs: list[dict[str, Any]] = []
    if knowledge_provider is not None:
        try:
            retrieved = knowledge_provider(anchor or _text(getattr(node, "heading", "")))
            knowledge_text, knowledge_downgrades, provider_refs, knowledge_diagnostics = (
                normalize_retrieval(retrieved)
            )
        except Exception:  # noqa: BLE001 - 检索是可选上下文，失败不阻断规划
            knowledge_text = ""
    package_id = package_id or str((project.meta or {}).get("package_id") or "") or None
    valid = _project_valid_ids(db, project_id, user_id, package_id)
    ref_errors: list[str] = []
    knowledge = _provider_knowledge_refs(provider_refs, valid, ref_errors)
    knowledge_warning = ""
    if ref_errors:
        knowledge_warning = ref_errors[0]
    # provider 的真实引用（含外部 API 库）可作为模型引用的合法 ID 来源。
    valid["knowledge_ids"] |= {str(ref["id"]) for ref in knowledge if ref.get("id")}
    other_nodes = list(
        db.scalars(
            select(OutlineNode).where(
                OutlineNode.project_id == project_id,
                OutlineNode.id != node_id,
            )
        )
    )
    context: dict[str, Any] = {
        "project_id": project_id,
        "node_id": node_id,
        "package_id": package_id,
        "package": _text(getattr(project, "package", "")),
        "heading": _text(getattr(node, "heading", "")),
        "intent": _text(getattr(node, "intent", "")),
        "target_words": int(getattr(node, "target_words", 0) or 0),
        "needs": [str(item) for item in needs],
        "user_instruction": _text(user_instruction),
        "lessons_text": _text(lessons_text),
        "scoring": [
            {
                "id": row.id,
                "name": row.name or "",
                "score": float(row.score or 0),
                "criteria": row.criteria or "",
                "section_hint": row.section_hint or "",
            }
            for row in scoring_rows[:12]
        ],
        "requirements": [
            {
                "id": row.id,
                "text": row.text or "",
                "mandatory": bool(row.mandatory),
                "marker": row.marker or "",
                "source_quote": row.source_quote or "",
            }
            for row in requirement_rows[:15]
        ],
        "facts": [
            {
                "id": row.id,
                "key": row.key or "",
                "label": row.label or "",
                "value": row.value or "",
                "unit": row.unit or "",
                "status": row.status or "",
            }
            for row in fact_rows[:12]
        ],
        "knowledge": knowledge,
        "knowledge_text": knowledge_text,
        "knowledge_downgrades": knowledge_downgrades,
        "knowledge_diagnostics": knowledge_diagnostics,
        "knowledge_warning": knowledge_warning,
        "other_nodes": [
            {"id": row.id, "heading": row.heading or ""} for row in other_nodes[:20]
        ],
    }
    context.update(valid)
    return context


def _deterministic_plan(context: dict[str, Any]) -> dict[str, list[Any] | str]:
    """由真实项目数据推导规划；没有模型时也能得到可校验、可确认的结果。"""

    heading = context["heading"] or "本章"
    intent = context["intent"]
    scoring = context["scoring"]
    requirements = context["requirements"]
    facts = context["facts"]
    instruction = context["user_instruction"]
    objective = f"围绕「{heading}」逐项响应招标文件要求与评分标准，形成可核验的技术标正文。"
    if intent:
        objective += f"章节意图：{intent}。"
    if instruction:
        objective += f"用户补充要求：{instruction}。"

    score_points = [
        {
            "id": item["id"],
            "name": item["name"],
            "score": item["score"],
            "criteria": item["criteria"],
        }
        for item in scoring[:6]
    ]
    req_rows = [
        {
            "id": item["id"],
            "text": item["text"],
            "mandatory": item["mandatory"],
            "marker": item["marker"],
        }
        for item in requirements[:8]
    ]
    fact_refs = [
        {
            "id": item["id"],
            "key": item["key"],
            "label": item["label"],
            "value": item["value"],
        }
        for item in facts[:8]
    ]
    conditions = [
        f"{item['label'] or item['key']}：{item['value'] or '{{待填:' + item['key'] + '}}'}"
        for item in facts[:5]
        if item["key"]
    ]
    challenges: list[str] = []
    for item in requirements[:3]:
        challenges.append(
            f"逐条响应{'强制性' if item['mandatory'] else '一般性'}要求：{item['text'][:120]}"
        )
    for item in facts:
        if item["status"] in {"missing", "conflict"}:
            challenges.append(f"事实台账中的「{item['key']}」为 {item['status']}，不得据此下结论")
    if not challenges:
        challenges.append("招标文件未给出的参数与数量不得估算，统一写待填占位符")

    strategy = [
        f"以「{heading}」为主线，先概述响应口径，再按评分点与招标要求分层展开。",
        "先引用事实台账中的真实取值，再写实施方案；事实缺失处写 {{待填:字段名}}，不编造。",
    ]
    for item in scoring[:4]:
        criterion = item["criteria"] or item["name"]
        strategy.append(f"针对评分项「{item['name']}」：{criterion}")
    if instruction:
        strategy.append(f"落实用户指令：{instruction}")
    if context.get("knowledge_warning"):
        strategy.append(context["knowledge_warning"])

    measures = [
        "逐条对照招标技术参数给出具体响应值与配置，不以「完全响应」代替。",
        "数量、单位、期限与事实台账逐项核对，同一对象前后保持一致。",
        "涉及清单、点位、参数、容量计算时使用 Markdown 表格并列明依据。",
        "计划性措施不得写成企业已完成的事实；无依据的量化承诺一律不写。",
    ]
    planned_tables: list[str] = []
    if scoring:
        planned_tables.append("评分响应对照表（评分项、分值、响应要点、正文位置）")
    if facts:
        planned_tables.append("项目事实核对表（字段、取值、来源、是否待填）")
    planned_tables.append("技术参数响应表（参数、招标要求、响应值、偏离）")
    planned_diagrams = ["施工/实施进度网络图（仅在招标文件要求或台账有工期依据时绘制）"]
    if re.search(r"系统|网络|拓扑|架构|布线", heading + intent):
        planned_diagrams.append("系统拓扑结构图（标注台账中已有的设备与点位口径）")
    forbidden_assumptions = [
        "不编造未出现在事实台账的报价、资质、业绩、人员、证书与专利编号。",
        "不把计划性措施写成企业已经具备的事实。",
        "不跨项目、跨标段引用其他项目的资料或编号。",
    ]
    quality_criteria = [
        "正文覆盖本章选中的全部评分点与强制性要求。",
        "所有数量、期限、参数与事实台账一致，未列出的取值写待填占位符。",
        f"正文篇幅不少于目标字数 {context['target_words']} 的 80%。",
    ]
    return {
        "objective": objective,
        "score_points": score_points,
        "requirements": req_rows,
        "conditions": conditions,
        "challenges": challenges,
        "strategy": strategy,
        "measures": measures,
        "knowledge_refs": context["knowledge"],
        "fact_refs": fact_refs,
        "planned_tables": planned_tables,
        "planned_diagrams": planned_diagrams,
        "forbidden_assumptions": forbidden_assumptions,
        "quality_criteria": quality_criteria,
    }


def _model_plan_output(client: Any, context: dict[str, Any]) -> str:
    """在调用方的事务外请求模型，返回原始 JSON 文本（失败返回空串）。"""

    try:
        from .prompts import chapter_plan_messages

        messages = chapter_plan_messages(context)
        return str(client.complete(messages, max_tokens=1800, temperature=0.1) or "")
    except Exception:  # noqa: BLE001 - 模型不可用一律回退确定性规划
        return ""


def _parse_model_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    for candidate in (text, _extract_object(text)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _extract_object(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else ""


def _validated_model_fields(
    data: dict[str, Any], context: dict[str, Any], errors: list[str]
) -> dict[str, list[Any] | str]:
    """只接收模型输出中可映射到真实行的字段，其他字段一律忽略。"""

    result: dict[str, list[Any] | str] = {}
    objective = _text(data.get("objective"))
    if objective:
        result["objective"] = objective[:1200]
    for field in JSON_FIELDS:
        if field == "validation_errors" or field not in data:
            continue
        value = _list(data.get(field))
        if value:
            result[field] = value
    for field in ID_REF_FIELDS:
        if field not in result:
            continue
        valid_key = {
            "score_points": "scoring_ids",
            "requirements": "requirement_ids",
            "fact_refs": "fact_ids",
            "knowledge_refs": "knowledge_ids",
        }[field]
        valid = context[valid_key]
        result[field] = _filter_refs(
            result[field],
            valid,
            field,
            errors,
            context["node_ids"],
            fact_keys=context["fact_keys"],
        )
    return result


def _filter_refs(
    rows: list[Any],
    valid: set[str],
    label: str,
    errors: list[str],
    node_ids: set[str] | None = None,
    *,
    fact_keys: set[str] | None = None,
) -> list[Any]:
    """所有引用必须有真实 ID；无 ID / 非 UUID / 不在本项目范围内的一律剔除。"""

    nodes = valid if node_ids is None else node_ids
    keys = fact_keys or set()
    kept: list[Any] = []
    dropped = 0
    for item in rows:
        if isinstance(item, dict):
            ident = _text(item.get("id") or item.get(f"{label}_id"))
            if label == "fact_refs" and not ident:
                ident = _text(item.get("key"))
            valid_ref = ident in keys if label == "fact_refs" and not _UUID_RE.fullmatch(ident) else (
                bool(_UUID_RE.fullmatch(ident)) and ident in valid
            )
            node_id = _text(item.get("node_id"))
            if (not valid_ref) or (node_id and node_id not in nodes):
                dropped += 1
                continue
        elif isinstance(item, str):
            stripped = item.strip()
            if label == "fact_refs" and stripped in keys:
                kept.append(item)
                continue
            if not _UUID_RE.fullmatch(stripped) or stripped not in valid:
                dropped += 1
                continue
        else:
            dropped += 1
            continue
        kept.append(item)
    if dropped:
        errors.append(
            f"已剔除 {dropped} 条无效的{_ID_LABELS.get(label, label)}引用"
            "（ID 不属于本项目真实数据或不是真实 ID）"
        )
    return kept


def _add_error(plan: ChapterPlan, message: str) -> None:
    current = _list(plan.validation_errors)
    if message not in current:
        current.append(message)
        plan.validation_errors = current


def plan_dependency_staleness(db: Session, plan: ChapterPlan) -> list[str]:
    """比较规划引用的真实行与当前值，返回中文依赖变化列表。

    已确认规划只在依赖稳定时可复用。这里不新增字段/迁移，直接逐条读取规划
    保存时写入 JSON 的真实 ID 与取值，和当前 ``ScoringItem`` / ``Requirement`` /
    ``Fact`` / ``KbDocument`` / ``KbChunk`` / ``OutlineNode`` 对比。外部 API 知识库
    引用（无本地 document/chunk 行）无法比对正文，不做臆测性判废。
    """

    reasons: list[str] = []

    def add(message: str) -> None:
        if message not in reasons and len(reasons) < 20:
            reasons.append(message)

    node = db.get(OutlineNode, plan.node_id) if plan.node_id else None
    if node is None or node.project_id != plan.project_id:
        add("大纲节点已删除或不再属于本项目，规划失效")
    else:
        match = re.search(r"围绕「(.+?)」", _text(plan.objective))
        heading = _text(node.heading)
        if match and heading and match.group(1) != heading:
            add(f"大纲标题已从「{match.group(1)}」变更为「{heading}」，规划失效")

    for row in _list(plan.score_points):
        if not isinstance(row, dict):
            continue
        ident = _text(row.get("id"))
        if not ident:
            continue
        item = db.get(ScoringItem, ident)
        if item is None or item.project_id != plan.project_id:
            add(f"评分项 {ident} 已删除，规划失效")
            continue
        if "name" in row and _text(row["name"]) != _text(item.name):
            add(f"评分项「{_text(item.name)}」名称已变化，规划失效")
        if "score" in row:
            try:
                old_score = float(row["score"] or 0)
            except (TypeError, ValueError):
                old_score = -1.0
            if old_score != float(item.score or 0):
                add(f"评分项「{_text(item.name)}」分值已变化，规划失效")
        if "criteria" in row and _text(row["criteria"]) != _text(item.criteria):
            add(f"评分项「{_text(item.name)}」评分标准已变化，规划失效")

    for row in _list(plan.requirements):
        if not isinstance(row, dict):
            continue
        ident = _text(row.get("id"))
        if not ident:
            continue
        item = db.get(Requirement, ident)
        if item is None or item.project_id != plan.project_id:
            add(f"招标要求 {ident} 已删除，规划失效")
            continue
        if "text" in row and _text(row["text"]) != _text(item.text):
            add("招标要求正文已变化，规划失效")
        if "mandatory" in row and bool(row["mandatory"]) != bool(item.mandatory):
            add("招标要求强制属性已变化，规划失效")
        if "marker" in row and _text(row["marker"]) != _text(item.marker):
            add("招标要求标记已变化，规划失效")

    for row in _list(plan.fact_refs):
        if not isinstance(row, dict):
            continue
        ident = _text(row.get("id"))
        if not ident:
            continue
        item = db.get(Fact, ident)
        if item is None or item.project_id != plan.project_id:
            add(f"事实台账 {ident} 已删除，规划失效")
            continue
        for key, attr in (("key", "key"), ("label", "label"), ("value", "value"), ("unit", "unit")):
            if key in row and _text(row[key]) != _text(getattr(item, attr, "")):
                add(f"事实「{_text(item.key) or ident}」的{key}已变化，规划失效")

    for row in _list(plan.knowledge_refs):
        if not isinstance(row, dict):
            continue
        chunk_id = _text(row.get("chunk_id") or row.get("id"))
        doc_id = _text(row.get("document_id"))
        chunk = db.get(KbChunk, chunk_id) if chunk_id else None
        document = db.get(KbDocument, doc_id) if doc_id else None
        if chunk is None and document is None:
            # 外部 API 知识库引用没有本地行，交由检索层按信任等级降级。
            continue
        if chunk_id and chunk is None:
            add("知识检索引用指向的正文块已删除，规划失效")
        if chunk is not None and (chunk_id != chunk.id or (doc_id and doc_id != chunk.document_id)):
            add("知识检索引用指向的正文块已变化，规划失效")
        if document is not None:
            status = _text(getattr(document, "review_status", ""))
            if status and status not in {"published", "ready"}:
                add("知识文档已退回未发布状态，规划失效")
    return reasons


def invalidate_stale_plan(db: Session, plan: ChapterPlan | None) -> list[str]:
    """把依赖已变化的规划明确判废；返回本次新增的中文原因。"""

    if plan is None:
        return []
    reasons = plan_dependency_staleness(db, plan)
    if not reasons:
        return []
    for message in reasons:
        _add_error(plan, message)
    if REJECTED in VALID_TRANSITIONS.get(plan.status or DRAFT, frozenset()):
        plan.status = REJECTED
    db.flush()
    return reasons


def validate_chapter_plan(
    db: Session,
    plan: ChapterPlan,
    *,
    user_id: str | None = None,
    package_id: str | None = None,
    valid_ids: dict[str, set[str]] | None = None,
) -> list[str]:
    """校验并就地修复规划：剔除无效引用，返回中文问题列表。

    这是"可修复"校验：坏的引用被删掉而不是让流程失败；调用方在确认前必须
    重新检查「绑定节点是否存在」这类不可修复错误。
    """

    project_id = plan.project_id
    node = db.get(OutlineNode, plan.node_id) if plan.node_id else None
    if node is None or node.project_id != project_id:
        _add_error(plan, "规划绑定的章节不存在或不属于本项目，已拒绝该规划")
        if plan.status != REJECTED:
            plan.status = REJECTED
        return _list(plan.validation_errors)
    valid = valid_ids or _project_valid_ids(db, project_id, user_id, package_id)
    for field in ID_REF_FIELDS:
        valid_ids = valid[{
            "score_points": "scoring_ids",
            "requirements": "requirement_ids",
            "fact_refs": "fact_ids",
            "knowledge_refs": "knowledge_ids",
        }[field]]
        if field == "knowledge_refs":
            # 已落库的 provider 引用（如外部 API 库 passage）可在复核阶段保留。
            valid_ids = valid_ids | {
                _text(row.get("id"))
                for row in _list(getattr(plan, field))
                if isinstance(row, dict)
                and _UUID_RE.fullmatch(_text(row.get("id")))
                and _text(row.get("kb_id")) in valid["kb_ids"]
            }
        repair_errors: list[str] = []
        rows = _filter_refs(
            _list(getattr(plan, field)),
            valid_ids,
            field,
            repair_errors,
            valid["node_ids"],
            fact_keys=valid["fact_keys"],
        )
        for message in repair_errors:
            _add_error(plan, message)
        if rows != _list(getattr(plan, field)):
            setattr(plan, field, rows)
    # 描述性字段里若夹带伪造的 node_id 也要剔除。
    for field in JSON_FIELDS:
        if field in ID_REF_FIELDS:
            continue
        kept: list[Any] = []
        dropped = False
        for item in _list(getattr(plan, field)):
            if (
                isinstance(item, dict)
                and _text(item.get("node_id"))
                and item["node_id"] not in valid["node_ids"]
            ):
                dropped = True
                continue
            kept.append(item)
        if dropped:
            _add_error(plan, f"已剔除 {field} 中指向其他章节的引用")
            setattr(plan, field, kept)
    if not _text(plan.objective):
        _add_error(plan, "写作目标为空，已回填默认目标")
        plan.objective = f"围绕「{node.heading or '本章'}」响应招标文件要求"
    if not _list(plan.strategy):
        _add_error(plan, "写作策略为空，已回填默认策略")
        plan.strategy = ["按评分标准逐条响应，事实缺失处写待填占位符"]
    if not _list(plan.quality_criteria):
        _add_error(plan, "质量口径为空，已回填默认口径")
        plan.quality_criteria = ["数量与期限必须与事实台账一致"]
    return _list(plan.validation_errors)


def _transition(plan: ChapterPlan, target: str) -> None:
    current = plan.status or DRAFT
    if target not in VALID_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"规划状态不能从 {current} 变为 {target}")


def _next_version(db: Session, project_id: str, node_id: str) -> int:
    value = db.scalar(
        select(func.max(ChapterPlan.version)).where(
            ChapterPlan.project_id == project_id,
            ChapterPlan.node_id == node_id,
        )
    )
    return int(value or 0) + 1


def _retire(db: Session, plan: ChapterPlan | None) -> None:
    if plan is None:
        return
    try:
        _transition(plan, REGENERATING)
        plan.status = REGENERATING
    except ValueError:
        # 已终态的历史版本保留原状态，由版本号决定有效版本。
        pass


def _new_plan(
    db: Session,
    project_id: str,
    node: OutlineNode,
    data: dict[str, Any],
    *,
    version: int,
    status: str,
    user_instruction: str,
    created_by: str,
    package_id: str | None,
    raw_response: str,
    model_id: str,
) -> ChapterPlan:
    row = ChapterPlan(
        project_id=project_id,
        node_id=node.id,
        package_id=package_id,
        version=version,
        status=status,
        objective=str(data.get("objective") or ""),
        raw_response=raw_response,
        model_id=model_id,
        prompt_version=PROMPT_VERSION,
        user_instruction=user_instruction,
        created_by=created_by,
    )
    for field in JSON_FIELDS:
        setattr(row, field, _list(data.get(field)))
    db.add(row)
    db.flush()
    return row


def generate_chapter_plan(
    db: Session,
    project: Project,
    node: OutlineNode,
    user_instruction: str = "",
    created_by: str = "",
    regenerate: bool = False,
    *,
    client: Any | None = None,
    model_output: str = "",
    knowledge_provider: Callable[[str], Any] | None = None,
    package_id: str | None = None,
    user_id: str | None = None,
) -> ChapterPlan:
    """为一章生成新版本规划（实数据回退 + 可选模型 JSON 覆盖）。

    ``model_output`` 由调用方在短会话之外调用模型得到；本函数只做短的 DB
    读写事务。传 ``client`` 时本函数也会直接调用模型（仅用于 CLI/测试）。
    """

    project_id = _ident(project)
    node_id = _ident(node)
    fresh_node = db.get(OutlineNode, node_id)
    if fresh_node is None or fresh_node.project_id != project_id:
        raise LookupError("章节不存在或不属于该项目")
    node = fresh_node
    context = build_plan_context(
        db, project_id, node, user_instruction, knowledge_provider, user_id, package_id
    )
    data: dict[str, Any] = _deterministic_plan(context)
    errors: list[str] = []
    shape = context.get("package_id") or package_id
    raw = model_output or (_model_plan_output(client, context) if client is not None else "")
    parsed = _parse_model_json(raw)
    if parsed:
        data.update(_validated_model_fields(parsed, context, errors))
        raw_response = raw[:8000]
        model_id = str(getattr(getattr(client, "settings", None), "model_name", "") or "model")
    else:
        raw_response = (
            "fallback: 未取得可用模型 JSON，规划由本项目真实评分项/要求/事实确定性生成；"
            f"原始返回：{raw[:1000]}"
        )
        model_id = "deterministic-fallback"
    if context.get("knowledge_warning"):
        errors.append(context["knowledge_warning"])
    data["validation_errors"] = errors
    previous = load_active_plan(db, project_id, node_id, package_id)
    if regenerate:
        _retire(db, previous)
    row = _new_plan(
        db,
        project_id,
        node,
        data,
        version=_next_version(db, project_id, node_id),
        status=DRAFT,
        user_instruction=user_instruction,
        created_by=created_by,
        package_id=shape,
        raw_response=raw_response,
        model_id=model_id,
    )
    validate_chapter_plan(db, row, user_id=user_id, valid_ids=context)
    return row


def update_chapter_plan(
    db: Session,
    project: Project,
    node: OutlineNode,
    updates: Any,
    created_by: str = "",
    package_id: str | None = None,
    user_id: str | None = None,
) -> ChapterPlan:
    """按当前有效版本追加 UPDATED 新版本；旧版本同时标记为被取代。"""

    project_id = _ident(project)
    node_id = _ident(node)
    current = load_active_plan(db, project_id, node_id, package_id)
    if current is None:
        raise LookupError("该章节还没有可修改的规划")
    fresh_node = db.get(OutlineNode, node_id)
    if fresh_node is None or fresh_node.project_id != project_id:
        raise LookupError("章节不存在或不属于该项目")

    def value(field: str, default: Any = None) -> Any:
        if isinstance(updates, dict):
            return updates.get(field, default)
        return getattr(updates, field, default)

    data = chapter_plan_payload(current)
    for field in ("objective",) + JSON_FIELDS[:-2]:
        incoming = value(field)
        if incoming is None:
            continue
        data[field] = _list(incoming) if field != "objective" else _text(incoming)
    adjustments = _list(data.get("user_adjustments"))
    adjustments.append(
        {
            "by": created_by,
            "at": _iso(clock.utc_now()),
            "note": _text(value("note")),
            "changes": {
                field: data[field]
                for field in ("objective",) + JSON_FIELDS[:-2]
                if value(field) is not None
            },
        }
    )
    data["user_adjustments"] = adjustments
    data["validation_errors"] = []
    _retire(db, current)
    row = _new_plan(
        db,
        project_id,
        fresh_node,
        data,
        version=_next_version(db, project_id, node_id),
        status=UPDATED,
        user_instruction=str(value("user_instruction", current.user_instruction) or ""),
        created_by=created_by or current.created_by,
        package_id=current.package_id,
        raw_response=f"update: 基于版本 {current.version} 的人工调整",
        model_id="user-update",
    )
    validate_chapter_plan(db, row, user_id=user_id, package_id=package_id)
    return row


def confirm_chapter_plan(
    db: Session,
    plan: ChapterPlan,
    confirmed_by: str = "",
    note: str = "",
    user_id: str | None = None,
) -> ChapterPlan:
    """校验通过后确认规划；不可修复的错误（如节点越权）直接拒绝。"""

    project = db.get(Project, plan.project_id)
    # 以规划自身的标段为准：项目 meta 可能已切换到另一标段，确认时不能按新标段
    # 把原规划里的合法知识引用静默剔除。
    package_id = str(plan.package_id or "") or (
        str((project.meta or {}).get("package_id") or "") or None if project else None
    )
    validate_chapter_plan(db, plan, user_id=user_id, package_id=package_id)
    if plan.status == REJECTED:
        raise ValueError("规划绑定章节无效，不能确认")
    if plan.status != CONFIRMED:
        _transition(plan, CONFIRMED)
    plan.status = CONFIRMED
    plan.confirmed_by = confirmed_by or ""
    plan.confirmed_at = clock.utc_now()
    if note:
        adjustments = _list(plan.user_adjustments)
        adjustments.append({"by": confirmed_by, "at": _iso(clock.utc_now()), "note": note})
        plan.user_adjustments = adjustments
    db.flush()
    return plan


def mark_plan_used(db: Session, plan: ChapterPlan | None) -> ChapterPlan | None:
    """正文生成后幂等标记 USED_FOR_GENERATION；重复调用只刷新 used_at。"""

    if plan is None:
        return None
    if plan.status == USED_FOR_GENERATION:
        # 真正幂等：重复投递/重复生成不改写 used_at，便于审计对账。
        return plan
    _transition(plan, USED_FOR_GENERATION)
    plan.status = USED_FOR_GENERATION
    plan.used_at = clock.utc_now()
    db.flush()
    return plan


def plan_context_text(
    plan: ChapterPlan | None, *, lessons_text: str = "", cap: int = _MAX_PLAN_CONTEXT
) -> str:
    """把已确认规划转成正文提示词可插入的纯文本。

    人工调整与历史经验都放在显式的 advisory 段里：它们提示复核方向，但不是招标
    事实，正文不得把它们当作事实或真实引用 ID 使用。
    """

    if plan is None:
        return ""
    payload = chapter_plan_payload(plan)
    lines = [
        "【章节规划（已确认，必须遵循；与大纲冲突时以本规划为准）】",
        f"写作目标：{payload['objective']}",
        f"规划版本：{payload['version']}（状态 {payload['status']}）",
    ]

    def section(title: str, field: str, *, with_id: bool = False) -> None:
        rows = payload.get(field) or []
        if not rows:
            return
        lines.append(f"{title}：")
        for row in rows:
            if isinstance(row, dict):
                ident = _text(row.get("id"))
                body = " ".join(
                    _text(row.get(key))
                    for key in ("name", "text", "criteria", "key", "value", "label", "heading")
                    if _text(row.get(key))
                ) or json.dumps(row, ensure_ascii=False)
                prefix = f"[ID:{ident}] " if with_id and ident else ""
                lines.append(f"- {prefix}{body[:300]}")
            else:
                lines.append(f"- {_text(row)[:300]}")

    section("写作策略", "strategy")
    section("落实措施", "measures")
    section("本章覆盖的评分点", "score_points", with_id=True)
    section("必须响应的招标要求", "requirements", with_id=True)
    section("前提条件", "conditions")
    section("难点与风险", "challenges")
    section("计划表格", "planned_tables")
    section("计划图示", "planned_diagrams")
    section("禁止假设", "forbidden_assumptions")
    section("质量口径", "quality_criteria")
    section("事实引用", "fact_refs", with_id=True)
    # 知识引用带原文 ID 与溯源链；正文只能引用这里列出的真实条目。
    knowledge_rows = payload.get("knowledge_refs") or []
    if knowledge_rows:
        lines.append("知识引用：")
        for row in knowledge_rows:
            if not isinstance(row, dict):
                lines.append(f"- {_text(row)[:300]}")
                continue
            bits = [f"[ID:{_text(row.get('id'))}]"]
            for key in ("heading", "source", "snippet"):
                if _text(row.get(key)):
                    bits.append(_text(row.get(key)))
            detail = []
            if row.get("document_id"):
                detail.append(f"文档 {row['document_id']}")
            if row.get("page"):
                detail.append(f"页码 {row['page']}")
            if row.get("score") not in (None, ""):
                detail.append(f"检索分 {row['score']}")
            if row.get("rerank_score") not in (None, ""):
                detail.append(f"重排分 {row['rerank_score']}")
            if row.get("trust_tier"):
                detail.append(f"可信度 {row['trust_tier']}")
            if row.get("degraded"):
                detail.append("已降级，仅可引用出处")
            if row.get("trace"):
                detail.append(f"溯源 {row['trace']}")
            suffix = f"（{' · '.join(detail)}）" if detail else ""
            lines.append(f"- {' '.join(bits)}{suffix}"[:400])
    adjustments = payload.get("user_adjustments") or []
    if adjustments:
        lines.append("【历史人工调整（未审核建议经验，仅供参考，非招标事实）】")
        for row in adjustments:
            if not isinstance(row, dict):
                lines.append(f"- {_text(row)[:300]}")
                continue
            note = _text(row.get("note"))
            if not note:
                continue
            meta = " · ".join(
                part
                for part in (_text(row.get("by")), _text(row.get("at")))
                if part
            )
            suffix = f"（{meta}）" if meta else ""
            lines.append(f"- {note[:300]}{suffix}")
    lessons = _text(lessons_text)
    if lessons:
        lines.append("【未审核建议经验（非核实事实，仅供复核参考，不得写入正文作为事实）】")
        lines.append(lessons[:1200])
    text = "\n".join(lines)
    return text[:cap]
