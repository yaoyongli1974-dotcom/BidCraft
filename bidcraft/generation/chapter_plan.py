"""章节规划引擎（Phase 1，backend）。

一章一条可版本化的规划记录：生成 → 校验（剔除编造引用）→ 人工确认 → 用于正文。
所有引用 ID 都从本项目真实行中取得；模型给出的 ID 也必须与真实行匹配，否则
直接丢弃并记入 ``validation_errors``，绝不把虚构的评分/事实/知识/章节 ID
传给正文生成。持久化全部走短事务，模型输入由调用方在事务外构造后传入。
"""

from __future__ import annotations

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
PROMPT_VERSION = "chapter_plan_v1"

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
        if row.status not in ACTIVE_STATUSES:
            continue
        if package_id and row.package_id and row.package_id != package_id:
            continue
        return row
    return None


def load_plan_history(db: Session, project_id: str, node_id: str) -> list[ChapterPlan]:
    """按版本倒序返回一章的全部规划版本（含被拒绝/被取代的版本）。"""

    return list(db.scalars(_plan_query(db, project_id, node_id)))


def _project_valid_ids(db: Session, project_id: str) -> dict[str, set[str]]:
    scoring = set(db.scalars(select(ScoringItem.id).where(ScoringItem.project_id == project_id)))
    requirements = set(
        db.scalars(select(Requirement.id).where(Requirement.project_id == project_id))
    )
    facts = set(db.scalars(select(Fact.id).where(Fact.project_id == project_id)))
    fact_keys = set(db.scalars(select(Fact.key).where(Fact.project_id == project_id)))
    nodes = set(db.scalars(select(OutlineNode.id).where(OutlineNode.project_id == project_id)))
    chunks = set(db.scalars(select(KbChunk.id)))
    documents = set(db.scalars(select(KbDocument.id)))
    return {
        "scoring_ids": {str(item) for item in scoring if item},
        "requirement_ids": {str(item) for item in requirements if item},
        "fact_ids": {str(item) for item in facts if item},
        "fact_keys": {str(item) for item in fact_keys if item},
        "node_ids": {str(item) for item in nodes if item},
        "knowledge_ids": {str(item) for item in chunks | documents if item},
    }


def _knowledge_refs(
    db: Session, query: str, knowledge_text: str
) -> tuple[list[dict[str, Any]], str]:
    """把检索文本映射回真实 KbChunk/KbDocument；映射不到就不产生引用 ID。"""

    refs: list[dict[str, Any]] = []
    if not knowledge_text:
        return refs, ""
    # 只映射已启用的共享知识库；个人库按 options.personal 隔离，避免把
    # 其他用户的知识 ID 写进项目规划。
    enabled_kb_ids = [
        kb.id
        for kb in db.scalars(select(KnowledgeBase))
        if kb.enabled and not (kb.options or {}).get("personal")
    ]
    if not enabled_kb_ids:
        return refs, "知识检索结果无法映射到真实知识条目，已忽略知识引用，正文不得据此编造资料"
    chunks = list(
        db.scalars(
            select(KbChunk)
            .where(KbChunk.kb_id.in_(enabled_kb_ids))
            .order_by(KbChunk.created_at.desc())
            .limit(300)
        )
    )
    scored: list[tuple[float, KbChunk]] = []
    for chunk in chunks:
        score = _similarity(query, f"{chunk.heading or ''} {str(chunk.text or '')[:300]}")
        if score > 0.05:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    for _score, chunk in scored[:4]:
        refs.append(
            {
                "id": chunk.id,
                "kind": "chunk",
                "kb_id": chunk.kb_id or "",
                "heading": chunk.heading or "",
                "source": chunk.source or "",
                "snippet": str(chunk.text or "")[:240],
            }
        )
    if not refs:
        documents = list(
            db.scalars(
                select(KbDocument)
                .where(KbDocument.kb_id.in_(enabled_kb_ids))
                .order_by(KbDocument.created_at.desc())
                .limit(50)
            )
        )
        for document in documents[:2]:
            refs.append(
                {
                    "id": document.id,
                    "kind": "document",
                    "kb_id": document.kb_id or "",
                    "heading": document.source or "",
                    "source": document.source or "",
                    "snippet": "",
                }
            )
    warning = ""
    if not refs:
        warning = "知识检索结果无法映射到真实知识条目，已忽略知识引用，正文不得据此编造资料"
    return refs, warning


def build_plan_context(
    db: Session,
    project_id: str,
    node: OutlineNode,
    user_instruction: str = "",
    knowledge_provider: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """在短会话里构造规划输入；只收集真实行，不含任何生成的 ID。"""

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
    if knowledge_provider is not None:
        try:
            retrieved = knowledge_provider(anchor or _text(getattr(node, "heading", "")))
            if isinstance(retrieved, tuple):
                knowledge_text = str(retrieved[0] or "")
            else:
                knowledge_text = str(retrieved or "")
        except Exception:  # noqa: BLE001 - 检索是可选上下文，失败不阻断规划
            knowledge_text = ""
    knowledge, knowledge_warning = _knowledge_refs(db, anchor, knowledge_text)
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
        "package_id": str((project.meta or {}).get("package_id") or "") or None,
        "package": _text(getattr(project, "package", "")),
        "heading": _text(getattr(node, "heading", "")),
        "intent": _text(getattr(node, "intent", "")),
        "target_words": int(getattr(node, "target_words", 0) or 0),
        "needs": [str(item) for item in needs],
        "user_instruction": _text(user_instruction),
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
        "knowledge_warning": knowledge_warning,
        "other_nodes": [
            {"id": row.id, "heading": row.heading or ""} for row in other_nodes[:20]
        ],
    }
    context.update(_project_valid_ids(db, project_id))
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
        if field == "fact_refs":
            valid = valid | set(context["fact_keys"])
        result[field] = _filter_refs(
            result[field], valid, field, errors, context["node_ids"]
        )
    return result


def _filter_refs(
    rows: list[Any],
    valid: set[str],
    label: str,
    errors: list[str],
    node_ids: set[str] | None = None,
) -> list[Any]:
    nodes = valid if node_ids is None else node_ids
    kept: list[Any] = []
    dropped = 0
    for item in rows:
        if isinstance(item, dict):
            ident = _text(item.get("id") or item.get(f"{label}_id"))
            node_id = _text(item.get("node_id"))
            if (ident and ident not in valid) or (node_id and node_id not in nodes):
                dropped += 1
                continue
        elif isinstance(item, str):
            stripped = item.strip()
            if _UUID_RE.fullmatch(stripped) and stripped not in valid:
                dropped += 1
                continue
        kept.append(item)
    if dropped:
        errors.append(
            f"已剔除 {dropped} 条无效的{_ID_LABELS.get(label, label)}引用"
            "（ID 不属于本项目真实数据）"
        )
    return kept


def _add_error(plan: ChapterPlan, message: str) -> None:
    current = _list(plan.validation_errors)
    if message not in current:
        current.append(message)
        plan.validation_errors = current


def validate_chapter_plan(db: Session, plan: ChapterPlan) -> list[str]:
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
    valid = _project_valid_ids(db, project_id)
    for field in ID_REF_FIELDS:
        valid_ids = valid[{
            "score_points": "scoring_ids",
            "requirements": "requirement_ids",
            "fact_refs": "fact_ids",
            "knowledge_refs": "knowledge_ids",
        }[field]]
        if field == "fact_refs":
            valid_ids = valid_ids | valid["fact_keys"]
        repair_errors: list[str] = []
        rows = _filter_refs(
            _list(getattr(plan, field)), valid_ids, field, repair_errors, valid["node_ids"]
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
    context = build_plan_context(db, project_id, node, user_instruction, knowledge_provider)
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
    validate_chapter_plan(db, row)
    return row


def update_chapter_plan(
    db: Session,
    project: Project,
    node: OutlineNode,
    updates: Any,
    created_by: str = "",
    package_id: str | None = None,
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
    validate_chapter_plan(db, row)
    return row


def confirm_chapter_plan(
    db: Session,
    plan: ChapterPlan,
    confirmed_by: str = "",
    note: str = "",
) -> ChapterPlan:
    """校验通过后确认规划；不可修复的错误（如节点越权）直接拒绝。"""

    validate_chapter_plan(db, plan)
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


def plan_context_text(plan: ChapterPlan | None, *, cap: int = _MAX_PLAN_CONTEXT) -> str:
    """把已确认规划转成正文提示词可插入的纯文本。"""

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
    section("知识引用", "knowledge_refs", with_id=True)
    text = "\n".join(lines)
    return text[:cap]
