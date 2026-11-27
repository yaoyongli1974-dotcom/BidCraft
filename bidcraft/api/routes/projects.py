"""API routes: /projects (从 app.py 迁移，URL 与响应格式不变)。"""

import hashlib
import logging
import sys
import threading
import uuid

from fastapi import APIRouter
from sqlalchemy import case, or_

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ...evaluation.learning import (  # noqa: E402
    LESSON_POLICY_KEY,
    lessons_block,
    load_lesson_policy,
    normalize_lesson_policy,
    revoke_lesson,
)
from ...generation.chapter_plan import (  # noqa: E402
    invalidate_stale_plan,
    normalize_retrieval,
    retrieval_ref_payload,
    scoped_knowledge_provider,
)
from ...generation.coverage import (  # noqa: E402
    calculate_score_coverage,
    validate_scoring_coverage,
)
from ..shared import (  # noqa: F401
    BATCH_STALE_SECONDS,
    BOQ_UPLOAD_SUFFIXES,
    CONFIRMED_STATUSES,
    ISSUE_BULK_LIMIT,
    MEMBER_ROLES,
    SEVERITY_ORDER,
    TENDER_SOURCE_ROLES,
    TENDER_UPLOAD_SUFFIXES,
    Any,
    AuditLog,
    BatchExportRequest,
    BatchIdsRequest,
    BidPackage,
    BoqAdjustRequest,
    BoqApplyRequest,
    BoqPriceRequest,
    BoqQuotaFillRequest,
    BoqSnapshot,
    BoqVersionCreate,
    BoqVersionReview,
    BoqVersionSubmit,
    ChapterPlan,
    ChapterPlanConfirm,
    ChapterPlanGenerateRequest,
    ChapterPlanUpdate,
    ChapterReview,
    ChapterSave,
    Delivery,
    ExportGateError,
    ExportRequest,
    ExportTemplateUpdate,
    Fact,
    FactUpdate,
    File,
    FileResponse,
    Form,
    GenerateRequest,
    HTTPException,
    ImageAsset,
    ImageBatchRequest,
    ImageCreate,
    ImagePolicyUpdate,
    Issue,
    IssueBulkStatusUpdate,
    IssueStatusUpdate,
    Job,
    JobEvent,
    JSONResponse,
    Material,
    MemberCreate,
    MemberUpdate,
    OutlineNode,
    OutlineRequest,
    OutlineSave,
    PackageCreate,
    PackageUpdate,
    Parameter,
    Path,
    PricePolicyUpdate,
    Project,
    ProjectCreate,
    ProjectDuplicate,
    ProjectInstantiate,
    ProjectMember,
    ProjectUpdate,
    Requirement,
    RequirementResponseUpdate,
    Risk,
    ScoresUpdate,
    ScoringItem,
    Section,
    SourceFile,
    UploadFile,
    User,
    _apply_issue_status,
    _apply_requirement_status,
    _audit_item,
    _batch_projects,
    _bid_package,
    _bid_package_payload,
    _bid_packages,
    _boq_package_ids,
    _cancel_stale_jobs,
    _chapter_payload,
    _clamp_limit,
    _credit_guard,
    _discard_source,
    _generation_client,
    _http_error,
    _image_http_error,
    _iso,
    _issue_node_map,
    _issue_payload,
    _knowledge_provider,
    _member,
    _member_payload,
    _node,
    _outline_payload,
    _page_bounds,
    _pending_chapters,
    _profile_keys,
    _project,
    _project_payload,
    _project_stats,
    _project_stats_batch,
    _report_payload,
    _require_owner,
    _require_write,
    _requirement_payload,
    _resolve_boq_package,
    _risk_overview_payload,
    _run_batch_check_job,
    _run_batch_export_job,
    _run_generation_job,
    _run_image_batch_job,
    _safe_filename,
    _save_upload,
    _source_payload,
    _validate_issue_note,
    _validate_requirement_note,
    add_source,
    audit,
    auth,
    authorize_node,
    billing,
    billing_user,
    boq,
    build_outline,
    build_plan_context,
    build_requirement_context,
    chapter_plan_payload,
    check_project,
    clock,
    confirm_chapter_plan,
    count_words,
    create_project,
    current_user,
    delete,
    duplicate_project,
    enforce_export_gate,
    export_docx,
    export_markdown,
    generate_chapter_plan,
    generate_document,
    images,
    is_pending,
    jobqueue,
    jobs,
    judge_for_project,
    ledger_gate,
    ledger_summary,
    lessons_payload,
    load_active_plan,
    load_ledger,
    load_outline,
    load_package,
    load_plan_history,
    mark_plan_used,
    math,
    parse_documents,
    parse_project,
    persist_parse,
    plan_context_text,
    profile,
    provenance,
    re,
    read_parse_inputs,
    save_outline,
    scoring,
    select,
    session_scope,
    settings,
    shutil,
    tempfile,
    update_chapter_plan,
    update_fact,
    validate_chapter_plan,
)

router = APIRouter()


def _release_generation_lease(project_id: str, token: str) -> None:
    """Release a generation lease after a terminal sync request."""

    if not token:
        return
    try:
        with session_scope() as db:
            jobqueue.release_generation_lease(db, project_id, token)
    except Exception:  # noqa: BLE001 - 释放失败由 reaper 的过期清理兜底
        logging.getLogger(__name__).exception("生成租约释放失败: %s", project_id)


def _planning_mode(project: Project) -> str:
    """项目级章节规划模式：默认 review（确认后才可写正文）。"""

    return str((project.meta or {}).get("chapter_planning_mode") or "review")


def _plan_metadata(plan: ChapterPlan | None) -> dict[str, Any]:
    """规划溯源字段，同步写审计、异步写 Job.payload。"""

    if plan is None:
        return {
            "planning_id": "",
            "planning_version": 0,
            "planning_status": "",
            "knowledge_reference_ids": [],
            "fact_reference_ids": [],
            "scoring_reference_ids": [],
            "generation_strategy": [],
            "prompt_version": "",
        }
    payload = chapter_plan_payload(plan)

    def ids(field: str) -> list[str]:
        return [
            str(row.get("id") or "")
            for row in payload.get(field) or []
            if isinstance(row, dict) and row.get("id")
        ]

    return {
        "planning_id": plan.id,
        "planning_version": int(plan.version or 0),
        "planning_status": plan.status or "",
        "knowledge_reference_ids": ids("knowledge_refs"),
        "fact_reference_ids": ids("fact_refs"),
        "scoring_reference_ids": ids("score_points"),
        "generation_strategy": payload.get("strategy") or [],
        "prompt_version": plan.prompt_version or "",
    }


def _chapter_plan_gate(
    db, project: Project, node: OutlineNode, warnings: list[str]
) -> ChapterPlan | None:
    """正文生成前的章节规划门禁。

    老项目从未生成过规划时按"未启用规划"放行并记 warning；一旦该章节出现过
    规划记录，review/review_strict 模式必须先确认有效规划；review_strict
    对从未规划的新章节也强制阻断；auto 模式放行但记 warning。
    """
    mode = _planning_mode(project)

    plan = load_active_plan(db, project.id, node.id)
    if plan is not None:
        stale = invalidate_stale_plan(db, plan)
        if stale:
            # HTTP 异常会让 session_scope 回滚；先把判废状态提交，避免旧 CONFIRMED
            # 规划在后续请求里又被 load_active_plan 取回。
            db.commit()
            raise _http_error(409, "章节规划依赖已变化，请重新生成并确认：" + "；".join(stale[:3]))
    if plan is not None and plan.status in CONFIRMED_STATUSES:
        return plan
    history = db.scalar(
        select(ChapterPlan.id)
        .where(ChapterPlan.project_id == project.id, ChapterPlan.node_id == node.id)
        .limit(1)
    )
    if mode in {"review", "review_strict"} and (history or mode == "review_strict"):
        raise _http_error(409, "本章章节规划尚未确认，请先生成并确认规划后再生成正文")
    warnings.append(f"章节「{node.heading}」缺少已确认规划（模式：{mode}）")
    return plan


def _enqueue_or_429(db, owner_id: str) -> None:
    """队列背压：全局排队上限与单用户配额，超限 429。"""

    error = jobqueue.check_backpressure(db, owner_id)
    if error:
        raise HTTPException(status_code=429, detail=error)


@router.get("/projects")
def projects(limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        user = current_user() or {}
        if user.get("role") == auth.ROLE_SUPER_ADMIN:
            rows = list(
                db.scalars(
                    select(Project)
                    .order_by(Project.updated_at.desc().nullslast(), Project.id)
                    .limit(capped)
                    .offset(start)
                )
            )
            stats = _project_stats_batch(db, [item.id for item in rows])
            return [_project_payload(item, stats[item.id], "admin") for item in rows]
        user_id = user.get("id")
        memberships = {
            row.project_id: row.role
            for row in db.scalars(
                select(ProjectMember).where(ProjectMember.user_id == user_id)
            )
            # 与 authorize_projects 的成员分支一致：只有有效成员角色才授予可见性，
            # 历史脏数据（如 role='super_admin'）不能在列表接口里造成越权展示。
            if row.role in MEMBER_ROLES
        }
        # 可见性过滤、排序、分页全部下推到 SQL：旧实现把所有可见项目读入
        # Python 再 sort/slice，项目数量增长时内存与延迟线性膨胀。
        visible_filter = [Project.owner_id == user_id]
        if memberships:
            visible_filter.append(Project.id.in_(list(memberships)))
        rows = list(
            db.scalars(
                select(Project)
                .where(or_(*visible_filter))
                .order_by(Project.updated_at.desc().nullslast(), Project.id)
                .limit(capped)
                .offset(start)
            )
        )
        stats = _project_stats_batch(db, [item.id for item in rows])
        return [
            _project_payload(
                item,
                stats[item.id],
                "owner" if item.owner_id == user_id else memberships.get(item.id, "owner"),
            )
            for item in rows
        ]


@router.post("/projects", status_code=201)
def new_project(payload: ProjectCreate) -> dict[str, Any]:
    bid_section = payload.bid_section.strip()
    if len(bid_section) > 60:
        raise HTTPException(status_code=400, detail="标段/包件不超过 60 个字符")
    with session_scope() as db:
        try:
            project = create_project(db, payload.name, payload.package)
            project.owner_id = (current_user() or {}).get("id")
            project.meta = {**(project.meta or {}), "bid_section": bid_section}
            db.flush()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, project.id, "project.create", {"name": project.name})
        _, my_role = _project(db, project.id)
        return _project_payload(project, _project_stats(db, project.id), my_role)


@router.post("/projects/batch-check", status_code=202)
def batch_check(payload: BatchIdsRequest) -> dict[str, Any]:
    """Queue a background check over up to 50 accessible projects."""

    if len(payload.ids) > 50:
        raise HTTPException(status_code=400, detail="一次最多批量核对 50 个项目")
    with session_scope() as db:
        projects = _batch_projects(db, payload.ids)
        if not projects:
            raise HTTPException(status_code=400, detail="没有可操作的项目")
        user = current_user() or {}
        project_ids = [project.id for project in projects]
        if _cancel_stale_jobs(
            db,
            ["batch_check"],
            BATCH_STALE_SECONDS,
            project_id=None,
            stale_message="批量核对任务超时中断，已允许重新提交",
        ) is not None:
            raise HTTPException(status_code=409, detail="已有批量核对任务在运行")
        _enqueue_or_429(db, user.get("id") or "")
        job = Job(
            project_id=None,
            kind="batch_check",
            status="queued",
            message=f"批量核对排队中（{len(project_ids)} 个项目）…",
            payload={
                "owner_id": user.get("id") or "",
                "total": len(project_ids),
                "done": 0,
                "ids": project_ids,
                "results": [],
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
        owner_row = db.get(User, user.get("id") or "")
        job.priority = billing.queue_priority(db, owner_row) if owner_row else 1
        priority = job.priority
        jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
        audit(db, None, "project.batch_check", {"count": len(project_ids)})
    jobqueue.submit(
        _run_batch_check_job,
        (job_id, project_ids),
        priority=priority,
        job_id=job_id,
    )
    return {"job_id": job_id}


@router.post("/projects/batch-export", status_code=202)
def batch_export(payload: BatchExportRequest) -> dict[str, Any]:
    """Queue a background export and return the job whose zip can be downloaded.

    交付硬门禁：存在未处置废标/阻断风险的项目会被跳过（不进入 zip），
    原因见任务结果 ``failed`` 列表中的 ``error``；其余项目照常导出。
    批量后台任务不提供 force 通道（fail-closed）。
    """

    fmt = (payload.format or "docx").strip().lower()
    if fmt not in {"docx", "md", "xlsx"}:
        raise HTTPException(status_code=400, detail="format 只能是 docx/md/xlsx")
    if len(payload.ids) > 50:
        raise HTTPException(status_code=400, detail="一次最多批量导出 50 个项目")
    with session_scope() as db:
        projects = _batch_projects(db, payload.ids)
        if not projects:
            raise HTTPException(status_code=400, detail="没有可操作的项目")
        user = current_user() or {}
        project_ids = [project.id for project in projects]
        if _cancel_stale_jobs(
            db,
            ["batch_export"],
            BATCH_STALE_SECONDS,
            project_id=None,
            stale_message="批量导出任务超时中断，已允许重新提交",
        ) is not None:
            raise HTTPException(status_code=409, detail="已有批量导出任务在运行")
        _enqueue_or_429(db, user.get("id") or "")
        job = Job(
            project_id=None,
            kind="batch_export",
            status="queued",
            message=f"批量导出排队中（{len(project_ids)} 个项目）…",
            payload={
                "owner_id": user.get("id") or "",
                "total": len(project_ids),
                "done": 0,
                "format": fmt,
                "ids": project_ids,
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
        owner_row = db.get(User, user.get("id") or "")
        job.priority = billing.queue_priority(db, owner_row) if owner_row else 1
        priority = job.priority
        jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
        audit(db, None, "project.batch_export", {"count": len(project_ids), "format": fmt})
    jobqueue.submit(
        _run_batch_export_job,
        (job_id, project_ids, fmt),
        priority=priority,
        job_id=job_id,
    )
    return {"job_id": job_id}


@router.get("/projects/templates")
def project_templates() -> list[dict[str, Any]]:
    """模板列表：登录即可浏览（含 viewer），仅模板、updated_at 降序上限 200。"""

    with session_scope() as db:
        rows = list(
            db.scalars(
                select(Project)
                .where(Project.is_template.is_(True))
                .order_by(Project.updated_at.desc().nullslast(), Project.id)
                .limit(200)
            )
        )
        owner_ids = {item.owner_id for item in rows if item.owner_id}
        owners = (
            {
                user.id: user
                for user in db.scalars(select(User).where(User.id.in_(list(owner_ids))))
            }
            if owner_ids
            else {}
        )
        items: list[dict[str, Any]] = []
        for item in rows:
            owner = owners.get(item.owner_id) if item.owner_id else None
            items.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "package": item.package,
                    "is_template": True,
                    "updated_at": _iso(item.updated_at),
                    "owner_name": (owner.display_name or owner.username)
                    if owner is not None
                    else None,
                }
            )
        return items


@router.post("/projects/templates/{template_id}/instantiate", status_code=201)
def instantiate_template(
    template_id: str, payload: ProjectInstantiate
) -> dict[str, Any]:
    """R23 从模板创建：登录即可（无需成员关系），创建者成为新项目 owner。

    复用 R22 深拷贝：台账/大纲/正文始终复制，BOQ 与配图可选；报价版本、
    问题、任务、成员授权、审计不复制。新项目不是模板。失败时清理新目录。
    """

    with session_scope() as db:
        template = db.get(Project, template_id)
        if template is None or not template.is_template:
            raise LookupError("模板不存在")
        try:
            new = duplicate_project(
                db,
                template,
                name=payload.name,
                owner_id=(current_user() or {}).get("id"),
                include_boq=payload.include_boq,
                include_images=payload.include_images,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            new_id = getattr(exc, "duplicate_project_id", "")
            if new_id:
                shutil.rmtree(settings.home / "projects" / new_id, ignore_errors=True)
            raise
        audit(
            db,
            new.id,
            "project.instantiate",
            {
                "template_id": template.id,
                "name": new.name,
                "include_boq": payload.include_boq,
                "include_images": payload.include_images,
            },
        )
        return _project_payload(new, _project_stats(db, new.id), "owner")


@router.get("/projects/{ref}")
def project_detail(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, my_role = _project(db, ref)
        ledger = load_ledger(db, project.id)
        sources = list(
            db.scalars(
                select(SourceFile)
                .where(
                    SourceFile.project_id == project.id,
                    SourceFile.role.in_(TENDER_SOURCE_ROLES),
                )
                .order_by(SourceFile.created_at)
            )
        )
        return {
            **_project_payload(project, _project_stats(db, project.id), my_role),
            "ledger_summary": ledger_summary(ledger),
            "missing_required": [item.key for item in ledger.missing_required()],
            "outline": len(load_outline(db, project.id)),
            "checks_total": len(load_package(project.package).checks),
            "sources": [_source_payload(source) for source in sources],
        }


@router.patch("/projects/{ref}")
def update_project(ref: str, payload: ProjectUpdate) -> dict[str, Any]:
    """Rename a project, set its bid section (标段/包件), approval chain or template flag.

    ``approval_chain`` 双格式（R19，仅 owner/admin）：① R18 扁平用户 id
    列表 → 每 id 一个单审批人 all 阶段；② v2 阶段列表
    [{approvers: [用户id], mode?: "all"|"any", min_amount?: 数值,
    packages?: [包件id], currencies?: [币种]}]。校验：阶段 ≤5、级内 1–5 人
    去重保序、users 存在性、mode ∈ all|any（默认 all）、可选 min_amount 为
    0–1e12 的数值（R21 金额门槛，布尔/字符串拒绝）、可选 packages /
    currencies 为 ≤20 项、每项非空且 ≤40 字符的字符串列表（R25 条件，
    去重保序，非法→400「条件无效」）。写入 meta 时统一存 v2 阶段快照
    （name=display_name||username，合法条件原样保留），此后用户改名不追更；
    空列表清空审批链，回到单级负责人审批。v0.33.0 RC-08 新增
    ``lesson_policy`` 部分字段 PATCH（仅 owner/admin）：写入 project.meta
    （无 schema 迁移），回显经 learning 归一化的策略快照。
    """

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    with session_scope() as db:
        project, my_role = _project(db, ref)
        _require_write(my_role)
        keys: list[str] = []
        lesson_audit: dict[str, Any] = {}
        meta = dict(project.meta or {})
        if "name" in updates:
            name = str(updates["name"] or "").strip()
            if not name or len(name) > 80:
                raise HTTPException(status_code=400, detail="项目名称需为 1–80 个字符")
            project.name = name
            keys.append("name")
        if "bid_section" in updates:
            section = str(updates["bid_section"] or "").strip()
            if len(section) > 60:
                raise HTTPException(status_code=400, detail="标段/包件不超过 60 个字符")
            meta["bid_section"] = section
            project.meta = meta
            keys.append("bid_section")
        if "approval_chain" in updates:
            _require_owner(my_role)
            raw_chain = updates["approval_chain"] or []
            # R19 双格式：v2 阶段列表原样解析；R18 扁平用户 id 列表先整链
            # 去重保序，再每 id 一个单审批人 all 阶段（语义同 R18）。
            if raw_chain and isinstance(raw_chain[0], dict):
                raw_stages: list[Any] = list(raw_chain)
            else:
                flat_seen: set[str] = set()
                flat_ordered: list[str] = []
                for raw_id in raw_chain:
                    user_id = str(raw_id or "").strip()
                    if user_id and user_id not in flat_seen:
                        flat_seen.add(user_id)
                        flat_ordered.append(user_id)
                raw_stages = [{"approvers": [uid]} for uid in flat_ordered]
            if len(raw_stages) > 5:
                raise HTTPException(status_code=400, detail="审批链最多 5 级")
            chain: list[dict[str, Any]] = []
            for raw_stage in raw_stages:
                if not isinstance(raw_stage, dict):
                    raise HTTPException(
                        status_code=400, detail="审批阶段需为 {approvers, mode} 对象"
                    )
                mode = raw_stage.get("mode", "all")
                if mode not in ("all", "any"):
                    raise HTTPException(
                        status_code=400, detail="审批模式只能是 all（会签）或 any（或签）"
                    )
                if "min_amount" in raw_stage:
                    threshold = raw_stage["min_amount"]
                    if (
                        isinstance(threshold, bool)
                        or not isinstance(threshold, (int, float))
                        or not math.isfinite(threshold)
                        or threshold < 0
                        or threshold > 1e12
                    ):
                        raise HTTPException(status_code=400, detail="金额门槛无效")
                conditions: dict[str, list[str]] = {}
                for condition_key in ("packages", "currencies"):
                    if condition_key not in raw_stage:
                        continue
                    raw_values = raw_stage[condition_key]
                    if (
                        not isinstance(raw_values, list)
                        or len(raw_values) > boq.STAGE_CONDITION_MAX_ITEMS
                    ):
                        raise HTTPException(status_code=400, detail="条件无效")
                    seen_values: set[str] = set()
                    ordered_values: list[str] = []
                    for raw_value in raw_values:
                        if not isinstance(raw_value, str):
                            raise HTTPException(status_code=400, detail="条件无效")
                        text = raw_value.strip()
                        if (
                            not text
                            or len(text) > boq.STAGE_CONDITION_ITEM_MAX
                        ):
                            raise HTTPException(status_code=400, detail="条件无效")
                        if text not in seen_values:
                            seen_values.add(text)
                            ordered_values.append(text)
                    conditions[condition_key] = ordered_values
                raw_approvers = raw_stage.get("approvers")
                if not isinstance(raw_approvers, list):
                    raise HTTPException(
                        status_code=400, detail="每级审批人需为 1–5 个用户 id"
                    )
                seen: set[str] = set()
                ordered: list[str] = []
                for raw_id in raw_approvers:
                    user_id = str(raw_id or "").strip()
                    if user_id and user_id not in seen:
                        seen.add(user_id)
                        ordered.append(user_id)
                if not ordered or len(ordered) > 5:
                    raise HTTPException(status_code=400, detail="每级审批人需为 1–5 个")
                approvers: list[dict[str, str]] = []
                for user_id in ordered:
                    approver = db.get(User, user_id)
                    if approver is None:
                        raise HTTPException(
                            status_code=400, detail=f"审批人不存在: {user_id}"
                        )
                    approvers.append(
                        {
                            "id": user_id,
                            "name": approver.display_name or approver.username,
                        }
                    )
                stage_snapshot: dict[str, Any] = {"approvers": approvers, "mode": mode}
                if "min_amount" in raw_stage:
                    stage_snapshot["min_amount"] = raw_stage["min_amount"]
                for condition_key, condition_values in conditions.items():
                    stage_snapshot[condition_key] = condition_values
                stage_rules = boq._stage_rules(raw_stage.get("rules"))
                if stage_rules:
                    stage_snapshot["rules"] = stage_rules
                chain.append(stage_snapshot)
            meta["approval_chain"] = chain
            project.meta = meta
            keys.append("approval_chain")
        if "lesson_policy" in updates:
            _require_owner(my_role)
            # 部分字段 PATCH：未给出的键沿用旧策略（尤其是 revoked_ids），
            # 白名单校验与裁剪统一交给 learning.normalize_lesson_policy。
            old_policy = dict(load_lesson_policy(project))
            merged = dict(old_policy)
            for key, value in (updates["lesson_policy"] or {}).items():
                if value is not None:
                    merged[key] = value
            new_policy = normalize_lesson_policy(merged)
            meta[LESSON_POLICY_KEY] = new_policy
            lesson_audit = {
                "revoked_added": sorted(
                    set(new_policy["revoked_ids"]) - set(old_policy["revoked_ids"])
                ),
                "revoked_removed": sorted(
                    set(old_policy["revoked_ids"]) - set(new_policy["revoked_ids"])
                ),
                "changed": {
                    key: {"from": old_policy.get(key), "to": new_policy.get(key)}
                    for key in ("grades", "trust_levels", "min_trust", "ttl_days")
                    if old_policy.get(key) != new_policy.get(key)
                },
            }
            project.meta = meta
            keys.append("lesson_policy")
        if "is_template" in updates:
            _require_owner(my_role)
            project.is_template = bool(updates["is_template"])
            keys.append("is_template")
        audit(
            db,
            project.id,
            "project.update",
            {"keys": keys, **({"lesson_policy": lesson_audit} if lesson_audit else {})},
        )
        return _project_payload(project, _project_stats(db, project.id), my_role)


@router.get("/projects/{ref}/profile")
def get_project_profile(ref: str) -> dict[str, Any]:
    """Return the project's global facts and their canonical anchors."""

    with session_scope() as db:
        project, _role = _project(db, ref)
        stored = profile.load(project)
        return {
            "profile": stored,
            "anchors": profile.anchors(stored),
            "facts_block": profile.facts_block(stored),
        }


@router.get("/projects/{ref}/scores")
def project_scores(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return scoring.analysis(db, project)


@router.get("/projects/{ref}/scoring-coverage")
def project_scoring_coverage(ref: str) -> dict[str, Any]:
    """评分覆盖矩阵（只读）：每个评分点是否映射到带正文的章节。

    不修改大纲、不写库、不触发任务；viewer 可读。判定口径保守——匹配不到或
    正文为空视为未覆盖，不做语义脑补。
    """

    with session_scope() as db:
        project, _role = _project(db, ref)
        scores = [scoring.score_payload(item) for item in scoring.list_scores(db, project)]
        nodes = load_outline(db, project.id)
        report = calculate_score_coverage(scores, nodes)
        report["project_id"] = project.id
        report["issues"] = validate_scoring_coverage(report)
        return report


@router.put("/projects/{ref}/scores")
def project_scores_update(ref: str, payload: ScoresUpdate) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        try:
            updated = scoring.record_scores(
                db, project, [row.model_dump() for row in payload.rows]
            )
        except scoring.ScoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.flush()
        audit(db, project.id, "project.scores", {"rows": updated})
        return scoring.analysis(db, project)


@router.put("/projects/{ref}/profile")
def save_project_profile(ref: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the project's global facts (used by generation and checks)."""

    with session_scope() as db:
        project, my_role = _project(db, ref)
        _require_write(my_role)
        try:
            stored = profile.save(project, payload.get("profile", payload))
        except profile.ProfileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, project.id, "project.profile", {"keys": _profile_keys(stored)})
        return {
            "profile": stored,
            "anchors": profile.anchors(stored),
            "facts_block": profile.facts_block(stored),
        }


@router.delete("/projects/{ref}")
def delete_project(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_owner(_role)
        project_id = project.id
        project_name = project.name
        # 显式清理全部关联行（含此前漏掉的报价版本/包件/引用），口径集中在
        # services.purge，避免各入口各写一份、再次漏表。审计 user_id 留存不受影响。
        from ...services.purge import delete_project_rows

        removed = delete_project_rows(db, project_id)
        project_dir = settings.project_dir(project.id)
        db.delete(project)
        db.flush()
        audit(
            db,
            None,
            "project.delete",
            {"name": project_name, "project_id": project_id, "removed": removed},
        )
    shutil.rmtree(project_dir, ignore_errors=True)
    return {"deleted": True, "id": project_id}


@router.post("/projects/{ref}/duplicate", status_code=201)
def duplicate_project_route(ref: str, payload: ProjectDuplicate) -> dict[str, Any]:
    """R22 项目复制（模板复用）：台账/大纲/正文始终复制，BOQ 与配图可选。

    报价版本与审批记录、核对问题、任务、交付、成员授权、审计日志不复制；
    复制者成为新项目 owner。复制失败时清理已创建的新项目目录。
    """

    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            new = duplicate_project(
                db,
                project,
                name=payload.name,
                owner_id=(current_user() or {}).get("id"),
                include_boq=payload.include_boq,
                include_images=payload.include_images,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            new_id = getattr(exc, "duplicate_project_id", "")
            if new_id:
                shutil.rmtree(settings.home / "projects" / new_id, ignore_errors=True)
            raise
        audit(
            db,
            new.id,
            "project.duplicate",
            {
                "source_id": project.id,
                "name": new.name,
                "include_boq": payload.include_boq,
                "include_images": payload.include_images,
            },
        )
        _, my_role = _project(db, new.id)
        return _project_payload(new, _project_stats(db, new.id), my_role)


@router.get("/projects/{ref}/members")
def project_members(
    ref: str, limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        project, _role = _project(db, ref)
        items: list[dict[str, Any]] = []
        owner = db.get(User, project.owner_id) if project.owner_id else None
        if owner is not None:
            items.append(
                {
                    "user_id": owner.id,
                    "username": owner.username,
                    "display_name": owner.display_name or owner.username,
                    "role": "owner",
                    "created_at": _iso(project.created_at),
                }
            )
        rows = db.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project.id)
            .order_by(ProjectMember.created_at)
        ).all()
        items.extend(_member_payload(member, user) for member, user in rows)
        return items[start : start + capped]


@router.post("/projects/{ref}/members", status_code=201)
def add_project_member(ref: str, payload: MemberCreate) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_owner(_role)
        if payload.role not in MEMBER_ROLES:
            raise HTTPException(status_code=400, detail="role 只能是 editor 或 viewer")
        user = db.scalar(select(User).where(User.username == payload.username.strip()))
        if user is None:
            raise LookupError("用户不存在")
        if project.owner_id == user.id:
            raise HTTPException(status_code=409, detail="该用户已是项目负责人")
        existing = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="该用户已是项目成员")
        member = ProjectMember(
            project_id=project.id, user_id=user.id, role=payload.role
        )
        db.add(member)
        db.flush()
        audit(
            db,
            project.id,
            "member.add",
            {"username": user.username, "role": payload.role},
        )
        return _member_payload(member, user)


@router.patch("/projects/{ref}/members/{user_id}")
def update_project_member(
    ref: str, user_id: str, payload: MemberUpdate
) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_owner(_role)
        if payload.role not in MEMBER_ROLES:
            raise HTTPException(status_code=400, detail="role 只能是 editor 或 viewer")
        if project.owner_id == user_id:
            raise HTTPException(status_code=400, detail="项目负责人角色不可修改")
        member = _member(db, project.id, user_id)
        member.role = payload.role
        db.flush()
        user = db.get(User, user_id)
        audit(
            db,
            project.id,
            "member.update",
            {"username": user.username if user is not None else "", "role": payload.role},
        )
        return _member_payload(member, user)


@router.delete("/projects/{ref}/members/{user_id}")
def remove_project_member(ref: str, user_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_owner(_role)
        if project.owner_id == user_id:
            raise HTTPException(status_code=400, detail="项目负责人不可移除")
        member = _member(db, project.id, user_id)
        user = db.get(User, user_id)
        username = user.username if user is not None else ""
        db.delete(member)
        db.flush()
        audit(db, project.id, "member.remove", {"username": username})
        return {"deleted": True}


@router.get("/projects/{ref}/audit")
def project_audit(
    ref: str, limit: int = 50, before: int | None = None
) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        statement = (
            select(AuditLog)
            .where(AuditLog.project_id == project.id)
            .order_by(AuditLog.id.desc())
            .limit(_clamp_limit(limit))
        )
        if before is not None:
            statement = statement.where(AuditLog.id < before)
        rows = list(db.scalars(statement))
        return {
            "items": [_audit_item(row) for row in rows],
            "next_before": rows[-1].id if len(rows) == _clamp_limit(limit) else None,
        }


@router.get("/projects/{ref}/images")
def project_images(
    ref: str, node_id: str = "", limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        project, _role = _project(db, ref)
        assets = images.list_assets(db, project.id, node_id=node_id or None)
        return [
            images.asset_payload(asset) for asset in assets[start : start + capped]
        ]


@router.post("/projects/{ref}/images/generate-missing", status_code=202)
def generate_missing_images(
    ref: str, payload: ImageBatchRequest | None = None
) -> dict[str, Any]:
    include_failed = bool(payload.include_failed) if payload else False
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        plan = images.image_plan(db, project)
        targets = images.image_batch_targets(plan, include_failed=include_failed)
        if not targets:
            raise HTTPException(status_code=409, detail="没有需要生成的配图")
        if _cancel_stale_jobs(
            db,
            ["images"],
            2 * 3600,
            project_id=project.id,
            stale_message="配图任务超时中断，已允许重新生成",
        ) is not None:
            raise HTTPException(status_code=409, detail="该项目已有配图任务在运行")
        _enqueue_or_429(db, (current_user() or {}).get("id") or "")
        job = Job(
            project_id=project.id,
            kind="images",
            status="queued",
            message=f"配图任务排队中（{len(targets)} 个图名）…",
            payload={
                # 归一化归属人：背压检查按索引列 jobs.owner_id 计数。
                "owner_id": (current_user() or {}).get("id") or project.owner_id or "",
                "total": len(targets),
                "done": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
                "include_failed": include_failed,
                "labels": [target["label"] for target in targets],
                # DB 驱动派发：worker 从 payload 重建 targets（含 node_id/asset_id 等）。
                "targets": targets,
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
        owner_row = db.get(User, project.owner_id or "")
        job.priority = billing.queue_priority(db, owner_row) if owner_row else 1
        priority = job.priority
        jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
        audit(db, project.id, "image.batch", {"count": len(targets)})
    jobqueue.submit(
        _run_image_batch_job,
        (job_id, project.id, targets),
        priority=priority,
        job_id=job_id,
    )
    return {"job_id": job_id}


@router.post("/projects/{ref}/images", status_code=201)
def create_image(ref: str, payload: ImageCreate) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        label = payload.label.strip()
        if not label:
            raise HTTPException(status_code=400, detail="图片描述不能为空")
        try:
            asset = images.generate_image(
                db,
                project,
                label=label,
                node_id=payload.node_id,
                heading=payload.heading,
                prompt=payload.prompt,
                style=payload.style,
                size=payload.size,
            )
        except images.ImageError as exc:
            raise _image_http_error(exc) from exc
        audit(db, project.id, "image.generate", {"label": label})
        return images.asset_payload(asset)


@router.post("/projects/{ref}/images/{image_id}/regenerate", status_code=201)
def regenerate_image(ref: str, image_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        source = images.get_asset(db, project.id, image_id)
        if source is None:
            raise LookupError(f"图片不存在：{image_id}")
        try:
            asset = images.regenerate_image(db, project, source)
        except images.ImageError as exc:
            raise _image_http_error(exc) from exc
        audit(db, project.id, "image.regenerate", {"id": image_id})
        return images.asset_payload(asset)


@router.post("/projects/{ref}/images/upload", status_code=201)
async def upload_image(
    ref: str,
    file: UploadFile = File(...),
    label: str = Form(""),
    node_id: str = Form(""),
) -> dict[str, Any]:
    content = await file.read(images.MAX_UPLOAD_BYTES + 1)
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            asset = images.upload_image(
                db,
                project,
                label=label,
                filename=_safe_filename(file.filename, "image.png"),
                content=content,
                node_id=node_id,
            )
        except images.ImageError as exc:
            raise _image_http_error(exc) from exc
        audit(db, project.id, "image.upload", {"filename": file.filename or ""})
        return images.asset_payload(asset)


@router.get("/projects/{ref}/image-policy")
def get_image_policy(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return images.get_policy(project)


@router.put("/projects/{ref}/image-policy")
def update_image_policy(
    ref: str, payload: ImagePolicyUpdate | None = None
) -> dict[str, Any]:
    body = payload.model_dump(exclude_unset=True) if payload is not None else {}
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        policy = images.set_policy(db, project, body)
        keys = [key for key in images.POLICY_KEYS if key in body]
        audit(
            db,
            project.id,
            "image.policy",
            {"keys": keys, "values": {key: policy[key] for key in keys}},
        )
        return policy


@router.get("/projects/{ref}/images/plan")
def image_plan(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return images.image_plan(db, project)


@router.post("/projects/{ref}/images/apply-plan")
def apply_image_plan(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        plan = images.image_plan(db, project)
        if not plan["chapters"]:
            raise HTTPException(status_code=409, detail="没有可插入的配图占位")
        if not plan["summary"]["ready"]:
            raise HTTPException(status_code=409, detail="没有已生成的配图可插入")
        result = images.apply_image_plan(db, project)
        audit(db, project.id, "image.apply_plan", {"count": len(plan["chapters"])})
        return result


@router.get("/projects/{ref}/images/{image_or_file}")
def image_detail_or_file(ref: str, image_or_file: str):
    with session_scope() as db:
        project, _role = _project(db, ref)
        suffix = Path(image_or_file).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg"}:
            path = images.resolve_image_path(project.id, image_or_file)
            if path is None:
                raise LookupError(f"图片不存在：{image_or_file}")
            media = images.media_type_for(image_or_file)
            return FileResponse(path, media_type=media, filename=image_or_file)
        asset = images.get_asset(db, project.id, image_or_file)
        if asset is None:
            raise LookupError(f"图片不存在：{image_or_file}")
        return images.asset_payload(asset)


@router.delete("/projects/{ref}/images/{image_id}")
def delete_image(ref: str, image_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        if not images.delete_asset(db, project.id, image_id):
            raise LookupError(f"图片不存在：{image_id}")
        audit(db, project.id, "image.delete", {"id": image_id})
        return {"deleted": True, "id": image_id}


@router.get("/projects/{ref}/sources")
def sources(
    ref: str, limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        project, _role = _project(db, ref)
        rows = list(
            db.scalars(
                select(SourceFile)
                .where(
                    SourceFile.project_id == project.id,
                    SourceFile.role.in_(TENDER_SOURCE_ROLES),
                )
                .order_by(SourceFile.created_at)
            )
        )
        return [_source_payload(source) for source in rows[start : start + capped]]


@router.post("/projects/{ref}/sources")
async def upload(ref: str, file: UploadFile = File(...)) -> dict[str, Any]:
    raw_name = (file.filename or "").strip()
    if raw_name and raw_name not in {"/", "\\"}:
        suffix = Path(raw_name).suffix.lower()
        if suffix not in TENDER_UPLOAD_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="招标文件仅支持 PDF/DOCX/TXT/MD/XLSX/XLSM 格式",
            )
    filename = _safe_filename(raw_name, "upload.bin")
    with tempfile.TemporaryDirectory(prefix="bidcraft-api-") as tmp:
        target = Path(tmp) / filename
        await _save_upload(
            file,
            target,
            max_bytes=_core.MAX_TENDER_UPLOAD_BYTES,
            detail="招标文件不能超过 50MB",
        )
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            source = add_source(db, project, target)
            try:
                ledger, info = parse_project(db, project.id)
            except Exception as exc:
                _discard_source(db, source)
                if isinstance(exc, ValueError):
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                raise
            if info.get("parse_warnings"):
                audit(
                    db,
                    project.id,
                    "source.parse_truncated",
                    {"warnings": info["parse_warnings"]},
                )
            audit(db, project.id, "source.upload", {"filename": filename})
            return {
                "project": _project_payload(project, _project_stats(db, project.id)),
                "ledger_summary": ledger_summary(ledger),
                "notes": info["notes"],
                "missing_required": [item.key for item in ledger.missing_required()],
            }


@router.get("/projects/{ref}/sources/{source_id}/file")
def source_file(ref: str, source_id: str) -> FileResponse:
    with session_scope() as db:
        project, _role = _project(db, ref)
        source = db.get(SourceFile, source_id)
        if source is None or source.project_id != project.id:
            raise LookupError(f"文件不存在: {source_id}")
        root = (settings.project_dir(project.id) / "sources").resolve()
        path = Path(source.stored_path).resolve()
        if root not in path.parents or not path.is_file():
            raise LookupError(f"原件不存在: {source.filename}")
        return FileResponse(path, filename=source.filename)


@router.post("/projects/{ref}/parse")
def reparse(ref: str) -> dict[str, Any]:
    # 重解析会做 OCR/文档抽取（秒级到分钟级），拆成"短读事务 → 纯解析 →
    # 短写事务"三段，避免整段时间占用数据库写连接；响应结构与原来一致。
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        project_id = project.id
        try:
            package_name, source_specs = read_parse_inputs(db, project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ledger, info, updates = parse_documents(package_name, source_specs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with session_scope() as db:
        persist_parse(db, project_id, ledger, updates)
        project = db.get(Project, project_id)
        audit(db, project.id, "parse.run", {"notes": len(info["notes"])})
        if info.get("parse_warnings"):
            audit(
                db,
                project.id,
                "parse.truncated",
                {"warnings": info["parse_warnings"]},
            )
        return {
            "project": _project_payload(project, _project_stats(db, project.id)),
            "ledger_summary": ledger_summary(ledger),
            "notes": info["notes"],
            "missing_required": [item.key for item in ledger.missing_required()],
        }


@router.get("/projects/{ref}/ledger")
def ledger(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        data = load_ledger(db, project.id)
        return {**data.model_dump(), "summary": ledger_summary(data)}


@router.get("/projects/{ref}/lessons")
def project_lessons(
    ref: str,
    node_id: str | None = None,
    limit: int = 20,
    include_revoked: bool = False,
) -> dict[str, Any]:
    """只读经验摘要：viewer 也能看；查询动作本身记 learning.read 审计。

    ``include_revoked=true`` 仅把已撤销条目带回治理视图（仍过滤过期与低信任），
    不改变“未审核建议”的展示口径。
    """

    cap = _clamp_limit(limit)
    with session_scope() as db:
        project, _role = _project(db, ref)
        # 默认调用保持原有签名，兼容 monkeypatch 本名字的契约测试。
        if include_revoked:
            data = lessons_payload(
                db, project, node_id=node_id or None, include_revoked=True
            )
        else:
            data = lessons_payload(db, project, node_id=node_id or None)
        items = list(data.get("items") or [])[:cap]
        audit(
            db,
            project.id,
            "learning.read",
            {
                "node_id": node_id or "",
                "limit": cap,
                "count": len(items),
                "include_revoked": bool(include_revoked),
            },
        )
        return {**data, "items": items, "limit": cap}


@router.patch("/projects/{ref}/lessons/{lesson_id}")
def revoke_project_lesson(ref: str, lesson_id: str) -> dict[str, Any]:
    """owner-only：撤销一条建议经验；只写 project.meta，不改任何来源行。"""

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_owner(role)
        try:
            result = revoke_lesson(db, project, lesson_id)
        except LookupError as exc:
            raise _http_error(404, str(exc)) from exc
        audit(db, project.id, "learning.revoke", {"lesson_id": lesson_id})
        return result


@router.patch("/projects/{ref}/facts/{key}")
def set_fact(ref: str, key: str, payload: FactUpdate) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        # 服务端盖章：key 来自路径，不信任请求体；审计由 update_fact 同事务写入。
        payload = payload.model_copy(update={"key": key})
        try:
            row = update_fact(db, project, payload, actor=current_user())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        affected = provenance.affected_by_change(db, project.id, row.key, row.value or "")
        source = db.get(SourceFile, row.source_file_id) if row.source_file_id else None
        return {
            "key": row.key,
            "label": row.label,
            "value": row.value,
            "unit": row.unit,
            "status": row.status,
            "required": row.required,
            "confirmed_by": row.confirmed_by,
            "confirmed_at": _iso(row.confirmed_at),
            "provenance_kind": getattr(row, "provenance_kind", None) or "tender_extract",
            "supplier_note": getattr(row, "supplier_note", "") or "",
            "source_file_id": row.source_file_id,
            "source_file_name": source.filename if source else "",
            "source_quote": row.source_quote,
            "source_page": row.source_page,
            "stale_sections": affected,
        }


@router.get("/projects/{ref}/outline")
def outline(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return {"chapters": _outline_payload(db, project.id)}


@router.post("/projects/{ref}/outline")
def make_outline(ref: str, payload: OutlineRequest) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        ledger = load_ledger(db, project.id)
        package = load_package(project.package)
        spec = build_outline(ledger, package, mode=payload.mode, total_words=payload.words)
        nodes = save_outline(db, project.id, spec)
        audit(
            db,
            project.id,
            "outline.save",
            {"mode": spec.mode, "count": len(nodes)},
        )
        return {
            "mode": spec.mode,
            "chapters": [
                {"id": node.id, "heading": node.heading, "target_words": node.target_words}
                for node in nodes
            ],
        }


@router.put("/projects/{ref}/outline")
def save_outline_edits(ref: str, payload: OutlineSave) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        existing = {
            node.id: node
            for node in db.scalars(
                select(OutlineNode).where(OutlineNode.project_id == project.id)
            )
        }
        keep: set[str] = set()
        for position, item in enumerate(payload.chapters):
            node = existing.get(item.id) if item.id else None
            if node is None:
                node = OutlineNode(project_id=project.id, heading=item.heading)
                db.add(node)
                db.flush()
                db.add(Section(node_id=node.id, body="", status="pending", words=0))
            node.position = position
            node.heading = item.heading.strip()
            node.intent = item.intent
            node.target_words = max(100, min(50000, item.target_words))
            keep.add(node.id)
        removed = [node_id for node_id in existing if node_id not in keep]
        if removed:
            db.execute(delete(Section).where(Section.node_id.in_(removed)))
            db.execute(delete(OutlineNode).where(OutlineNode.id.in_(removed)))
        db.flush()
        audit(db, project.id, "outline.save", {"count": len(payload.chapters)})
        return {"chapters": _outline_payload(db, project.id)}


@router.get("/projects/{ref}/chapters/{node_id}")
def chapter(ref: str, node_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return _chapter_payload(db, _node(db, project.id, node_id))


@router.get("/projects/{ref}/chapters/{node_id}/citations")
def chapter_citations(ref: str, node_id: str) -> dict[str, Any]:
    """本章正文里出现的台账取值，逐条给出字段、当前值与原件出处。"""

    with session_scope() as db:
        project, _role = _project(db, ref)
        node = _node(db, project.id, node_id)
        items = provenance.node_citations(db, project.id, node.id)
        return {
            "node_id": node.id,
            "heading": node.heading,
            "citations": items,
            "stale": sum(1 for item in items if item["stale"]),
        }


@router.get("/projects/{ref}/chapters/{node_id}/plan")
def get_chapter_plan(ref: str, node_id: str) -> dict[str, Any]:
    """读取本章当前有效规划（无规划时 plan=null，status=NOT_CREATED）。"""

    with session_scope() as db:
        project, _role = _project(db, ref)
        node = authorize_node(db, project.id, node_id)
        plan = load_active_plan(db, project.id, node.id)
        return {
            "node_id": node.id,
            "heading": node.heading,
            "mode": _planning_mode(project),
            "status": plan.status if plan else "NOT_CREATED",
            "plan": chapter_plan_payload(plan) if plan else None,
        }


def _generate_chapter_plan(
    ref: str,
    node_id: str,
    payload: ChapterPlanGenerateRequest,
    *,
    regenerate: bool,
) -> dict[str, Any]:
    # 1) 短事务：鉴权 + 构造模型输入（只读真实行，不含模型长调用）。
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        node = authorize_node(db, project.id, node_id)
        user = current_user() or {}
        package_id = str((project.meta or {}).get("package_id") or "") or None
        try:
            provider = scoped_knowledge_provider(
                _knowledge_provider, db, user.get("id"), project.id, package_id
            )
        except Exception:  # noqa: BLE001 - 知识检索失败不阻断规划
            provider = None
        package_id = str(payload.package_id or "").strip() or package_id
        try:
            lessons_text = lessons_block(db, project, node.id)
        except Exception:  # noqa: BLE001 - 未审核经验是可选 advisory
            lessons_text = ""
        context = build_plan_context(
            db,
            project.id,
            node,
            payload.user_instruction,
            provider,
            user_id=str(user.get("id") or ""),
            package_id=package_id,
            lessons_text=lessons_text,
        )
        client = _generation_client(db)
        project_id = project.id
        node_id_value = node.id
        package_id = payload.package_id or context.get("package_id")
        created_by = str(user.get("username") or "")
        user_id_value = str(user.get("id") or "")

    # 2) 模型调用在事务之外；不可用时回退确定性规划，原始返回留在 raw_response。
    model_output = ""
    if client.available:
        try:
            from ...generation.prompts import chapter_plan_messages

            model_output = str(
                client.complete(
                    chapter_plan_messages(context), max_tokens=1800, temperature=0.1
                )
                or ""
            )
        except Exception:  # noqa: BLE001 - 统一按回退处理
            model_output = ""

    # 3) 短事务：落库 + 审计。
    with session_scope() as db:
        project = db.get(Project, project_id)
        node = db.get(OutlineNode, node_id_value)
        plan = generate_chapter_plan(
            db,
            project,
            node,
            payload.user_instruction,
            created_by,
            regenerate,
            model_output=model_output,
            package_id=package_id,
            user_id=user_id_value,
        )
        result = chapter_plan_payload(plan)
        audit(
            db,
            project_id,
            "chapter_plan.generate",
            {
                "node_id": node_id_value,
                "heading": node.heading,
                "version": plan.version,
                "status": plan.status,
                "regenerate": bool(regenerate),
                "validation_errors": result["validation_errors"],
            },
        )
        return {"plan": result}


@router.post("/projects/{ref}/chapters/{node_id}/plan")
def create_chapter_plan(
    ref: str, node_id: str, payload: ChapterPlanGenerateRequest
) -> dict[str, Any]:
    """生成章节规划新版本；模型不可用时使用真实项目数据的确定性回退。"""

    return _generate_chapter_plan(ref, node_id, payload, regenerate=bool(payload.regenerate))


@router.post("/projects/{ref}/chapters/{node_id}/plan/generate")
def create_chapter_plan_alias(
    ref: str, node_id: str, payload: ChapterPlanGenerateRequest
) -> dict[str, Any]:
    """统一契约别名：与 POST /plan 等价，便于前端只使用文档化路径。"""

    return _generate_chapter_plan(ref, node_id, payload, regenerate=bool(payload.regenerate))


@router.post("/projects/{ref}/chapters/{node_id}/plan/regenerate")
def regenerate_chapter_plan(
    ref: str, node_id: str, payload: ChapterPlanGenerateRequest
) -> dict[str, Any]:
    """废弃当前有效规划并追加新版本（旧规划标记为 REGENERATING）。"""

    return _generate_chapter_plan(ref, node_id, payload, regenerate=True)


@router.put("/projects/{ref}/chapters/{node_id}/plan")
def modify_chapter_plan(
    ref: str, node_id: str, payload: ChapterPlanUpdate
) -> dict[str, Any]:
    """人工调整：追加 UPDATED 新版本，旧版本自动失效。"""

    if payload.updates:
        merged = payload.model_dump(exclude={"updates"})
        wrappable = {
            key: value
            for key, value in payload.updates.items()
            if key in ChapterPlanUpdate.model_fields
            and key not in {"updates", "note", "package_id"}
        }
        for key, value in wrappable.items():
            if merged.get(key) is None:
                merged[key] = value
        payload = ChapterPlanUpdate(**merged)

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        node = authorize_node(db, project.id, node_id)
        plan = update_chapter_plan(
            db,
            project,
            node,
            payload,
            str((current_user() or {}).get("username") or ""),
            payload.package_id or (project.meta or {}).get("package_id"),
            str((current_user() or {}).get("id") or ""),
        )
        result = chapter_plan_payload(plan)
        audit(
            db,
            project.id,
            "chapter_plan.update",
            {
                "node_id": node.id,
                "heading": node.heading,
                "version": plan.version,
                "note": payload.note,
            },
        )
        return {"plan": result}


@router.post("/projects/{ref}/chapters/{node_id}/plan/confirm")
def approve_chapter_plan(
    ref: str, node_id: str, payload: ChapterPlanConfirm
) -> dict[str, Any]:
    """确认当前有效规划；校验通过后才允许正文生成。"""

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        node = authorize_node(db, project.id, node_id)
        # 不带项目 meta 的包件过滤：规划自身记录了生成时的 package_id，确认时
        # 以它为准，避免项目切换到其他标段后把本标段的合法引用静默剔除。
        plan = load_active_plan(db, project.id, node.id)
        if plan is None:
            raise _http_error(409, "该章节没有可确认的规划")
        # 确认前同样做依赖校验：避免确认成功到正文生成阶段才因失效被 409。
        stale = invalidate_stale_plan(db, plan)
        if stale:
            db.commit()
            raise _http_error(409, "章节规划依赖已变化，请重新生成并确认：" + "；".join(stale[:3]))
        username = str((current_user() or {}).get("username") or "")
        confirm_chapter_plan(db, plan, username, payload.note, str((current_user() or {}).get("id") or ""))
        result = chapter_plan_payload(plan)
        audit(
            db,
            project.id,
            "chapter_plan.confirm",
            {
                "node_id": node.id,
                "heading": node.heading,
                "plan_id": plan.id,
                "version": plan.version,
                "note": payload.note,
            },
        )
        return {"plan": result}


@router.get("/projects/{ref}/chapters/{node_id}/plan/history")
def chapter_plan_history(ref: str, node_id: str) -> dict[str, Any]:
    """按版本倒序返回一章的全部规划（含被取代与被拒绝的版本）。"""

    with session_scope() as db:
        project, _role = _project(db, ref)
        node = authorize_node(db, project.id, node_id)
        rows = load_plan_history(db, project.id, node.id)
        return {
            "node_id": node.id,
            "heading": node.heading,
            "plans": [chapter_plan_payload(row) for row in rows],
        }


@router.get("/projects/{ref}/retrieval/preview")
def retrieval_preview(ref: str, q: str = "", limit: int = 4) -> dict[str, Any]:
    """只读检索预览：viewer 可用，返回真实 passage 溯源与诊断，不改任何正文。"""

    query = str(q or "").strip()
    cap = max(1, min(20, int(limit or 4)))
    with session_scope() as db:
        project, _role = _project(db, ref)
        user = current_user() or {}
        package_id = str((project.meta or {}).get("package_id") or "") or None
        try:
            provider = scoped_knowledge_provider(
                _knowledge_provider, db, user.get("id"), project.id, package_id
            )
            retrieved = provider(query) if query else None
            text, downgrades, refs, diagnostics = normalize_retrieval(retrieved)
        except Exception:  # noqa: BLE001 - 检索不可用返回空预览，不阻断页面
            text, downgrades, refs, diagnostics = "", 0, [], []
        passages: list[dict[str, Any]] = []
        for row in refs[:cap]:
            item = retrieval_ref_payload(row)
            passages.append(
                {
                    "id": item.get("id") or "",
                    "score": item.get("score") or 0.0,
                    "rerank_score": item.get("rerank_score"),
                    # 未知信号保留 null，前端才能区分“未知”与明确的“未降级”。
                    "trust_tier": item.get("trust_tier"),
                    "degraded": item.get("degraded"),
                    "chunk_id": item.get("chunk_id") or "",
                    "document_id": item.get("document_id") or "",
                    "page": item.get("page") or 0,
                    "heading": item.get("heading") or "",
                    "source": item.get("source") or "",
                    "text": item.get("snippet") or item.get("text") or "",
                    "trace": item.get("trace") or "",
                    "kb_id": item.get("kb_id") or "",
                }
            )
        if not passages and text:
            # Old (text-only) providers still yield a reviewable text block.
            passages.append(
                {
                    "id": "", "score": 0.0, "rerank_score": None, "trust_tier": None,
                    "degraded": bool(downgrades) if downgrades else None,
                    "chunk_id": "", "document_id": "",
                    "page": 0, "heading": "", "source": "", "text": text[:2000],
                    "trace": "", "kb_id": "",
                }
            )
        audit(
            db,
            project.id,
            "retrieval.preview",
            {"query": query[:200], "limit": cap, "count": len(passages),
             "downgrades": downgrades},
        )
        return {
            "project_id": project.id,
            "query": query,
            "limit": cap,
            "passages": passages,
            "diagnostics": diagnostics,
            "downgrades": downgrades,
        }


@router.get("/projects/{ref}/citations")
def project_citations(ref: str) -> dict[str, Any]:
    """全书来源追溯：哪些章节引用了台账哪个字段、哪些已与台账不一致。"""

    with session_scope() as db:
        project, _role = _project(db, ref)
        return {"project_id": project.id, **provenance.report(db, project.id)}


@router.put("/projects/{ref}/chapters/{node_id}")
def save_chapter(ref: str, node_id: str, payload: ChapterSave) -> Any:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        node = _node(db, project.id, node_id)
        section = db.scalar(select(Section).where(Section.node_id == node.id))
        current_revision = (section.revision or 0) if section is not None else 0
        if payload.base_revision is not None and payload.base_revision != current_revision:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "章节已被他人修改，请载入最新或强制覆盖",
                    "current_revision": current_revision,
                    "current_body": section.body if section is not None else "",
                    "current_words": section.words if section is not None else 0,
                },
            )
        review_before = (section.review_status if section is not None else "draft") or "draft"
        if review_before in {"confirmed", "locked"}:
            raise _http_error(
                409,
                "本章已确认，不能再直接修改；如需改动请先撤回为草稿",
            )
        if section is None:
            section = Section(node_id=node.id)
            db.add(section)
        # R18：改动前先留存旧正文的哈希与修订号——人工确认快照要能回答
        # 「确认时依据的是哪一版正文」。current_revision 是改动前的版本。
        old_body_hash = hashlib.sha256((section.body or "").encode("utf-8")).hexdigest()
        confirmed_revision = current_revision
        section.body = payload.body
        section.words = count_words(payload.body)
        section.status = "edited"
        section.revision = current_revision + 1
        node.status = "edited"
        # 待复核的正文一旦被改写，复核对象已变，自动退回草稿（不可绕过检查点）。
        if review_before == "review":
            section.review_status = "draft"
            section.review_requested_at = None
            section.review_requested_by = None
        db.flush()
        # 第三批（协作版本）：每次有效正文保存生成一条不可变修订快照，
        # revision 与 Section.revision 对齐；与正文同事务，失败整体回滚。
        from ...collaboration import snapshot_on_save

        change_note = (payload.change_note or "").strip()[:500]
        snapshot_on_save(
            db,
            section,
            project.id,
            (current_user() or {}).get("username") or "system",
            change_note,
        )
        provenance.refresh_node(db, project.id, node.id, payload.body)
        detail = {"heading": node.heading, "change_note": change_note}
        if review_before == "review":
            detail["review_reverted"] = True
        # R15：正文变化后，匹配到本章且已人工确认的实质性条款失效——它们是基于旧
        # 正文确认的，必须重新判定/确认，不能让「已确认」掩盖了改动后的正文。
        invalidated = [
            row
            for row in db.scalars(
                select(Requirement).where(
                    Requirement.project_id == project.id,
                    Requirement.node_id == node.id,
                    Requirement.response_status != "pending",
                )
            )
        ]
        if invalidated:
            snapshot_user = (current_user() or {}).get("username") or "system"
            invalidated_at = clock.iso_beijing(clock.utc_now())
            for row in invalidated:
                # R18：失效前把「原始自动判定 + 人工确认 + 依据的正文版本」完整快照
                # 写入 Append-only 审计。普通编辑接口无法修改历史审计行；再次确认会
                # 生成新的快照，不会覆盖这一条。与正文保存同事务，失败整体回滚。
                snapshot = {
                    "requirement_id": row.id,
                    "requirement_text": row.text,
                    "marker": row.marker or "",
                    "original_auto_verdict": row.response_verdict or "",
                    "original_verdict_label": row.response_verdict_label or "",
                    "original_verdict_reason": row.response_verdict_reason or "",
                    "original_verdict_evidence": {
                        "source_quote": row.source_quote or "",
                        "page": row.page or 0,
                        "clause_no": row.clause_no or "",
                    },
                    "original_response_status": row.response_status or "",
                    "original_response_note": row.response_note or "",
                    "confirmed_by": row.responded_by or "",
                    "confirmed_at": clock.iso_beijing(row.responded_at),
                    "section_id": row.section_id or "",
                    "node_id": node.id,
                    "section_revision": confirmed_revision,
                    "body_hash": old_body_hash,
                    "invalidated_at": invalidated_at,
                    "invalidated_by": snapshot_user,
                    "invalidation_reason": "章节正文已修改，旧确认不再对应当前版本",
                    "event": "confirmation_invalidated",
                }
                audit(db, project.id, "requirement.confirmation_invalidated", snapshot)
                _apply_requirement_status(row, "pending", "", "")
                row.node_id = None
                row.section_id = None
                row.auto_covered = False
            detail["requirement_confirmations_invalidated"] = [row.id for row in invalidated]
        audit(db, project.id, "chapter.save", detail)
        return _chapter_payload(db, node)


@router.post("/projects/{ref}/chapters/{node_id}/review")
def review_chapter(ref: str, node_id: str, payload: ChapterReview) -> dict[str, Any]:
    """章节复核流转，写入审计。

    草稿 --提交--> 待复核 --负责人确认--> 已确认 --负责人锁定--> 已锁定。
    已确认/已锁定的正文不可编辑、不可重生成；导出只读取已确认（含已锁定）的章节。
    """

    with session_scope() as db:
        project, role = _project(db, ref)
        node = _node(db, project.id, node_id)
        section = db.scalar(select(Section).where(Section.node_id == node.id))
        if section is None:
            section = Section(node_id=node.id)
            db.add(section)
            db.flush()
        current = (section.review_status or "draft")
        action = payload.action
        note = (payload.note or "").strip()
        username = str((current_user() or {}).get("username") or "")

        def need_note(message: str) -> None:
            if len(note) < 4:
                raise _http_error(400, message)

        if action == "submit":
            _require_write(role)
            if current != "draft":
                raise _http_error(409, "只有草稿可以提交复核")
            if not (section.body or "").strip():
                raise _http_error(409, "本章还没有正文，无法提交复核")
            section.review_status = "review"
            section.review_requested_at = clock.utc_now()
            section.review_requested_by = username
            section.review_note = note
        elif action == "confirm":
            _require_owner(role)
            if current != "review":
                raise _http_error(409, "只有待复核的章节可以确认")
            section.review_status = "confirmed"
            section.confirmed_at = clock.utc_now()
            section.confirmed_by = username
            section.review_note = note
        elif action == "lock":
            _require_owner(role)
            if current != "confirmed":
                raise _http_error(409, "只有已确认的章节可以锁定")
            section.review_status = "locked"
            section.locked_at = clock.utc_now()
            section.locked_by = username
        elif action == "unlock":
            _require_owner(role)
            if current != "locked":
                raise _http_error(409, "只有已锁定的章节可以解锁")
            need_note("解锁章节必须说明原因（至少 4 个字）")
            section.review_status = "confirmed"
            section.locked_at = None
            section.locked_by = None
            section.review_note = note
        else:  # revert
            if current in {"confirmed", "locked"}:
                _require_owner(role)
                need_note("撤回已确认的章节必须说明原因（至少 4 个字）")
            else:
                _require_write(role)
            if current == "draft":
                raise _http_error(409, "本章已经是草稿")
            section.review_status = "draft"
            section.review_requested_at = None
            section.review_requested_by = None
            section.confirmed_at = None
            section.confirmed_by = None
            section.locked_at = None
            section.locked_by = None
            section.review_note = note
        db.flush()
        audit(
            db,
            project.id,
            "chapter.review",
            {
                "heading": node.heading,
                "action": action,
                "from": current,
                "to": section.review_status,
                "note": note,
            },
        )
        return _chapter_payload(db, node)


@router.post("/projects/{ref}/chapters/{node_id}/generate")
def generate_chapter(ref: str, node_id: str) -> dict[str, Any]:
    lease_token = ""
    # 1) 短事务：鉴权 + 门禁 + 扣费。扣费不再与耗时数十秒的模型调用共处一个
    #    长事务（原实现一旦网络抖动就会长时间持有计费行与 DB 连接）。
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        node = _node(db, project.id, node_id)
        node_id_value = node.id
        heading = node.heading
        planning_warnings: list[str] = []
        active_plan = _chapter_plan_gate(db, project, node, planning_warnings)
        # 门禁通过的规划必须冻结：后续生成会话里若有人改动/删除该行，也不能让
        # writer 悄悄换用另一版本（尤其不能回退到 DRAFT/UPDATED）。
        frozen_plan = active_plan
        plan_meta = _plan_metadata(active_plan)
        existing = db.scalar(select(Section).where(Section.node_id == node.id))
        if existing is not None and (existing.review_status or "draft") in {
            "confirmed",
            "locked",
        }:
            raise _http_error(
                409, "本章已确认，不能再重新生成；如需重写请先撤回为草稿"
            )
        ledger = load_ledger(db, project.id)
        problems = ledger_gate(ledger)
        if problems:
            raise HTTPException(status_code=409, detail={"missing_required": problems})
        package = load_package(project.package)
        user = billing_user(db)
        client = _generation_client(db)
        _credit_guard(db, user, billing.billing_settings(db)["chapter_cost"])
        if user.is_external and not client.available:
            raise HTTPException(
                status_code=409, detail="尚未配置可用的对话模型，请联系超级管理员"
            )
        lease_token = jobqueue.acquire_generation_lease(
            db,
            project.id,
            job_id=f"sync:{uuid.uuid4().hex}",
            owner_id=user.id,
        )
        if not lease_token:
            raise HTTPException(
                status_code=409, detail="该项目已有生成任务在运行，请等待完成后再试"
            )
        try:
            charged = billing.charge_chapters(
                db, user, 1, project_id=project.id, note=f"生成本章：{heading}"
            )
        except billing.InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        # durable marker 与扣费同事务提交：进程在扣费后、写章节前崩溃时，
        # reconcile_pending_charges 能凭它把费用退还（API 响应保持不变）。
        pending_id = jobqueue.begin_pending_charge(
            db, user, project.id, charged, 1, note=f"生成本章：{heading}"
        )
        project_id = project.id
        package_id = str((project.meta or {}).get("package_id") or "") or None
    # 2) 模型调用。generate_document 需要可写 Session 落地章节（writer.py 直接
    #    写 Section/OutlineNode 并 flush），且 before/after_chapter 钩子也接收
    #    该 Session；在不动 writer.py 的前提下无法把 HTTP 模型调用再挪出会话，
    #    因此采用"窄事务"：此会话只覆盖章节写入，不含扣费/退款/审计。
    #    同步路径没有 Job 行，长任务靠 heartbeat 续 marker 的 updated_at，避免
    #    TTL 对账把仍在运行的生成误判为崩溃并提前全额退款。
    settled = False
    heartbeat_abort = threading.Event()
    try:
        with jobqueue.pending_charge_heartbeat(
            pending_id,
            lease_project_id=project_id,
            lease_token=lease_token,
            abort=heartbeat_abort,
        ), session_scope() as gen_db:
            report = generate_document(
                gen_db,
                project_id,
                client,
                ledger,
                package,
                only_node=node_id_value,
                knowledge_provider=scoped_knowledge_provider(
                    _knowledge_provider, gen_db, user.id, project_id, package_id
                ),
                plan_provider=lambda nid: (
                    frozen_plan if nid == node_id_value and frozen_plan is not None else None
                ),
            )
        # 心跳线程发现租约失效后只置事件；生成会话可能已写出部分章节，按已写
        # 章节数结算而不是全额退款，避免把已完成的工作白送。
        if heartbeat_abort.is_set():
            raise RuntimeError("项目生成租约已失效，生成中止")
    except BaseException:
        # 原实现里模型调用抛错会回滚整个事务（含扣费）；扣费已提前提交，必须
        # 显式退还，保持净额与原错误行为一致。BaseException（Ctrl-C/SIGTERM）
        # 同样会走到这里；settled 保证后续正常结算不会二次退款。
        if not settled:
            settled = True
            with session_scope() as refund_db:
                jobqueue.settle_pending_charge(
                    refund_db,
                    pending_id,
                    user,
                    project_id,
                    1,
                    len(report.written or []) if "report" in locals() else 0,
                )
        _release_generation_lease(project_id, lease_token)
        raise
    # 3) 短写事务：未写出章节退还、状态、溯源与审计。
    with session_scope() as db:
        if not settled:
            settled = True
            jobqueue.settle_pending_charge(
                db, pending_id, user, project_id, 1, len(report.written or [])
            )
        project = db.get(Project, project_id)
        project.status = "drafting"
        db.flush()
        provenance.refresh_node(db, project_id, node_id_value)
        if plan_meta["planning_id"]:
            mark_plan_used(db, db.get(ChapterPlan, plan_meta["planning_id"]))
        audit(
            db,
            project_id,
            "chapter.generate",
            {
                **plan_meta,
                "heading": heading,
                "chapter_plan_warnings": planning_warnings,
                "unused_plan_refs": getattr(report, "unused_plan_refs", {}),
            },
        )
        jobqueue.release_generation_lease(db, project_id, lease_token)
    return _report_payload(report)


@router.post("/projects/{ref}/write")
def write(ref: str) -> dict[str, Any]:
    """Synchronous generation kept for CLI parity and small integrations."""
    lease_token = ""
    # 与 generate_chapter 同样拆成"短计费事务 → 窄生成会话 → 短收尾事务"。
    # writer.generate_document 必须拿到可写 Session 才能落地章节（见该函数
    # 注释），所以 HTTP 模型调用无法完全脱离会话，但计费已不再处于长事务中。
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        ledger = load_ledger(db, project.id)
        problems = ledger_gate(ledger)
        if problems:
            raise HTTPException(status_code=409, detail={"missing_required": problems})
        package = load_package(project.package)
        if not load_outline(db, project.id):
            save_outline(db, project.id, build_outline(ledger, package))
            db.flush()
        planning_warnings: list[str] = []
        plan_meta_list: list[dict[str, Any]] = []
        frozen_plans: dict[str, ChapterPlan] = {}
        for outline_row in load_outline(db, project.id):
            if not is_pending(outline_row):
                continue
            node_row = db.get(OutlineNode, outline_row["id"])
            if node_row is None:
                continue
            active = _chapter_plan_gate(db, project, node_row, planning_warnings)
            if active is not None:
                frozen_plans[node_row.id] = active
                plan_meta_list.append(_plan_metadata(active))
        plan_meta = plan_meta_list[0] if plan_meta_list else _plan_metadata(None)
        plan_ids = [
            item["planning_id"] for item in plan_meta_list if item["planning_id"]
        ]
        user = billing_user(db)
        client = _generation_client(db)
        count = _pending_chapters(db, project.id)
        _credit_guard(db, user, billing.billing_settings(db)["chapter_cost"] * max(0, count))
        if user.is_external and not client.available:
            raise HTTPException(
                status_code=409, detail="尚未配置可用的对话模型，请联系超级管理员"
            )
        lease_token = jobqueue.acquire_generation_lease(
            db,
            project.id,
            job_id=f"sync:{uuid.uuid4().hex}",
            owner_id=user.id,
        )
        if not lease_token:
            raise HTTPException(
                status_code=409, detail="该项目已有生成任务在运行，请等待完成后再试"
            )
        try:
            charged = billing.charge_chapters(
                db, user, count, project_id=project.id, note="同步生成全书正文"
            )
        except billing.InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        pending_id = jobqueue.begin_pending_charge(
            db, user, project.id, charged, count, note="同步生成全书正文"
        )
        project_id = project.id
        package_id = str((project.meta or {}).get("package_id") or "") or None
    settled = False
    heartbeat_abort = threading.Event()
    try:
        with jobqueue.pending_charge_heartbeat(
            pending_id,
            lease_project_id=project_id,
            lease_token=lease_token,
            abort=heartbeat_abort,
        ), session_scope() as gen_db:
            report = generate_document(
                gen_db,
                project_id,
                client,
                ledger,
                package,
                knowledge_provider=scoped_knowledge_provider(
                    _knowledge_provider, gen_db, user.id, project_id, package_id
                ),
                plan_provider=lambda nid: frozen_plans.get(nid),
            )
        if heartbeat_abort.is_set():
            raise RuntimeError("项目生成租约已失效，生成中止")
    except BaseException:
        # 扣费已提前提交；模型调用失败（含 BaseException）时显式退还，等价于
        # 原来的整事务回滚。settled 防住后续正常结算的重复退款。
        if not settled:
            settled = True
            with session_scope() as refund_db:
                jobqueue.settle_pending_charge(
                    refund_db,
                    pending_id,
                    user,
                    project_id,
                    count,
                    len(report.written or []) if "report" in locals() else 0,
                )
        _release_generation_lease(project_id, lease_token)
        raise
    with session_scope() as db:
        if not settled:
            settled = True
            jobqueue.settle_pending_charge(
                db, pending_id, user, project_id, count, len(report.written or [])
            )
        project = db.get(Project, project_id)
        project.status = "drafting"
        db.flush()
        for plan_id in plan_ids:
            mark_plan_used(db, db.get(ChapterPlan, plan_id))
        audit(
            db,
            project_id,
            "project.write",
            {
                **plan_meta,
                "planning_ids": plan_ids,
                "plans": plan_meta_list,
                "chapter_plan_warnings": planning_warnings,
                "unused_plan_refs": getattr(report, "unused_plan_refs", {}),
                "chapters": len(report.written),
            },
        )
        jobqueue.release_generation_lease(db, project_id, lease_token)
    return _report_payload(report)


@router.post("/projects/{ref}/generate", status_code=202)
def generate_all(ref: str, payload: GenerateRequest | None = None) -> dict[str, Any]:
    force = bool(payload.force) if payload else False
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        ledger = load_ledger(db, project.id)
        problems = ledger_gate(ledger)
        if problems:
            raise HTTPException(status_code=409, detail={"missing_required": problems})
        if _cancel_stale_jobs(
            db,
            ["generate"],
            30 * 60,
            project_id=project.id,
            stale_message="生成任务超时中断，已允许重新生成",
        ) is not None:
            raise HTTPException(status_code=409, detail="该项目已有生成任务在运行")
        user = billing_user(db)
        _enqueue_or_429(db, user.id or "")
        client = _generation_client(db)
        count = _pending_chapters(db, project.id, force=force)
        if count <= 0:
            raise HTTPException(status_code=409, detail="没有待生成的章节")
        planning_warnings: list[str] = []
        plan_meta_list: list[dict[str, Any]] = []
        plans_by_node: dict[str, dict[str, Any]] = {}
        for outline_row in load_outline(db, project.id):
            if not (force or is_pending(outline_row)):
                continue
            node_row = db.get(OutlineNode, outline_row["id"])
            if node_row is None:
                continue
            active = _chapter_plan_gate(db, project, node_row, planning_warnings)
            if active is not None:
                meta = _plan_metadata(active)
                plan_meta_list.append(meta)
                plans_by_node[node_row.id] = meta
        plan_meta = plan_meta_list[0] if plan_meta_list else _plan_metadata(None)
        plan_ids = [
            item["planning_id"] for item in plan_meta_list if item["planning_id"]
        ]
        _credit_guard(db, user, billing.billing_settings(db)["chapter_cost"] * max(0, count))
        if user.is_external and not client.available:
            raise HTTPException(
                status_code=409, detail="尚未配置可用的对话模型，请联系超级管理员"
            )
        job_id = str(uuid.uuid4())
        lease_token = jobqueue.acquire_generation_lease(
            db, project.id, job_id=job_id, owner_id=user.id
        )
        if not lease_token:
            raise HTTPException(
                status_code=409, detail="该项目已有生成任务在运行，请等待完成后再试"
            )
        try:
            charged = billing.charge_chapters(
                db, user, count, project_id=project.id, note="生成全书正文"
            )
        except billing.InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        marker_id = jobqueue.begin_pending_charge(
            db,
            user,
            project.id,
            charged,
            count,
            note=f"job:{job_id}|生成全书正文(异步)",
        )
        job = Job(
            id=job_id,
            project_id=project.id,
            kind="generate",
            status="queued",
            message="排队中…",
            payload={
                "charged": charged,
                "chapters": count,
                "user_id": user.id,
                # 扣费实际落在父账号；子账号被删除后退款仍需找到付款方。
                "billing_account_id": str(getattr(user, "parent_id", None) or user.id),
                # 对账 marker 与 Job 的双向索引：崩溃后按 done_steps 精确退款。
                "pending_charge_id": marker_id,
                # 同项目生成互斥租约；worker 心跳续租、终态释放。
                "generation_lease_token": lease_token,
                # 背压检查用 owner_id 索引列；user_id 仅为 worker 结算保留。
                "owner_id": user.id,
                "cost_per": charged // count if count else 0,
                # DB 驱动派发：worker 从 payload 重建 force 参数。
                "force": bool(force),
                # v0.32 章节规划溯源：worker 按 node_id 使用同一份规划，
                # 顶层字段兼容单章查询，plans 保存逐章完整映射。
                **plan_meta,
                "planning_ids": plan_ids,
                "plans": plans_by_node,
                "chapter_plan_warnings": planning_warnings,
            },
        )
        db.add(job)
        db.flush()
        job.priority = billing.queue_priority(db, user)
        priority = job.priority
        jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
        audit(
            db,
            project.id,
            "project.generate",
            {
                "chapters": count,
                **plan_meta,
                "planning_ids": plan_ids,
                "chapter_plan_warnings": planning_warnings,
            },
        )
    jobqueue.submit(
        _run_generation_job,
        (job_id, project.id, force),
        priority=priority,
        job_id=job_id,
    )
    return {"job_id": job_id}


@router.get("/projects/{ref}/jobs")
def project_jobs(ref: str, kind: str = "") -> list[dict[str, Any]]:
    """Recent jobs of one kind, used to re-attach after a page refresh."""

    with session_scope() as db:
        project, _role = _project(db, ref)
        rows = list(
            db.scalars(
                select(Job)
                .where(
                    Job.project_id == project.id,
                    Job.kind == (kind.strip() or "generate"),
                )
                .order_by(Job.created_at.desc())
                .limit(20)
            )
        )
        return [jobs.job_payload(job) for job in rows]


@router.post("/projects/{ref}/check")
def check(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        results, summary = check_project(db, project.id)
        audit(db, project.id, "check.run", {"total": summary["total"]})
        return {
            "verdict": summary["verdict"],
            "summary": summary,
            "issues": [result.model_dump() for result in results if not result.passed],
        }


@router.get("/projects/{ref}/issues")
def issues(ref: str, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        project, _role = _project(db, ref)
        # 严重度排序与分页下推到 SQL（Issue.id 作为稳定的次级键），不再把
        # 项目下的全部问题行读进 Python 再 sort/slice。
        severity = case(
            {level: rank for level, rank in SEVERITY_ORDER.items()},
            value=Issue.level,
            else_=9,
        )
        page = list(
            db.scalars(
                select(Issue)
                .where(Issue.project_id == project.id)
                .order_by(severity, Issue.id)
                .limit(capped)
                .offset(start)
            )
        )
        node_map = _issue_node_map(db, page)
        return [_issue_payload(row, node_map) for row in page]


@router.get("/projects/{ref}/risk-overview")
def risk_overview(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        rows = list(db.scalars(select(Issue).where(Issue.project_id == project.id)))
        return _risk_overview_payload(db, project.id, rows)


@router.get("/projects/{ref}/requirements")
def requirements(ref: str) -> list[dict[str, Any]]:
    """实质性条款及其正文响应状态；只读成员也能看（交付风险需要被看见）。"""

    with session_scope() as db:
        project, _role = _project(db, ref)
        rows = list(
            db.scalars(
                select(Requirement)
                .where(Requirement.project_id == project.id)
                .order_by(Requirement.marker.desc(), Requirement.text)
            )
        )
        # 单请求内项目级预加载：一次 load_outline 同时得到大纲正文与
        # node_id→heading 索引（R05），多条条款共享，消除逐条 db.get(OutlineNode)。
        context = build_requirement_context(db, project.id)
        return [_requirement_payload(db, row, context=context) for row in rows]


@router.patch("/projects/{ref}/requirements/{req_id}")
def requirement_response_update(
    ref: str, req_id: str, payload: RequirementResponseUpdate
) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        requirement = db.get(Requirement, req_id)
        if requirement is None or requirement.project_id != project.id:
            raise LookupError(f"条款不存在: {req_id}")
        # R07：编制与确认分离。★ 实质性条款的最终确认（含撤回）必须由项目负责人/超管
        # 执行；editor 仍可写响应正文（章节编辑），但不能自行确认 ★ 条款。
        # 权限在后端强制校验，不依赖前端隐藏按钮。
        if (requirement.marker or "").strip() == "★":
            _require_owner(role)
        note = _validate_requirement_note(payload.response_status, payload.note)
        username = str((current_user() or {}).get("username") or "")
        # 确认时快照「原始自动判定结果」，让人员/时间/原因/判定可被完整追溯。
        snapshot: dict[str, str] = {}
        # 与返回 payload 共享同一次大纲加载（T06/R05）：一次查询同时供判定与展示。
        context = build_requirement_context(db, project.id)
        if payload.response_status != "pending":
            # 与 /check 同口径：带入招标截止日期与可选语义 matcher，避免
            # 确认快照的判定与 SUB 问题的判定漂移。
            current = judge_for_project(
                db,
                requirement,
                context.outline_nodes,
                deadline=context.deadline,
                matcher=context.matcher,
                project_meta=context.project_meta,
            )
            snapshot = {
                "state": current.state,
                "label": current.label,
                "reason": current.reason,
                "level": current.level,
            }
        _apply_requirement_status(
            requirement, payload.response_status, note, username, verdict=snapshot
        )
        audit(
            db,
            project.id,
            "requirement.response",
            {
                "requirement_id": requirement.id,
                "status": payload.response_status,
                "note": note,
                "verdict": snapshot.get("state", ""),
                "verdict_label": snapshot.get("label", ""),
                "verdict_reason": snapshot.get("reason", ""),
            },
        )
        db.flush()
        return _requirement_payload(db, requirement, context=context)


@router.patch("/projects/{ref}/issues/{issue_id}")
def issue_status_update(
    ref: str, issue_id: str, payload: IssueStatusUpdate
) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        issue = db.get(Issue, issue_id)
        if issue is None or issue.project_id != project.id:
            raise LookupError(f"风险不存在: {issue_id}")
        note = _validate_issue_note(payload.status, payload.note)
        previous = issue.status
        username = str((current_user() or {}).get("username") or "")
        _apply_issue_status(issue, payload.status, note, username)
        audit(
            db,
            project.id,
            "issue.status",
            {
                "issue_id": issue.id,
                "code": issue.code,
                "level": issue.level,
                "from": previous,
                "to": payload.status,
                "note": note,
            },
        )
        db.flush()
        return _issue_payload(issue, _issue_node_map(db, [issue]))


@router.post("/projects/{ref}/issues/bulk-status")
def issue_bulk_status(ref: str, payload: IssueBulkStatusUpdate) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        ids = list(dict.fromkeys(payload.ids))
        if len(ids) > ISSUE_BULK_LIMIT:
            raise _http_error(400, f"一次最多批量处置 {ISSUE_BULK_LIMIT} 项风险")
        note = _validate_issue_note(payload.status, payload.note)
        rows = (
            {
                row.id: row
                for row in db.scalars(
                    select(Issue).where(
                        Issue.project_id == project.id, Issue.id.in_(ids)
                    )
                )
            }
            if ids
            else {}
        )
        username = str((current_user() or {}).get("username") or "")
        updated: list[Issue] = []
        for issue_id in ids:
            issue = rows.get(issue_id)
            if issue is None:
                continue  # 不属于该项目的 id 直接忽略
            _apply_issue_status(issue, payload.status, note, username)
            updated.append(issue)
        audit(
            db,
            project.id,
            "issue.bulk_status",
            {
                "ids": len(ids),
                "updated": len(updated),
                "status": payload.status,
                "note": note,
            },
        )
        db.flush()
        node_map = _issue_node_map(db, updated)
        return {
            "updated": len(updated),
            "items": [_issue_payload(row, node_map) for row in updated],
        }


@router.get("/projects/{ref}/export-templates")
def export_templates_list(ref: str) -> dict[str, Any]:
    from ...export import templates as export_templates

    with session_scope() as db:
        project, _role = _project(db, ref)
        return {
            "presets": export_templates.preset_list(),
            "current": export_templates.load(project),
        }


@router.put("/projects/{ref}/export-template")
def export_template_set(ref: str, payload: ExportTemplateUpdate) -> dict[str, Any]:
    from ...export import templates as export_templates

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        try:
            chosen = export_templates.save(project, payload.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, project.id, "project.export_template", {"id": payload.id})
        db.flush()
        return {"current": chosen, "presets": export_templates.preset_list()}


def _require_docx_integrity(path: Path) -> None:
    """交付前校验 DOCX 包结构；仅 error 级别阻断，警告不阻断。"""

    from ...documents.validator import validate_docx_package

    report = validate_docx_package(path)
    errors = [item for item in report.get("issues", []) if item.get("severity") == "error"]
    if errors:
        raise HTTPException(
            status_code=500,
            detail="导出文档完整性校验失败：" + str(errors[0].get("message") or errors[0]),
        )


@router.post("/projects/{ref}/export")
def export(ref: str, payload: ExportRequest) -> FileResponse:
    fmt = payload.format.lower()
    # 1) 短事务：鉴权 + 硬门禁 + 取渲染所需的只读快照。
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        # 交付硬门禁：未处置的废标/阻断风险禁止导出正文类格式；
        # 强制覆盖仅限 owner/admin，且必须留痕（export.force 审计）。
        # 统计/校验/审计与 MCP、CLI 共用 export_gate，保证口径与文案一致。
        try:
            enforce_export_gate(
                db,
                project.id,
                fmt,
                force=payload.force,
                force_reason=payload.force_reason,
                authorize_force=lambda: _require_owner(role),
            )
        except ExportGateError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        ledger = load_ledger(db, project.id)
        nodes = load_outline(db, project.id)
        if fmt != "xlsx" and (not nodes or not any(node.get("body") for node in nodes)):
            raise HTTPException(status_code=409, detail="还没有正文，请先生成或编写章节")
        project_id = project.id
        project_name = project.name
        output_dir = settings.project_dir(project_id) / "output"
        from ...export import templates as export_templates

        image_resolver = images.url_resolver(project_id)
        image_policy = images.get_policy(project)
        template = export_templates.load(project)
        # 专业引擎是 opt-in：显式在 project.meta 中打开并提供基础模板时启用。
        export_meta = dict(project.meta or {})
        professional = bool(export_meta.get("export_professional"))
        base_template = export_meta.get("export_base_template") or None
    # 2) DOCX 渲染与 PDF 的 LibreOffice/UNO 转换（超时可达 240s）在事务外执行，
    #    避免长时间占用数据库连接/写事务；失败时与原实现一样不写审计。
    if fmt == "docx":
        path = export_docx(
            output_dir / "bid.docx",
            ledger,
            nodes,
            image_resolver=image_resolver,
            image_policy=image_policy,
            template=template,
            professional=professional,
            base_template=base_template,
        )
        _require_docx_integrity(path)
    elif fmt in {"md", "markdown"}:
        path = export_markdown(output_dir / "bid.md", ledger, nodes)
    elif fmt == "xlsx":
        from ...export.xlsx import export_xlsx

        path = export_xlsx(output_dir / "bid.xlsx", ledger)
    elif fmt == "pdf":
        from ...export.pdf import PdfExportError, export_pdf

        docx_path = export_docx(
            output_dir / "bid.docx",
            ledger,
            nodes,
            image_resolver=image_resolver,
            image_policy=image_policy,
            template=template,
            professional=professional,
            base_template=base_template,
        )
        _require_docx_integrity(docx_path)
        try:
            path = export_pdf(docx_path)
        except PdfExportError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=400, detail="format 只能是 docx/md/pdf/xlsx")
    safe = re.sub(r'[\\/:*?"<>|]', "", project_name).strip() or "BidCraft"
    label = {
        "docx": "投标文件",
        "pdf": "投标文件",
        "md": "投标文件",
        "xlsx": "事实台账",
    }[fmt if fmt != "markdown" else "md"]
    # 3) 渲染成功后再写审计（与原"同事务提交"的语义一致：失败不留痕）。
    with session_scope() as db:
        audit(db, project_id, "export.run", {"format": fmt})
    return FileResponse(path, filename=f"{safe[:60]}-{label}{path.suffix}")


@router.get("/projects/{ref}/packages")
def package_list(
    ref: str, limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        project, _role = _project(db, ref)
        rows = _bid_packages(db, project.id)
        counts = _boq_package_ids(db, project.id, [row.id for row in rows])
        return [
            _bid_package_payload(row, row.id in counts)
            for row in rows[start : start + capped]
        ]


@router.post("/projects/{ref}/packages", status_code=201)
def package_create(ref: str, payload: PackageCreate) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="包件名称不能为空")
        existing = _bid_packages(db, project.id)
        if any(item.name == name for item in existing):
            raise HTTPException(status_code=409, detail=f"包件名称已存在：{name}")
        package = BidPackage(
            project_id=project.id,
            name=name,
            number=(payload.number or "").strip(),
            position=max((item.position or 0 for item in existing), default=-1) + 1,
        )
        db.add(package)
        db.flush()
        adopted = 0
        if not existing:
            # First package adopts the legacy project-wide (NULL) snapshot.
            for snapshot in db.scalars(
                select(BoqSnapshot).where(
                    BoqSnapshot.project_id == project.id,
                    BoqSnapshot.package_id.is_(None),
                )
            ):
                snapshot.package_id = package.id
                adopted += 1
            db.flush()
        audit(
            db,
            project.id,
            "project.package_create",
            {"package_id": package.id, "name": name, "adopted_snapshots": adopted},
        )
        has_boq = boq.get_snapshot(db, project.id, package.id) is not None
        return _bid_package_payload(package, has_boq)


@router.patch("/projects/{ref}/packages/{package_id}")
def package_update(
    ref: str, package_id: str, payload: PackageUpdate
) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        package = _bid_package(db, project, package_id)
        changed: dict[str, Any] = {}
        if payload.name is not None:
            name = payload.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="包件名称不能为空")
            duplicate = db.scalar(
                select(BidPackage).where(
                    BidPackage.project_id == project.id,
                    BidPackage.name == name,
                    BidPackage.id != package.id,
                )
            )
            if duplicate is not None:
                raise HTTPException(status_code=409, detail=f"包件名称已存在：{name}")
            if name != package.name:
                package.name = name
                changed["name"] = name
        if payload.number is not None:
            number = payload.number.strip()
            if number != (package.number or ""):
                package.number = number
                changed["number"] = number
        db.flush()
        audit(
            db,
            project.id,
            "project.package_update",
            {"package_id": package.id, **changed},
        )
        has_boq = boq.get_snapshot(db, project.id, package.id) is not None
        return _bid_package_payload(package, has_boq)


@router.delete("/projects/{ref}/packages/{package_id}")
def package_delete(ref: str, package_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        package = _bid_package(db, project, package_id)
        boq.remove_snapshot(db, project, package_id=package.id)
        audit(
            db,
            project.id,
            "project.package_delete",
            {"package_id": package.id, "name": package.name},
        )
        db.delete(package)
        db.flush()
        return {"deleted": True, "id": package.id}


@router.get("/projects/{ref}/boq")
def boq_alignment(ref: str, package_id: str | None = None) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        package = _resolve_boq_package(db, project, package_id)
        return boq.build_payload(
            db, project.id, package_id=package.id if package else None
        )


@router.post("/projects/{ref}/boq")
async def boq_upload(
    ref: str,
    file: UploadFile = File(...),
    sheet: str = Form(""),
    package_id: str = Form(""),
) -> dict[str, Any]:
    raw_name = (file.filename or "").strip()
    if raw_name and raw_name not in {"/", "\\"}:
        suffix = Path(raw_name).suffix.lower()
        if suffix not in BOQ_UPLOAD_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="工程量清单仅支持 .xlsx / .xlsm 文件",
            )
    filename = _safe_filename(raw_name, "boq.xlsx")
    with tempfile.TemporaryDirectory(prefix="bidcraft-boq-") as tmp:
        target = Path(tmp) / filename
        await _save_upload(
            file,
            target,
            max_bytes=_core.MAX_BOQ_UPLOAD_BYTES,
            detail="工程量清单不能超过 20MB",
        )
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            package = _resolve_boq_package(db, project, package_id)
            resolved = package.id if package else None
            try:
                boq.import_boq(
                    db, project, target, sheet=sheet or None, package_id=resolved
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            audit(
                db,
                project.id,
                "boq.upload",
                {"filename": filename, "package_id": resolved},
            )
            return boq.build_payload(db, project.id, package_id=resolved)


@router.post("/projects/{ref}/boq/apply")
def boq_apply(ref: str, payload: BoqApplyRequest) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            package = boq.resolve_package(db, project, payload.package_id)
            resolved = package.id if package else None
            result = boq.apply_action(
                db, project, payload.boq_row, payload.action, package_id=resolved
            )
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            project.id,
            "boq.apply",
            {"row": payload.boq_row, "action": payload.action, "package_id": resolved},
        )
        return result


@router.post("/projects/{ref}/boq/price")
def boq_price(ref: str, payload: BoqPriceRequest) -> dict[str, Any]:
    from ...services.quote_service import set_unit_price as service_set_unit_price

    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            # 编排下沉到 Service：解析包件 + 改价 + 审计一次完成，真实身份作为
            # actor 显式注入；HTTP 异常映射仍留在路由层（Service 只抛领域异常）。
            result = service_set_unit_price(
                db,
                project,
                payload.boq_row,
                payload.unit_price,
                package_id=payload.package_id,
                actor=current_user(),
            )
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result


@router.get("/projects/{ref}/boq/versions")
def boq_version_list(
    ref: str, package_id: str | None = None
) -> list[dict[str, Any]]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        try:
            return boq.list_versions(db, project, package_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{ref}/boq/versions", status_code=201)
def boq_version_create(ref: str, payload: BoqVersionCreate) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            version = boq.save_version(
                db,
                project,
                payload.name,
                payload.note,
                package_id=payload.package_id,
            )
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            project.id,
            "boq.version_save",
            {
                "version_id": version["id"],
                "name": version["name"],
                "package_id": version["package_id"],
            },
        )
        return version


@router.get("/projects/{ref}/boq/versions/{version_id}")
def boq_version_detail(ref: str, version_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        version = boq.get_version(db, project.id, version_id)
        if version is None:
            raise LookupError("版本不存在")
        return boq.version_payload(version, include_prices=True, project=project)


@router.get("/projects/{ref}/boq/versions/{version_id}/diff")
def boq_version_diff(
    ref: str, version_id: str, other: str | None = None
) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        try:
            return boq.diff_version(db, project, version_id, other)
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{ref}/boq/versions/{version_id}/restore")
def boq_version_restore(ref: str, version_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            result = boq.restore_version(db, project, version_id)
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snapshot = result.get("snapshot") or {}
        audit(
            db,
            project.id,
            "boq.version_restore",
            {"version_id": version_id, "package_id": snapshot.get("package_id")},
        )
        return result


@router.post("/projects/{ref}/boq/versions/{version_id}/submit")
def boq_version_submit(
    ref: str, version_id: str, payload: BoqVersionSubmit | None = None
) -> dict[str, Any]:
    data = payload or BoqVersionSubmit()
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        user = current_user() or {}
        actor = user.get("display_name") or user.get("username") or ""
        try:
            result = boq.submit_version(
                db, project, version_id, actor=actor, note=data.note
            )
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            project.id,
            "quote.submit",
            {
                "version_id": result["id"],
                "name": result["name"],
                "package_id": result["package_id"],
                "note": result["submit_note"],
            },
        )
        return result


@router.post("/projects/{ref}/boq/versions/{version_id}/review")
def boq_version_review(
    ref: str, version_id: str, payload: BoqVersionReview
) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        user = current_user() or {}
        actor = user.get("display_name") or user.get("username") or ""
        try:
            # R19：授权下沉到 boq.review_version——空链仅 owner/超管，
            # 非空链按当前级审批人（或超管）放行，会签/或签按阶段模式推进。
            result = boq.review_version(
                db,
                project,
                version_id,
                actor=actor,
                decision=payload.decision,
                note=payload.note,
                actor_id=str(user.get("id") or ""),
                role=_role,
                is_super=user.get("role") == auth.ROLE_SUPER_ADMIN,
            )
        except boq.BoqForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            project.id,
            "quote.approve" if payload.decision == "approve" else "quote.reject",
            {
                "version_id": result["id"],
                "name": result["name"],
                "package_id": result["package_id"],
                "note": payload.note,
                "status": result["status"],
                "stage": len(result["approvals"]),
            },
        )
        return result


@router.post("/projects/{ref}/boq/versions/{version_id}/unapprove")
def boq_version_unapprove(ref: str, version_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            result = boq.unapprove_version(db, project, version_id)
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            project.id,
            "quote.unapprove",
            {
                "version_id": result["id"],
                "name": result["name"],
                "package_id": result["package_id"],
            },
        )
        return result


@router.delete("/projects/{ref}/boq/versions/{version_id}")
def boq_version_delete(ref: str, version_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            boq.delete_version(db, project, version_id)
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit(db, project.id, "boq.version_delete", {"version_id": version_id})
        return {"deleted": True, "id": version_id}


@router.get("/projects/{ref}/price-policy")
def price_policy_get(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return boq.get_price_policy(project)


@router.put("/projects/{ref}/price-policy")
def price_policy_put(ref: str, payload: PricePolicyUpdate) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        policy = boq.set_price_policy(db, project, payload.model_dump())
        audit(db, project.id, "price.policy", policy)
        return policy


@router.post("/projects/{ref}/boq/quota-fill")
def boq_quota_fill(
    ref: str, payload: BoqQuotaFillRequest | None = None
) -> dict[str, Any]:
    data = payload or BoqQuotaFillRequest()
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            package = boq.resolve_package(db, project, data.package_id)
            resolved = package.id if package else None
            result = boq.quota_fill(
                db,
                project,
                package_id=resolved,
                only_missing=data.only_missing,
                dry_run=data.dry_run,
            )
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not data.dry_run:
            audit(
                db,
                project.id,
                "boq.quota_fill",
                {
                    "applied": result["applied"],
                    "only_missing": data.only_missing,
                },
            )
        return result


@router.post("/projects/{ref}/boq/adjust")
def boq_adjust(ref: str, payload: BoqAdjustRequest | None = None) -> dict[str, Any]:
    data = payload or BoqAdjustRequest()
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        try:
            package = boq.resolve_package(db, project, data.package_id)
            resolved = package.id if package else None
            result = boq.adjust_prices(db, project, package_id=resolved)
        except boq.BoqConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            project.id,
            "boq.adjust",
            {"factor": result["factor"], "rows": result["rows"]},
        )
        return result["payload"]


@router.delete("/projects/{ref}/boq")
def boq_delete(ref: str, package_id: str | None = None) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        package = _resolve_boq_package(db, project, package_id)
        resolved = package.id if package else None
        boq.remove_snapshot(db, project, package_id=resolved)
        audit(db, project.id, "boq.delete", {"package_id": resolved})
        return {"deleted": True}


@router.post("/projects/{ref}/boq/export")
def boq_export(ref: str, package_id: str | None = None) -> FileResponse:
    from ...export.boq_xlsx import export_boq_xlsx

    with session_scope() as db:
        project, _role = _project(db, ref)
        _require_write(_role)
        package = _resolve_boq_package(db, project, package_id)
        resolved = package.id if package else None
        payload = boq.build_payload(db, project.id, package_id=resolved)
        if payload["snapshot"] is None:
            raise HTTPException(status_code=409, detail="尚未上传工程量清单，无法导出响应表")
        path = export_boq_xlsx(
            settings.project_dir(project.id) / "output" / "boq-response.xlsx", payload
        )
        safe = re.sub(r'[\\/:*?"<>|]', "", project.name).strip() or "BidCraft"
        if package is None:
            filename = f"{safe[:60]}-清单响应表.xlsx"
        else:
            section = re.sub(r'[\\/:*?"<>|]', "", package.name).strip() or "包件"
            filename = f"{safe[:60]}-{section[:40]}-清单响应表.xlsx"
        return FileResponse(path, filename=filename)


