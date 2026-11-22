"""多维查重统一编排。

一次 ``run_similarity`` 创建 ``SimilarityReport``，按 ``mode`` 执行文本 /
结构 / 语义 / 图片维度（``all`` 跑四维），把每条命中写入 ``SimilarityHit``，
最后更新统计与状态并记审计日志。所有维度都只读章节正文，不修改任何内容。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from ..audit import record_audit
from ..models import Project, SimilarityHit, SimilarityReport, utcnow
from . import image as image_engine
from . import semantic as semantic_engine
from . import structure as structure_engine
from . import text as text_engine

MODES = ("text", "structure", "semantic", "image")
SCOPES = ("project", "history")
MAX_TOTAL_HITS = 1000
REVIEW_STATUSES = ("pending", "normal", "suspicious", "ignored", "confirmed")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def report_payload(report: SimilarityReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "project_id": report.project_id,
        "scope": report.scope,
        "mode": report.mode,
        "threshold": report.threshold,
        "status": report.status,
        "stats": report.stats or {},
        "skipped": report.skipped or [],
        "created_by": report.created_by,
        "created_at": _iso(report.created_at),
        "completed_at": _iso(report.completed_at),
    }


def hit_payload(hit: SimilarityHit) -> dict[str, Any]:
    return {
        "id": hit.id,
        "report_id": hit.report_id,
        "kind": hit.kind,
        "source_a": hit.source_a,
        "source_b": hit.source_b,
        "loc_a": hit.loc_a,
        "loc_b": hit.loc_b,
        "excerpt_a": hit.excerpt_a,
        "excerpt_b": hit.excerpt_b,
        "similarity": hit.similarity,
        "evidence": hit.evidence or {},
        "review_status": hit.review_status,
        "review_note": hit.review_note,
    }


def _validate(mode: str, scope: str, threshold: float) -> None:
    if mode not in (*MODES, "all"):
        raise ValueError("mode 只能是 text/structure/semantic/image/all")
    if scope not in SCOPES:
        raise ValueError("scope 只能是 project/history")
    if not 0.0 <= float(threshold) <= 1.0:
        raise ValueError("threshold 必须在 0~1 之间")


def _run_text(db, project_id: str, source_project_id: str | None, threshold: float) -> dict[str, Any]:
    return text_engine.compare_project_documents(
        db,
        project_id,
        threshold=threshold,
        source_project_id=source_project_id,
    )


def _run_structure(
    db, project_id: str, source_project_id: str | None, threshold: float
) -> dict[str, Any]:
    # 结构相似度用标题语义，包含即 1.0；阈值沿用请求阈值。
    return structure_engine.compare_project_structure(
        db,
        project_id,
        source_project_id=source_project_id,
        threshold=threshold,
    )


def _run_semantic(
    db, project_id: str, source_project_id: str | None, threshold: float
) -> dict[str, Any]:
    project = db.get(Project, project_id)
    paragraphs_a = text_engine.iter_document_paragraphs(
        db, project_id, project_name=project.name if project is not None else ""
    )
    paragraphs_b = None
    skipped: list[dict[str, Any]] = []
    if source_project_id and source_project_id != project_id:
        source = db.get(Project, source_project_id)
        paragraphs_b = text_engine.iter_document_paragraphs(
            db, source_project_id, project_name=source.name if source is not None else ""
        )
        if not paragraphs_b:
            skipped.append(
                {"mode": "semantic", "path": source_project_id, "reason": "来源项目没有可比对正文"}
            )
    if not paragraphs_a:
        skipped.append({"mode": "semantic", "path": project_id, "reason": "项目没有可比对正文"})
    result = semantic_engine.find_semantic_duplicates(
        db,
        paragraphs_a,
        paragraphs_b,
        threshold=threshold,
    )
    result["skipped"] = [*skipped, *result.get("skipped", [])]
    return result


def _run_image(
    db, project_id: str, source_project_id: str | None, threshold: float
) -> dict[str, Any]:
    return image_engine.find_image_duplicates(
        db,
        project_id,
        source_project_id=source_project_id,
        threshold=threshold,
    )


_RUNNERS = {
    "text": _run_text,
    "structure": _run_structure,
    "semantic": _run_semantic,
    "image": _run_image,
}


def run_similarity(
    db,
    project_id: str,
    *,
    scope: str = "project",
    mode: str = "text",
    threshold: float = 0.85,
    source_project_id: str | None = None,
    created_by: str = "",
) -> SimilarityReport:
    """执行一次查重并落库；返回已完成的 ``SimilarityReport``。

    ``mode='all'`` 依次跑四维。单个维度异常/无数据不会让整份报告失败：
    原因写入 ``report.skipped``，其余维度继续。查重过程不修改正文。
    """

    threshold = float(threshold)
    _validate(mode, scope, threshold)
    project = db.get(Project, project_id)
    if project is None:
        raise LookupError(f"项目不存在: {project_id}")
    if source_project_id and source_project_id == project_id:
        source_project_id = None
    if source_project_id and scope == "project":
        # 显式给出 source_project_id 即视为跨项目（history）比对，避免调用方
        # 忘记同时传 scope 导致来源项目被忽略。
        scope = "history"

    base_source = source_project_id if scope == "history" else None
    report = SimilarityReport(
        project_id=project_id,
        scope=scope,
        mode=mode,
        threshold=threshold,
        status="running",
        stats={},
        skipped=[],
        created_by=created_by or "",
    )
    db.add(report)
    db.flush()

    requested = list(MODES) if mode == "all" else [mode]
    hits: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    mode_stats: dict[str, Any] = {}
    degraded = False
    models: list[str] = []

    if scope == "history" and not base_source:
        skipped.append(
            {
                "mode": "all",
                "reason": "未指定 source_project_id，本次仅按项目内查重执行",
            }
        )

    for name in requested:
        runner = _RUNNERS[name]
        try:
            outcome = runner(db, project_id, base_source, threshold)
        except Exception as exc:  # noqa: BLE001 - 单维度失败不拖垮整份报告
            skipped.append({"mode": name, "reason": f"{type(exc).__name__}: {exc}"})
            mode_stats[name] = {"error": str(exc), "hits": 0}
            continue
        outcome_hits = outcome.get("hits") or []
        hits.extend(outcome_hits)
        skipped.extend(outcome.get("skipped") or [])
        stats = dict(outcome.get("stats") or {})
        mode_stats[name] = stats
        if stats.get("degraded"):
            degraded = True
        model = str(stats.get("model") or "")
        if model and model not in models:
            models.append(model)

    hits.sort(key=lambda item: (-float(item.get("similarity") or 0), item.get("kind") or ""))
    truncated = len(hits) > MAX_TOTAL_HITS
    hits = hits[:MAX_TOTAL_HITS]
    by_kind: dict[str, int] = {}
    common_candidates = 0
    for item in hits:
        kind = str(item.get("kind") or "text")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if (item.get("evidence") or {}).get("common"):
            common_candidates += 1

    for item in hits:
        db.add(
            SimilarityHit(
                report_id=report.id,
                kind=str(item.get("kind") or "text")[:20],
                source_a=str(item.get("source_a") or "")[:500],
                source_b=str(item.get("source_b") or "")[:500],
                loc_a=str(item.get("loc_a") or "")[:120],
                loc_b=str(item.get("loc_b") or "")[:120],
                excerpt_a=str(item.get("excerpt_a") or ""),
                excerpt_b=str(item.get("excerpt_b") or ""),
                similarity=float(item.get("similarity") or 0.0),
                evidence=item.get("evidence") or {},
                review_status="pending",
                review_note="",
            )
        )

    report.stats = {
        "mode": mode,
        "scope": scope,
        "threshold": threshold,
        "source_project_id": base_source or "",
        "project": project.name,
        "modes": mode_stats,
        "hits": len(hits),
        "by_kind": by_kind,
        "common_candidates": common_candidates,
        "degraded": degraded,
        "models": models,
        "truncated": truncated,
    }
    report.skipped = skipped
    report.status = "done"
    report.completed_at = utcnow()
    db.flush()

    record_audit(
        db,
        project_id,
        "similarity.check",
        {
            "report_id": report.id,
            "scope": scope,
            "mode": mode,
            "threshold": threshold,
            "hits": len(hits),
            "source_project_id": base_source or "",
        },
    )
    db.flush()
    return report


def list_reports(db, project_id: str, *, limit: int = 50) -> list[SimilarityReport]:
    return list(
        db.scalars(
            select(SimilarityReport)
            .where(SimilarityReport.project_id == project_id)
            .order_by(SimilarityReport.created_at.desc(), SimilarityReport.id.desc())
            .limit(max(1, min(200, limit)))
        ).all()
    )


def get_report(db, project_id: str, report_id: str) -> SimilarityReport:
    report = db.get(SimilarityReport, report_id)
    if report is None or report.project_id != project_id:
        raise LookupError(f"查重报告不存在: {report_id}")
    return report


def get_hits(
    db,
    project_id: str,
    report_id: str,
    *,
    kind: str | None = None,
    review_status: str | None = None,
    limit: int = 1000,
) -> list[SimilarityHit]:
    get_report(db, project_id, report_id)
    statement = select(SimilarityHit).where(SimilarityHit.report_id == report_id)
    if kind:
        statement = statement.where(SimilarityHit.kind == kind)
    if review_status:
        statement = statement.where(SimilarityHit.review_status == review_status)
    statement = statement.order_by(
        SimilarityHit.similarity.desc(), SimilarityHit.kind, SimilarityHit.id
    ).limit(max(1, min(2000, limit)))
    return list(db.scalars(statement).all())


def review_hit(
    db,
    project_id: str,
    hit_id: str,
    *,
    status: str = "normal",
    note: str = "",
) -> SimilarityHit:
    """更新单条命中的复核状态；状态流转不删除任何正文。"""

    if status not in REVIEW_STATUSES:
        raise ValueError(f"status 只能是 {'/'.join(REVIEW_STATUSES)}")
    hit = db.get(SimilarityHit, hit_id)
    if hit is None:
        raise LookupError(f"查重命中不存在: {hit_id}")
    report = db.get(SimilarityReport, hit.report_id)
    if report is None or report.project_id != project_id:
        raise LookupError(f"查重命中不存在: {hit_id}")
    hit.review_status = status
    hit.review_note = (note or "")[:2000]
    db.flush()
    record_audit(
        db,
        project_id,
        "similarity.review",
        {"report_id": report.id, "hit_id": hit.id, "status": status},
    )
    db.flush()
    return hit


def _md_cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def render_report_markdown(report: SimilarityReport, hits: list[SimilarityHit]) -> str:
    """渲染 Markdown 查重报告（版式沿用 ``dedup.render`` 的概览 + 明细表）。"""

    stats = report.stats or {}
    lines = [
        "# 多维查重报告",
        "",
        f"项目：{_md_cell(stats.get('project') or report.project_id)}",
        f"报告：`{report.id}`",
        f"范围：{report.scope}　模式：{report.mode}　阈值：{report.threshold}",
        f"状态：{report.status}　生成时间：{_iso(report.completed_at) or _iso(report.created_at)}",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 命中总数 | {stats.get('hits', len(hits))} |",
        f"| 合理重复候选 | {stats.get('common_candidates', 0)} |",
        f"| 降级运行 | {'是' if stats.get('degraded') else '否'} |",
        f"| 来源项目 | {_md_cell(stats.get('source_project_id') or '—')} |",
        "",
        "## 明细",
        "",
    ]
    if hits:
        current_kind = ""
        for hit in hits:
            if hit.kind != current_kind:
                current_kind = hit.kind
                lines += [
                    "",
                    f"### {current_kind}",
                    "",
                    "| 相似度 | 位置 A | 位置 B | 摘要 | 复核 |",
                    "| --- | --- | --- | --- | --- |",
                ]
            lines.append(
                f"| {hit.similarity:.0%} | {_md_cell(hit.loc_a)} "
                f"| {_md_cell(hit.loc_b)} | {_md_cell(hit.excerpt_a)} "
                f"| {_md_cell(hit.review_status)} |"
            )
    else:
        lines.append("（未发现达到阈值的重复）")
    skipped = report.skipped or []
    lines += ["", "## 跳过/降级说明", ""]
    if skipped:
        for entry in skipped:
            mode = entry.get("mode") or entry.get("path") or ""
            lines.append(f"- {_md_cell(mode)}：{_md_cell(entry.get('reason') or '')}")
    else:
        lines.append("（无）")
    lines += [
        "",
        "> 说明：命中仅为相似度候选；法律法规、国家标准、招标文件原文、合同固定条款、",
        "> 企业统一声明会标记为「合理重复候选」。图片相似不直接认定抄袭，请人工复核。",
    ]
    return "\n".join(lines) + "\n"
