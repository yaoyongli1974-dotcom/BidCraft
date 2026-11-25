"""覆盖率维度：实质性条款 / 评分点 / 证据 / 章节确认 / 章节规划。

全部只读现有权威来源：Requirement（含 verdict 状态）、ScoringItem、
ChapterPlan（已确认/已用于生成且章节有正文）、Fact（人工确认盖章）、
provenance.report、generation.coverage、export_gate.unconfirmed_chapters。
不修改任何业务数据；零分母一律返回 ``ratio=None`` + ``measured=False``，
不再把「没有样本」伪装成 100% 覆盖。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import ChapterPlan, Fact, OutlineNode, Requirement, ScoringItem, Section

#: ChapterPlan 状态机里「可用于正文生成」的状态（见 generation.chapter_plan.CONFIRMED_STATUSES）。
_PLAN_COVERED_STATUSES = frozenset({"CONFIRMED", "USED_FOR_GENERATION"})
#: material_policy 的降级档位；full 之外的已知档位都算低可信，未知(None)不臆断。
_LOW_TRUST_TIERS = frozenset({"excerpt", "title_only", "suppressed", "low", "low_trust"})
#: 检索引用的可追溯字段；一个都没有才算「缺失溯源」。
_TRACE_FIELDS = ("document_id", "chunk_id", "page", "heading", "source", "trace")


def _ratio(numerator: int, denominator: int) -> float | None:
    """比率；零分母返回 None（调用方须同时输出 measured=False）。"""
    return round(numerator / denominator, 4) if denominator else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _node_rows(db: Session, project_id: str, nodes: Any) -> list[tuple[str, str]]:
    """参与规划的章节：有正文的大纲节点；``nodes`` 给定时按传入集合收窄。"""
    body_by_node = {
        str(section.node_id): section.body
        for section in db.scalars(
            select(Section)
            .join(OutlineNode, OutlineNode.id == Section.node_id)
            .where(OutlineNode.project_id == project_id)
        )
        if _text(section.body)
    }
    if nodes is None:
        rows = db.execute(
            select(OutlineNode.id, OutlineNode.heading)
            .where(OutlineNode.project_id == project_id)
            .order_by(OutlineNode.position)
        ).all()
        candidates = [(str(node_id), str(heading or "")) for node_id, heading in rows]
    else:
        candidates = []
        for item in nodes:
            node_id = (
                getattr(item, "id", None)
                or (item.get("id") if isinstance(item, dict) else None)
                or (item if isinstance(item, str) else None)
            )
            if not node_id:
                continue
            heading = (
                getattr(item, "heading", None)
                or (item.get("heading") if isinstance(item, dict) else None)
                or ""
            )
            if not heading:
                node = db.get(OutlineNode, str(node_id))
                heading = node.heading if node is not None else ""
            candidates.append((str(node_id), str(heading or "")))
    return [(node_id, heading) for node_id, heading in candidates if node_id in body_by_node]


def _plan_records(
    db: Session, project_id: str, nodes: Any = None
) -> list[dict[str, Any]]:
    """逐章解析真实 ChapterPlan：active=已确认/已用于生成，latest=同章最新版本。"""
    plans = list(
        db.scalars(
            select(ChapterPlan)
            .where(ChapterPlan.project_id == project_id)
            .order_by(
                ChapterPlan.version.desc(),
                ChapterPlan.updated_at.desc(),
                ChapterPlan.id.desc(),
            )
        )
    )
    by_node: dict[str, list[ChapterPlan]] = {}
    for plan in plans:
        by_node.setdefault(str(plan.node_id), []).append(plan)
    records: list[dict[str, Any]] = []
    for node_id, heading in _node_rows(db, project_id, nodes):
        node_plans = by_node.get(node_id, [])
        active = next(
            (
                plan
                for plan in node_plans
                if _text(plan.status).upper() in _PLAN_COVERED_STATUSES
            ),
            None,
        )
        records.append(
            {
                "node_id": node_id,
                "heading": heading,
                "plan": active,
                "latest_plan": node_plans[0] if node_plans else None,
            }
        )
    return records


def calculate_requirement_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """实质性条款四态分布：satisfied / unsatisfied / partial / review。

    `response_status` 已人工确认（responded/na）的条款按"已处置"计入覆盖。
    ★ 未确认（pending 且 verdict=review/unsatisfied/partial）单列为硬风险。
    没有任何实质性条款时 ratio=None / measured=False，不再返回 1.0。
    """

    rows = list(
        db.scalars(select(Requirement).where(Requirement.project_id == project_id))
    )
    states: Counter[str] = Counter()
    star_unconfirmed = 0
    substantive_total = 0
    substantive_covered = 0
    for row in rows:
        marker = (row.marker or "").strip()
        is_substantive = bool(marker) or bool(row.mandatory)
        state = (row.response_verdict or "").strip().lower() or "pending"
        # 人工已确认响应/不适用 → 视为覆盖
        if row.response_status in {"responded", "na"}:
            state = "satisfied"
        states[state] += 1
        if is_substantive:
            substantive_total += 1
            if state == "satisfied":
                substantive_covered += 1
            if marker == "★" and row.response_status not in {"responded", "na"} and state != "satisfied":
                star_unconfirmed += 1
    total = len(rows)
    return {
        "total": total,
        "states": dict(states),
        "satisfied": states.get("satisfied", 0),
        "unsatisfied": states.get("unsatisfied", 0),
        "partial": states.get("partial", 0),
        "review": states.get("review", 0),
        "pending": states.get("pending", 0),
        "substantive_total": substantive_total,
        "substantive_covered": substantive_covered,
        "ratio": _ratio(substantive_covered, substantive_total),
        "measured": bool(substantive_total),
        "star_unconfirmed": star_unconfirmed,
    }


def calculate_scoring_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """评分点覆盖：复用 generation.coverage 的只读矩阵口径。"""

    from ..generation.coverage import calculate_score_coverage
    from ..generation.outline import load_outline
    from ..scoring import score_payload

    items = list(
        db.scalars(select(ScoringItem).where(ScoringItem.project_id == project_id))
    )
    scores = [score_payload(item) for item in items]
    nodes = load_outline(db, project_id)
    report = calculate_score_coverage(scores, nodes)
    total_items = int(report.get("total_items", len(items)) or 0)
    return {
        "total_items": total_items,
        "total_score": report.get("total_score", 0.0),
        "covered_score": report.get("covered_score", 0.0),
        "covered_ratio": report.get("covered_ratio", 0.0),
        "uncovered_count": report.get("uncovered_count", 0),
        "measured": bool(total_items),
    }


def calculate_plan_coverage(
    db: Session, project_id: str, nodes: Any = None
) -> dict[str, Any]:
    """章节规划覆盖：有正文的章节是否有已确认/已用于生成的真实 ChapterPlan。

    ``nodes`` 可传 OutlineNode 行、字典或 node_id 列表来收窄分母；默认取项目
    全部有正文的节点。零分母返回 ``ratio=None`` / ``measured=False``。
    """
    records = _plan_records(db, project_id, nodes)
    covered = [item for item in records if item["plan"] is not None]
    unconfirmed: list[dict[str, Any]] = []
    for item in records:
        if item["plan"] is not None:
            continue
        latest = item["latest_plan"]
        unconfirmed.append(
            {
                "node_id": item["node_id"],
                "heading": item["heading"],
                # 未确认时也给出真实的最新规划 id，供 finding 举证；没有规划则为空。
                "plan_id": latest.id if latest is not None else None,
                "status": _text(latest.status).upper() if latest is not None else "NOT_CREATED",
                "version": int(latest.version or 0) if latest is not None else 0,
            }
        )
    total = len(records)
    return {
        "total": total,
        "count": len(covered),
        "confirmed": len(covered),
        "covered": len(covered),
        "unconfirmed": unconfirmed,
        "unconfirmed_count": len(unconfirmed),
        "ratio": _ratio(len(covered), total),
        "measured": bool(total),
        "plans": [
            {
                "node_id": item["node_id"],
                "plan_id": item["plan"].id,
                "status": _text(item["plan"].status).upper(),
                "version": int(item["plan"].version or 0),
            }
            for item in covered
        ],
    }


def _cited_fact_confirmations(
    db: Session, project_id: str, citations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """只从真实 Fact 行读取人工确认盖章；缺行/缺章都记为未确认，不伪造。"""
    keys = sorted({_text(item.get("fact_key")) for item in citations} - {""})
    if not keys:
        return []
    facts = {
        str(row.key): row
        for row in db.scalars(
            select(Fact).where(Fact.project_id == project_id, Fact.key.in_(keys))
        )
    }
    result: list[dict[str, Any]] = []
    for key in keys:
        fact = facts.get(key)
        confirmed = bool(fact is not None and fact.confirmed_by and fact.confirmed_at)
        result.append(
            {
                "fact_key": key,
                "fact_id": fact.id if fact is not None else None,
                "label": _text(fact.label) if fact is not None else key,
                "status": _text(fact.status) if fact is not None else "",
                "confirmed": confirmed,
                "confirmed_by": fact.confirmed_by if fact is not None else None,
                "confirmed_at": (
                    clock.iso_beijing(fact.confirmed_at)
                    if fact is not None and fact.confirmed_at is not None
                    else None
                ),
            }
        )
    return result


def _ref_issues(ref: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if ref.get("degraded") is True or ref.get("downgraded") is True:
        issues.append("degraded")
    tier = _text(ref.get("trust_tier")).lower()
    if tier in _LOW_TRUST_TIERS:
        issues.append("low_trust")
    if not any(_text(ref.get(field)) for field in _TRACE_FIELDS):
        issues.append("missing_trace")
    return issues


def _ref_id(ref: dict[str, Any], index: int) -> str:
    return _text(ref.get("id")) or _text(ref.get("chunk_id")) or _text(
        ref.get("document_id")
    ) or f"index:{index}"


def calculate_evidence_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """证据/来源追溯：复用 provenance.report 的 stale 统计 + 真实规划引用质量。

    degraded / low_trust / missing_trace 比率的分母是已确认 ChapterPlan 里的
    knowledge_refs 条数；事实确认率的分母是正文实际引用的 Fact 键数。零分母
    一律 ``None``，避免把「没数据」当成 100%。
    """

    from ..provenance import report as provenance_report

    data = provenance_report(db, project_id)
    summary = data.get("summary", {}) if isinstance(data, dict) else {}
    citations = list(data.get("citations") or []) if isinstance(data, dict) else []
    stale = int(summary.get("stale", 0) or 0)
    citations_count = int(summary.get("citations", len(citations)) or 0)

    refs: list[tuple[ChapterPlan, dict[str, Any], int]] = []
    for item in _plan_records(db, project_id):
        plan = item["plan"]
        if plan is None:
            continue
        for index, ref in enumerate(plan.knowledge_refs or []):
            if isinstance(ref, dict):
                refs.append((plan, ref, index))
    degraded = sum(1 for _plan, ref, _i in refs if "degraded" in _ref_issues(ref))
    low_trust = sum(1 for _plan, ref, _i in refs if "low_trust" in _ref_issues(ref))
    missing_trace = sum(1 for _plan, ref, _i in refs if "missing_trace" in _ref_issues(ref))

    confirmations = _cited_fact_confirmations(db, project_id, citations)
    confirmed = sum(1 for item in confirmations if item["confirmed"])
    unconfirmed_keys = [item["fact_key"] for item in confirmations if not item["confirmed"]]

    return {
        # 既有键口径不变（仅零分母由 1.0 改为 None）。
        "citations": citations_count,
        "stale": stale,
        "facts_cited": int(summary.get("facts_cited", len(confirmations)) or 0),
        "sections_citing": int(summary.get("sections_citing", 0) or 0),
        "ratio": _ratio(citations_count - stale, citations_count),
        "measured": bool(citations_count),
        # 检索引用质量（全部来自已确认 ChapterPlan.knowledge_refs）。
        "plan_refs": len(refs),
        "degraded_refs": degraded,
        "low_trust_refs": low_trust,
        "missing_trace_refs": missing_trace,
        "degraded_ratio": _ratio(degraded, len(refs)),
        "low_trust_ratio": _ratio(low_trust, len(refs)),
        "missing_trace_ratio": _ratio(missing_trace, len(refs)),
        "ref_measured": bool(refs),
        # 事实确认盖章（来自真实 Fact.confirmed_by / confirmed_at）。
        "facts_confirmed": confirmed,
        "facts_unconfirmed": len(unconfirmed_keys),
        "confirmed_ratio": _ratio(confirmed, len(confirmations)),
        "fact_confirmations": confirmations,
        "unconfirmed_fact_keys": unconfirmed_keys,
    }


def calculate_chapter_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """章节确认覆盖：复用 export_gate.unconfirmed_chapters（空章节不拦）。"""

    from ..export_gate import unconfirmed_chapters

    pending = unconfirmed_chapters(db, project_id)
    total = len(
        list(db.scalars(select(OutlineNode).where(OutlineNode.project_id == project_id)))
    )
    confirmed = max(0, total - len(pending))
    return {
        "total": total,
        "confirmed": confirmed,
        "unconfirmed": len(pending),
        "pending_chapters": [heading for heading, _status in pending],
        "ratio": _ratio(confirmed, total),
        "measured": bool(total),
    }


def collect_coverage_findings(db: Session, project_id: str) -> list[dict[str, Any]]:
    """从真实 ChapterPlan / Fact 生成覆盖类 finding（每条带 stable_key）。"""

    findings: list[dict[str, Any]] = []
    for item in _plan_records(db, project_id):
        node_id, heading = item["node_id"], item["heading"]
        plan = item["plan"]
        if plan is None:
            latest = item["latest_plan"]
            if latest is None:
                # 完全没有规划记录的项目不臆造「未确认规划」问题，也没有 plan_id 可举证。
                continue
            findings.append(
                {
                    "category": "章节规划",
                    "level": "major",
                    "node_id": node_id,
                    "requirement_id": None,
                    "scoring_item_id": None,
                    "title": "章节缺少已确认的正文规划",
                    "description": f"《{heading}》有正文但没有 CONFIRMED/USED_FOR_GENERATION 规划记录",
                    "recommendation": "确认该章规划或重新生成后再用于正文",
                    "evidence": {
                        "node_id": node_id,
                        "heading": heading,
                        "plan_id": latest.id if latest is not None else None,
                        "status": _text(latest.status).upper() if latest is not None else "NOT_CREATED",
                        "stable_key": (
                            f"chapter_plan:{latest.id}"
                            if latest is not None
                            else f"plan_node:{node_id}"
                        ),
                    },
                }
            )
            continue

        grouped: dict[str, dict[str, Any]] = {}
        for index, ref in enumerate(plan.knowledge_refs or []):
            if not isinstance(ref, dict):
                continue
            issues = _ref_issues(ref)
            if not issues:
                continue
            ref_key = _ref_id(ref, index)
            entry = grouped.setdefault(
                ref_key,
                {
                    "issues": set(),
                    "rerank_score": ref.get("rerank_score"),
                    "trust_tier": ref.get("trust_tier"),
                    "degraded": ref.get("degraded"),
                    "document_id": _text(ref.get("document_id")),
                    "chunk_id": _text(ref.get("chunk_id")),
                    "page": ref.get("page"),
                    "heading": _text(ref.get("heading")),
                    "source": _text(ref.get("source")),
                },
            )
            entry["issues"].update(issues)
        for ref_key, entry in grouped.items():
            issues = sorted(entry.pop("issues"))
            labels = {
                "degraded": "降级检索",
                "low_trust": "低可信档位",
                "missing_trace": "缺少溯源字段",
            }
            severe = bool({"degraded", "low_trust"} & set(issues))
            findings.append(
                {
                    "category": "知识引用",
                    "level": "major" if severe else "info",
                    "node_id": node_id,
                    "requirement_id": None,
                    "scoring_item_id": None,
                    "title": f"知识引用质量待复核：{'、'.join(labels[i] for i in issues)}",
                    "description": (
                        f"《{heading}》的规划引用 {ref_key} 存在 "
                        f"{'、'.join(labels[i] for i in issues)}"
                    ),
                    "recommendation": "核对检索出处与可信档位后重新确认该章规划",
                    "evidence": {
                        "node_id": node_id,
                        "heading": heading,
                        "plan_id": plan.id,
                        "ref_id": ref_key,
                        "issues": issues,
                        **entry,
                        "stable_key": f"chapter_plan:{plan.id}:ref:{ref_key}",
                    },
                }
            )

    from ..provenance import report as provenance_report

    citations = provenance_report(db, project_id).get("citations") or []
    by_key: dict[str, set[str]] = {}
    for citation in citations:
        by_key.setdefault(_text(citation.get("fact_key")), set()).add(
            _text(citation.get("node_id"))
        )
    for item in _cited_fact_confirmations(db, project_id, citations):
        if item["confirmed"]:
            continue
        fact_key = item["fact_key"]
        findings.append(
            {
                "category": "来源追溯",
                "level": "major",
                "node_id": None,
                "requirement_id": None,
                "scoring_item_id": None,
                "title": "正文引用的事实尚未人工确认",
                "description": (
                    f"事实 {item['label']}（{fact_key}）被正文引用，"
                    "但没有真实的 confirmed_by/confirmed_at 盖章"
                ),
                "recommendation": "在事实台账中人工确认该取值后重新评估",
                "evidence": {
                    "fact_key": fact_key,
                    "fact_id": item["fact_id"],
                    "label": item["label"],
                    "status": item["status"],
                    "confirmed_by": item["confirmed_by"],
                    "confirmed_at": item["confirmed_at"],
                    "kind": "unconfirmed_cited",
                    "unconfirmed_cited": True,
                    "node_ids": sorted(by_key.get(fact_key, set())),
                    "stable_key": (
                        f"fact:{item['fact_id']}"
                        if item["fact_id"]
                        else f"fact_key:{fact_key}"
                    ),
                },
            }
        )
    return findings
