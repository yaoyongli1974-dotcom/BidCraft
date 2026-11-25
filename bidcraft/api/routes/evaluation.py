"""API routes: /evaluation（对标易标第四批 v0.27.0）。

v0.32 Phase 6 在这里补齐报告 Markdown 只读导出与单/跨项目质量摘要；
两者都只暴露真实报告快照，不给没有报告的项目编造分数。
"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from sqlalchemy import select  # noqa: F401

from ...evaluation import (  # noqa: F401
    build_quality_report,
    finding_payload,
    list_reports,
    load_report,
    report_payload,
    update_finding,
)
from ...evaluation.report import finding_for_project  # noqa: F401
from ...models import Job, QualityFinding, QualityReport  # noqa: F401
from ..schemas import QualityFindingUpdate, QualityRunRequest  # noqa: F401
from ..shared import (  # noqa: F401
    BATCH_STALE_SECONDS,
    Any,
    HTTPException,
    Response,
    User,
    _batch_projects,
    _cancel_stale_jobs,
    _project,
    audit,
    billing,
    current_user,
    jobqueue,
    jobs,
    session_scope,
)

router = APIRouter()


def _actor() -> str:
    return str((current_user() or {}).get("username") or "system")


def _md_text(value: Any) -> str:
    """Markdown 文本转义：竖线/换行不破坏表格与列表版式。"""
    return (
        str(value if value is not None else "")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _md_flat(value: Any, prefix: str = "") -> str:
    """把 evidence / coverage 的嵌套结构压成可抽查的 ``键=值`` 单行串。"""
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, (dict, list, tuple)):
                parts.append(_md_flat(item, label))
            else:
                parts.append(f"{label}={_md_text(item)}")
        return "；".join(part for part in parts if part)
    if isinstance(value, (list, tuple)):
        return "；".join(
            _md_flat(item) if isinstance(item, dict) else _md_text(item) for item in value
        )
    return _md_text(value)


def _render_quality_markdown(payload: dict[str, Any]) -> str:
    """从真实报告快照渲染 Markdown（不从项目当前状态重新推断分数）。"""
    summary = payload.get("summary") or {}
    risk = payload.get("risk_summary") or {}
    metrics = summary.get("metrics") or {}
    coverage = summary.get("coverage") or {}
    hard_risks = risk.get("hard_risks") or []

    lines = [
        "# 质量报告",
        "",
        f"报告：`{payload.get('id') or ''}`　版本：{payload.get('report_version') or 0}",
        f"项目：`{payload.get('project_id') or ''}`",
        (
            f"综合分：{payload.get('overall_score')}　等级：{payload.get('grade') or ''}"
            f"　结论：{payload.get('verdict') or ''}"
        ),
        f"生成人：{payload.get('created_by') or '—'}　生成时间：{payload.get('created_at') or '—'}",
        f"报告备注：{_md_text(summary.get('note') or '—')}",
        (
            f"硬性风险：{'是' if risk.get('has_hard_risk') else '否'}"
            f"（{_md_text('、'.join(str(item) for item in hard_risks)) or '无'}）"
        ),
        "",
        "> 综合分为内部质量参考分，不是中标概率，也不等同于实际评标分数。",
        "",
        "## 维度评分",
        "",
        "| 维度 | 得分 | 权重 | 加权得分 |",
        "| --- | --- | --- | --- |",
    ]
    dimensions = payload.get("dimensions") or []
    for row in dimensions:
        lines.append(
            f"| {_md_text(row.get('label') or row.get('key'))} | {_md_text(row.get('score'))} "
            f"| {_md_text(row.get('weight'))} | {_md_text(row.get('weighted'))} |"
        )
    if not dimensions:
        lines.append("（无维度数据）")

    lines += ["", "## 问题明细", ""]
    findings = payload.get("findings") or []
    if not findings:
        lines.append("（未发现质量问题）")
    for index, finding in enumerate(findings, 1):
        anchors = "; ".join(
            f"{name}={finding[name]}"
            for name in ("node_id", "scoring_item_id", "requirement_id")
            if finding.get(name)
        )
        lines += [
            f"### {index}. [{finding.get('level') or ''}] {_md_text(finding.get('title'))}",
            "",
            (
                f"- finding：`{finding.get('id') or ''}`　类别：{_md_text(finding.get('category'))}"
                f"　状态：{_md_text(finding.get('status'))}"
            ),
            f"- 来源锚点：{_md_text(anchors) if anchors else '—'}",
            f"- 描述：{_md_text(finding.get('description') or '—')}",
            f"- 证据：{_md_flat(finding.get('evidence') or {}) or '—'}",
            f"- 建议：{_md_text(finding.get('recommendation') or '—')}",
            "",
        ]

    lines += ["", "## 覆盖 / 指标 / 经验摘要", "", "### 覆盖", ""]
    for key, value in coverage.items():
        lines.append(f"- {_md_text(key)}：{_md_flat(value) or '—'}")
    if not coverage:
        lines.append("（无覆盖率数据）")

    lines += ["", "### 指标", ""]
    for key in ("manual_edit_rate", "issue_density", "boq_match_rate"):
        if key in metrics:
            lines.append(f"- {key}：{_md_text(metrics[key])}")
    for note in metrics.get("notes") or []:
        lines.append(f"  - {_md_text(note)}")
    tender = summary.get("tender_quality") or {}
    if tender:
        lines.append(
            f"- 真实招标文件指标：{_md_text(tender.get('status') or '未测量')}"
        )
    if not metrics and not tender:
        lines.append("（无指标数据）")

    lines += ["", "### 经验与建议", ""]
    lessons = summary.get("lessons")
    if lessons is None:
        lessons = summary.get("recommendations") or []
    if isinstance(lessons, dict):
        lines.append(f"- {_md_flat(lessons) or '—'}")
    elif isinstance(lessons, (list, tuple)):
        for item in lessons:
            lines.append(f"- {_md_text(item)}" if not isinstance(item, dict) else f"- {_md_flat(item)}")
        if not lessons:
            lines.append("（无经验与建议）")
    else:
        lines.append(f"- {_md_text(lessons) or '—'}")
    return "\n".join(lines) + "\n"


@router.post("/projects/{ref}/evaluation/run", status_code=201)
def evaluation_run(ref: str, payload: QualityRunRequest) -> dict[str, Any]:
    """生成一份质量报告快照（六维评分 + 硬性风险一票否决）。"""
    from ..shared import _require_write

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        report = build_quality_report(
            db, project.id, created_by=_actor(), note=payload.note or ""
        )
        audit(db, project.id, "quality.run.http", {"report_id": report.id, "version": report.report_version})
        # 同步运行也要带回本次生成的 finding，和 /evaluation/latest 的口径一致。
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        return report_payload(report, findings=findings)


@router.post("/projects/{ref}/evaluation/run-async", status_code=202)
def evaluation_run_async(ref: str, payload: QualityRunRequest) -> dict[str, Any]:
    """后台生成质量报告；返回 job_id，用 /jobs/{id} 或 SSE 跟踪进度。"""
    from ..shared import _require_write

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        if _cancel_stale_jobs(
            db,
            ["evaluation"],
            BATCH_STALE_SECONDS,
            project_id=project.id,
            stale_message="质量评估任务超时中断，已允许重新提交",
        ) is not None:
            raise HTTPException(status_code=409, detail="已有质量评估任务在运行")
        user = current_user() or {}
        error = jobqueue.check_backpressure(db, user.get("id") or "")
        if error:
            raise HTTPException(status_code=429, detail=error)
        note = payload.note or ""
        job = Job(
            project_id=project.id,
            kind="evaluation",
            status="queued",
            message="质量评估排队中…",
            payload={
                "owner_id": user.get("id") or "",
                "note": note,
                "created_by": _actor(),
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
        owner_row = db.get(User, user.get("id") or "")
        job.priority = billing.queue_priority(db, owner_row) if owner_row else 1
        priority = job.priority
        jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
        audit(db, project.id, "quality.run.async", {"job_id": job_id, "note": note})
    from ..shared import _run_evaluation_job

    jobqueue.submit(
        _run_evaluation_job,
        (job_id, project.id, note, _actor()),
        priority=priority,
        job_id=job_id,
    )
    return {"job_id": job_id}


@router.get("/projects/{ref}/evaluation")
def evaluation_latest(ref: str) -> dict[str, Any]:
    """最新一份质量报告（含 findings）。"""
    with session_scope() as db:
        project, _role = _project(db, ref)
        reports = list_reports(db, project.id, limit=1)
        if not reports:
            return {"report": None}
        report = reports[0]
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        return {"report": report_payload(report, findings=findings)}


@router.get("/projects/{ref}/evaluation/reports")
def evaluation_reports(ref: str, limit: int = 20) -> list[dict[str, Any]]:
    """历史报告列表（版本倒序，快照不可变）。"""
    with session_scope() as db:
        project, _role = _project(db, ref)
        return [report_payload(row, findings=[]) for row in list_reports(db, project.id, limit=limit)]


@router.get("/projects/{ref}/evaluation/reports/{report_id}")
def evaluation_report_detail(ref: str, report_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        report = load_report(db, report_id)
        if report is None or report.project_id != project.id:
            raise LookupError("质量报告不存在")
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        return report_payload(report, findings=findings)


@router.get("/projects/{ref}/evaluation/reports/{report_id}/export")
def evaluation_report_export(ref: str, report_id: str) -> Response:
    """只读导出报告快照 Markdown；跨项目 report_id 一律 404，无 force 旁路。"""
    with session_scope() as db:
        project, _role = _project(db, ref)  # viewer 也可导出（纯 GET）
        report = load_report(db, report_id)
        if report is None or report.project_id != project.id:
            raise LookupError("质量报告不存在")
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        version = report.report_version
        markdown = _render_quality_markdown(report_payload(report, findings=findings))
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="quality-report-{version}.md"'
        },
    )


@router.get("/projects/{ref}/evaluation/summary")
def evaluation_summary(ref: str, refs: str | None = None) -> dict[str, Any]:
    """单项目最新报告摘要；``?refs=a,b`` 聚合当前用户可访问的项目。

    没有真实报告的项目直接跳过，不返回带默认分数的条目（绝不编造评分）。
    """
    with session_scope() as db:
        if refs:
            ids = [item.strip() for item in refs.split(",") if item.strip()]
            projects = _batch_projects(db, ids)
        else:
            projects = [_project(db, ref)[0]]
        items: list[dict[str, Any]] = []
        for project in projects:
            reports = list_reports(db, project.id, limit=1)
            if not reports:
                continue
            report = reports[0]
            items.append(
                {
                    "project_id": report.project_id,
                    "report_id": report.id,
                    "overall_score": report.overall_score,
                    "grade": report.grade,
                    "verdict": report.verdict,
                    "measured": True,
                }
            )
    return {"items": items}


@router.get("/projects/{ref}/evaluation/reports/{report_id}/findings")
def evaluation_findings(ref: str, report_id: str) -> list[dict[str, Any]]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        report = load_report(db, report_id)
        if report is None or report.project_id != project.id:
            raise LookupError("质量报告不存在")
        rows = db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        return [finding_payload(row) for row in rows]


@router.patch("/projects/{ref}/evaluation/findings/{finding_id}")
def evaluation_finding_update(
    ref: str, finding_id: str, payload: QualityFindingUpdate
) -> dict[str, Any]:
    """人工处置质量问题（记录处置人/意见，不改原始报告快照）。"""
    from ..shared import _require_write

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        # IDOR 防护：finding → report → project 归属必须一致；跨项目一律 404，
        # 不暴露其它项目 finding 是否存在。Service 层再用 project_id 兜一次。
        found = finding_for_project(db, finding_id, project.id)
        if found is None:
            raise LookupError("质量问题不存在")
        # 忽略必须有非空处置意见：归属校验通过后、任何写库/审计动作之前拒绝。
        if payload.status == "ignored" and not (payload.note or "").strip():
            raise HTTPException(status_code=400, detail="忽略质量问题必须填写处置意见")
        row = update_finding(
            db,
            finding_id,
            payload.status,
            payload.note or "",
            _actor(),
            project_id=project.id,
        )
        if row is None:
            raise LookupError("质量问题不存在")
        # 审计使用真实归属项目（finding_for_project 已校验），不用 ref 猜测。
        audit(db, found[1].project_id, "quality.finding.http", {"finding_id": finding_id, "status": row.status})
        return finding_payload(row)
