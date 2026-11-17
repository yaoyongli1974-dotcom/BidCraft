"""HTTP API and B/S web host (optional extra: ``pip install -e '.[api]'``).

The API is a thin wrapper over the same pipeline used by the CLI. It uses
cookie sessions and role-based access control; there is no telemetry. Bind to
127.0.0.1 for single-user mode, or put it behind TLS and your own network
controls when serving a team.
"""

import io
import json
import logging
import math
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import (
    __version__,
    auth,
    billing,
    boq,
    images,
    jobs,
    knowledge,
    model_config,
    plugins,
    terms,
    wechat,
    wechatpay,
)
from .config import settings
from .context import current_user, set_current_user
from .db import init_db, session_scope
from .export.docx import export_docx, export_markdown
from .generation.agent import ModelClient
from .generation.outline import build_outline, load_outline, save_outline
from .generation.writer import count_words, generate_document
from .ledger import ledger_summary, load_ledger, update_fact
from .models import (
    AuditLog,
    BidPackage,
    BoqSnapshot,
    Delivery,
    Fact,
    ImageAsset,
    Issue,
    Job,
    JobEvent,
    Material,
    OutlineNode,
    Parameter,
    Plugin,
    Project,
    ProjectMember,
    QuotaItem,
    RechargeOrder,
    RedeemCode,
    Requirement,
    Risk,
    ScoringItem,
    Section,
    SourceFile,
    TermItem,
    User,
)
from .pipeline import add_source, check_project, create_project, parse_project, resolve_project
from .rules import store as package_store
from .rules.engine import ledger_gate
from .rules.loader import (
    document_package_name,
    list_packages,
    load_package,
    validate_package_text,
)

SEVERITY_ORDER = {"blocking": 0, "critical": 1, "major": 2, "minor": 3, "info": 4}

logger = logging.getLogger("bidcraft.api")

MAX_TENDER_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_BOQ_UPLOAD_BYTES = 20 * 1024 * 1024
TENDER_UPLOAD_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md", ".xlsx", ".xlsm"})
BOQ_UPLOAD_SUFFIXES = frozenset({".xlsx", ".xlsm"})
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_PAGE_SIZE = 1000
BATCH_STALE_SECONDS = 2 * 3600
KB_REINDEX_STALE_SECONDS = 2 * 3600
STARTUP_INTERRUPTED_MESSAGE = "服务重启，任务已中断"

# Roles that represent tender documents (the BOQ workbook is managed separately
# by the 清单对齐 feature and must never show up as a tender source).
TENDER_SOURCE_ROLES = ("tender", "annex")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    package: str = "weakcurrent-cabling"
    # 标段/包件：仅作项目元信息，不参与多包件共享台账（后续迭代）。
    bid_section: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    bid_section: str | None = None


class BatchIdsRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class BatchExportRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    format: str = "docx"


class MemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    role: str = "editor"


class MemberUpdate(BaseModel):
    role: str


class RulePackageDocument(BaseModel):
    document: str = Field(min_length=1, max_length=200 * 1024)


class RulePackageValidateRequest(BaseModel):
    name: str = ""
    document: str = Field(min_length=1, max_length=200 * 1024)


class FactUpdate(BaseModel):
    value: str
    status: Literal["extracted", "confirmed", "manual", "missing", "conflict"] = "confirmed"


class OutlineRequest(BaseModel):
    mode: str = "auto"
    words: int = Field(default=30000, ge=1000, le=200000)


class OutlineChapter(BaseModel):
    id: str | None = None
    heading: str = Field(min_length=1, max_length=200)
    intent: str = ""
    target_words: int = Field(default=1200, ge=100, le=50000)


class OutlineSave(BaseModel):
    chapters: list[OutlineChapter] = Field(default_factory=list, max_length=200)


class ChapterSave(BaseModel):
    body: str = Field(max_length=200000)
    base_revision: int | None = None


class GenerateRequest(BaseModel):
    force: bool = False


class ImageCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    node_id: str = ""
    heading: str = ""
    prompt: str = Field(default="", max_length=2000)
    style: str = Field(default="", max_length=80)
    size: str = Field(default="", max_length=20)


class ImageBatchRequest(BaseModel):
    include_failed: bool = False


class ExportRequest(BaseModel):
    format: str = "docx"


class BoqApplyRequest(BaseModel):
    boq_row: int
    action: str
    package_id: str | None = None


class BoqPriceRequest(BaseModel):
    boq_row: int
    unit_price: float | None = None
    package_id: str | None = None


class BoqVersionCreate(BaseModel):
    name: str
    note: str = ""
    package_id: str | None = None


class BoqQuotaFillRequest(BaseModel):
    package_id: str | None = None
    only_missing: bool = True
    dry_run: bool = True


class BoqAdjustRequest(BaseModel):
    package_id: str | None = None


class PricePolicyUpdate(BaseModel):
    enabled: bool | None = None
    factor: float | None = None
    round_digits: int | None = None
    tax_rate: float | None = None
    currency: str | None = None
    exchange_rate: float | None = None


class ImagePolicyUpdate(BaseModel):
    max_total: int | None = None
    style: str | None = None
    size: str | None = None
    captions: bool | None = None
    caption_prefix: str | None = None
    width_cm: float | None = None


class QuotaCreate(BaseModel):
    name: str = ""
    spec: str = ""
    unit: str = ""
    unit_price: float = 0.0
    category: str = ""
    note: str = ""


class QuotaUpdate(BaseModel):
    name: str | None = None
    spec: str | None = None
    unit: str | None = None
    unit_price: float | None = None
    category: str | None = None
    note: str | None = None


class TermCreate(BaseModel):
    kind: str = ""
    source: str = ""
    target: str = ""
    note: str = ""
    enabled: bool = True


class TermUpdate(BaseModel):
    kind: str | None = None
    source: str | None = None
    target: str | None = None
    note: str | None = None
    enabled: bool | None = None


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    number: str = Field(default="", max_length=40)


class PackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    number: str | None = Field(default=None, max_length=40)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = ""


class OrderCreate(BaseModel):
    package_id: str


class RedeemRequest(BaseModel):
    code: str = Field(min_length=4, max_length=40)


class CreditAdjust(BaseModel):
    amount: int = Field(ge=-10**9, le=10**9)
    note: str = ""


class RedeemGenerate(BaseModel):
    count: int = 10
    credits: int = 10
    amount_cents: int = 0
    days: int = 365


class BillingSettingsPayload(BaseModel):
    signup_credits: int = 5
    chapter_cost: int = 1
    packages: list[dict[str, Any]] = Field(default_factory=list)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    display_name: str = ""
    password: str = Field(min_length=8, max_length=200)
    role: str = "viewer"


class UserUpdate(BaseModel):
    display_name: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)
    role: str | None = None
    active: bool | None = None


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "chat"
    provider: str = "openai"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.4
    max_tokens: int = 4000
    options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class ModelUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    options: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class KbCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "local"
    provider: str = "local"
    location: str = ""
    api_key: str = ""
    dataset_id: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class KbUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    provider: str | None = None
    location: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    dataset_id: str | None = None
    options: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class PluginCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    transport: str = "stdio"
    command: str = ""
    # 允许传数组，也允许传 JSON 文本/逗号分隔字符串（管理端表单更省事）。
    args: list[str] | str | None = None
    env: dict[str, str] | str | None = None
    url: str = ""
    enabled: bool = True
    tool_allowlist: list[str] | str | None = None


class PluginUpdate(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | str | None = None
    env: dict[str, str] | str | None = None
    url: str | None = None
    enabled: bool | None = None
    tool_allowlist: list[str] | str | None = None


def _iso(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


PROJECT_WRITE_ROLES = {"owner", "editor", "admin"}
MEMBER_ROLES = {"editor", "viewer"}


def _safe_filename(name: str | None, fallback: str) -> str:
    """Return the basename of an uploaded file, or ``fallback`` when empty."""

    value = Path((name or "").replace("\\", "/")).name.strip()
    if not value or value in {".", ".."}:
        return fallback
    return value


def _page_bounds(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), MAX_PAGE_SIZE)), max(0, int(offset))


async def _save_upload(file, target: Path, *, max_bytes: int, detail: str) -> int:
    """Stream an upload to disk, aborting as soon as it exceeds ``max_bytes``."""

    total = 0
    with target.open("wb") as handle:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                handle.close()
                target.unlink(missing_ok=True)
                raise _http_error(413, detail)
            handle.write(chunk)
    return total


def _discard_source(db: Session, source: SourceFile) -> None:
    """Remove a copied source file and its pending row after a failed parse."""

    Path(source.stored_path).unlink(missing_ok=True)
    try:
        db.delete(source)
        db.flush()
    except Exception:  # noqa: BLE001 - the request is already failing
        logger.warning("删除失败上传的源文件记录失败", exc_info=True)


def _fail_job(db: Session, job: Job, message: str, error: str | None = None) -> None:
    """Mark a job failed and publish the matching SSE event."""

    detail = error or message
    job.status = "failed"
    job.message = message
    job.result = {"error": detail}
    jobs.publish_event(job.id, "failed", {**jobs.job_payload(job), "error": detail}, db=db)
    db.flush()


def _cancel_stale_jobs(
    db: Session,
    kinds: list[str],
    timeout_seconds: int,
    *,
    project_id: str | None = None,
    stale_message: str = "任务超时中断，已允许重新执行",
) -> Job | None:
    """Return the live job for ``kinds``; auto-fail it once it is stale.

    ``project_id=None`` matches project-less batch jobs.  A ``None`` return means
    the caller may enqueue a new job.
    """

    statement = select(Job).where(
        Job.kind.in_(kinds), Job.status.in_(["queued", "running"])
    )
    if project_id is None:
        statement = statement.where(Job.project_id.is_(None))
    else:
        statement = statement.where(Job.project_id == project_id)
    running = db.scalar(statement.order_by(Job.created_at.desc()))
    if running is None:
        return None
    last = running.updated_at or running.created_at
    if last is not None and datetime.now(UTC).replace(tzinfo=None) - last < timedelta(
        seconds=timeout_seconds
    ):
        return running
    _fail_job(
        db,
        running,
        stale_message,
        (
            f"任务超过 {timeout_seconds // 3600} 小时无进度，已标记为中断"
            if timeout_seconds >= 3600
            else f"任务超过 {max(1, timeout_seconds // 60)} 分钟无进度，已标记为中断"
        ),
    )
    return None


def _active_kb_reindex(db: Session, kb_id: str) -> Job | None:
    """Return a live reindex job for one knowledge base, auto-failing stale ones.

    Reindex jobs are project-less (``project_id=None``), so the generic
    :func:`_cancel_stale_jobs` cannot scope them per KB; the ``payload.kb_id``
    filter is applied in Python because SQLite JSON lookups are not portable.
    """

    rows = list(
        db.scalars(
            select(Job)
            .where(Job.kind == "kb_reindex", Job.status.in_(["queued", "running"]))
            .order_by(Job.created_at.desc())
        )
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    for row in rows:
        if str((row.payload or {}).get("kb_id") or "") != kb_id:
            continue
        last = row.updated_at or row.created_at
        if last is not None and now - last < timedelta(seconds=KB_REINDEX_STALE_SECONDS):
            return row
        _fail_job(
            db,
            row,
            "知识库索引任务超时中断，已允许重新提交",
            f"任务超过 {max(1, KB_REINDEX_STALE_SECONDS // 60)} 分钟无进度，已标记为中断",
        )
    return None


def _recover_interrupted_jobs() -> int:
    """Fail every job that was queued/running when the process last stopped."""

    with session_scope() as db:
        rows = list(
            db.scalars(select(Job).where(Job.status.in_(["queued", "running"])))
        )
        for row in rows:
            _fail_job(db, row, STARTUP_INTERRUPTED_MESSAGE, STARTUP_INTERRUPTED_MESSAGE)
        return len(rows)


def _bid_package_payload(package: BidPackage, has_boq: bool = False) -> dict[str, Any]:
    return {
        "id": package.id,
        "name": package.name,
        "number": package.number or "",
        "position": package.position or 0,
        "has_boq": bool(has_boq),
        "created_at": _iso(package.created_at),
    }


def _project(db: Session, ref: str) -> tuple[Project, str]:
    """Resolve a project the caller may see, plus their effective project role.

    Roles: ``admin`` (super administrator, any project), ``owner`` (project
    creator), ``editor``/``viewer`` (``ProjectMember`` grant). Anyone else gets
    a 404 so project existence is never leaked.
    """

    try:
        project = resolve_project(db, ref)
    except ValueError as exc:
        raise LookupError(str(exc)) from exc
    user = current_user()
    if not user:
        raise LookupError("项目不存在")
    if user.get("role") == auth.ROLE_SUPER_ADMIN:
        return project, "admin"
    if project.owner_id and project.owner_id == user.get("id"):
        return project, "owner"
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.get("id"),
        )
    )
    if member is not None and member.role in MEMBER_ROLES:
        return project, member.role
    raise LookupError("项目不存在")


def _require_write(role: str) -> None:
    if role not in PROJECT_WRITE_ROLES:
        raise _http_error(403, "只读成员不能修改该项目")


def _require_owner(role: str) -> None:
    if role not in {"owner", "admin"}:
        raise _http_error(403, "仅项目负责人可执行此操作")


def audit(
    db, project_id: str | None, action: str, detail: dict | None = None
) -> AuditLog:
    """Record a successful mutating action for the current request user."""

    user = current_user() or {}
    row = AuditLog(
        project_id=project_id,
        user_id=user.get("id"),
        username=user.get("username") or "system",
        display_name=user.get("display_name") or user.get("username") or "system",
        action=action,
        detail=detail or {},
    )
    db.add(row)
    db.flush()
    return row


def _job(db, job_id: str) -> Job:
    """Load a job and enforce the same project ownership as other endpoints."""

    job = db.get(Job, job_id)
    if job is None:
        raise LookupError(f"任务不存在: {job_id}")
    if job.project_id:
        _project(db, job.project_id)
    else:
        user = current_user()
        owner_id = str((job.payload or {}).get("owner_id") or "")
        if not user or (
            user.get("role") != auth.ROLE_SUPER_ADMIN
            and owner_id != str(user.get("id") or "")
        ):
            raise LookupError("任务不存在")
    return job


def _batch_projects(db, ids: list[str]) -> list[Project]:
    """Resolve requested project ids to those the caller may operate on.

    Super administrators see every project; everyone else is limited to owned
    projects and project memberships. Unknown ids are dropped, duplicates are
    collapsed while keeping the request order.
    """

    user = current_user() or {}
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in ids:
        ref = str(raw or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    projects: list[Project] = []
    is_admin = user.get("role") == auth.ROLE_SUPER_ADMIN
    for ref in ordered:
        project = db.get(Project, ref)
        if project is None:
            continue
        if is_admin or (project.owner_id and project.owner_id == user.get("id")):
            projects.append(project)
            continue
        member = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.get("id"),
            )
        )
        if member is not None:
            projects.append(project)
    return projects


def _project_stats(db, project_id: str) -> dict[str, Any]:
    sources = db.scalar(
        select(func.count())
        .select_from(SourceFile)
        .where(
            SourceFile.project_id == project_id,
            SourceFile.role.in_(TENDER_SOURCE_ROLES),
        )
    )
    facts_total = db.scalar(
        select(func.count()).select_from(Fact).where(Fact.project_id == project_id)
    )
    facts_filled = db.scalar(
        select(func.count())
        .select_from(Fact)
        .where(Fact.project_id == project_id, Fact.value != "")
    )
    missing_required = db.scalar(
        select(func.count())
        .select_from(Fact)
        .where(Fact.project_id == project_id, Fact.required.is_(True), Fact.value == "")
    )
    outline = db.scalar(
        select(func.count()).select_from(OutlineNode).where(OutlineNode.project_id == project_id)
    )
    written = db.scalar(
        select(func.count())
        .select_from(Section)
        .join(OutlineNode, Section.node_id == OutlineNode.id)
        .where(OutlineNode.project_id == project_id, Section.words > 0)
    )
    issues_open = db.scalar(
        select(func.count())
        .select_from(Issue)
        .where(Issue.project_id == project_id, Issue.status == "open")
    )
    return {
        "sources": sources or 0,
        "facts_total": facts_total or 0,
        "facts_filled": facts_filled or 0,
        "missing_required": missing_required or 0,
        "outline": outline or 0,
        "written": written or 0,
        "issues_open": issues_open or 0,
    }


def _package_usage(db) -> dict[str, int]:
    """Count projects that reference each package name."""
    rows = db.execute(select(Project.package, func.count()).group_by(Project.package)).all()
    return {str(name or ""): int(count) for name, count in rows}


def _rule_package_admin_payload(
    name: str, usage: dict[str, int], document: str | None = None
) -> dict[str, Any] | None:
    for item in list_packages(include_hidden=True):
        if item["name"] == name:
            payload = {**item, "usage": usage.get(str(item["name"]), 0)}
            if document is not None:
                payload["document"] = document
            return payload
    return None


def _project_payload(
    project: Project, stats: dict[str, Any] | None = None, my_role: str | None = None
) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "package": project.package,
        "status": project.status,
        "buyer": project.buyer,
        "owner_id": project.owner_id,
        "my_role": my_role,
        "number": (project.meta or {}).get("number", ""),
        "tender_number": (project.meta or {}).get("tender_number", ""),
        "bid_section": (project.meta or {}).get("bid_section", "") or "",
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
        "stats": stats or {},
    }


def _source_payload(source: SourceFile) -> dict[str, Any]:
    return {
        "id": source.id,
        "filename": source.filename,
        "role": source.role,
        "size": source.size,
        "page_count": source.page_count,
        "parse_state": source.parse_state,
        "parse_note": source.parse_note,
        "created_at": _iso(source.created_at),
    }


def _issue_payload(issue: Issue) -> dict[str, Any]:
    return {
        "id": issue.id,
        "code": issue.code,
        "level": issue.level,
        "category": issue.category,
        "location": issue.location,
        "finding": issue.finding,
        "recommendation": issue.recommendation,
        "status": issue.status,
        "evidence": issue.evidence or {},
    }


def _outline_payload(db, project_id: str) -> list[dict[str, Any]]:
    chapters = []
    for node in load_outline(db, project_id):
        body = node.pop("body", "")
        chapters.append({**node, "has_body": bool(body)})
    return chapters


def _report_payload(report) -> dict[str, Any]:
    return {
        "written": report.written,
        "fallback": report.fallback,
        "failed": report.failed,
        "suspicious_numbers": report.suspicious,
        "usage": report.usage,
    }


def _generation_client(db) -> ModelClient:
    endpoint = model_config.resolve_chat_endpoint(db)
    return ModelClient(endpoint if endpoint is not None else settings)


def _knowledge_provider(db):
    def provide(query: str) -> str:
        passages = knowledge.search(db, query, limit=4)
        return knowledge.context_block(passages)

    return provide


def _credit_guard(db, user, cost: int) -> None:
    """Fail with 402 before a missing-model 409 so the UI can offer recharge."""
    if cost <= 0 or billing.billing_exempt(user):
        return
    balance = billing.summary(db, user)["balance"]
    if balance < cost:
        raise _http_error(402, f"点数不足，本次需要 {cost} 点，当前 {balance} 点")


def _http_error(status: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status, detail=detail)


def _pending_chapters(db, project_id: str, force: bool = False) -> int:
    nodes = load_outline(db, project_id)
    return len([node for node in nodes if force or not node.get("body")])


def _job_refund(db, job: Job, written: int) -> None:
    payload = dict(job.payload or {})
    charged = int(payload.get("charged") or 0)
    chapters = int(payload.get("chapters") or 0)
    if not charged or not chapters or payload.get("refunded"):
        return
    refund = max(0, chapters - written) * charged // chapters
    user = db.get(User, payload.get("user_id") or "")
    if refund > 0 and user is not None:
        billing.refund_chapters(db, user, refund)
        payload["refunded"] = True
        payload["refunded_credits"] = refund
        job.payload = payload


def _run_generation_job(job_id: str, project_id: str, force: bool) -> None:
    """Background worker: writes chapters and streams progress into the Job row.

    Progress is persisted twice: the job row remains the fallback/polling view,
    while ``job_events`` is the resumable SSE feed used by the browser.
    """
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.message = "正在准备事实台账…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)
            project = db.get(Project, project_id)
            if project is None:
                raise ValueError("项目不存在")
            ledger = load_ledger(db, project_id)
            problems = ledger_gate(ledger)
            if problems:
                raise ValueError("必填事实缺失：" + "、".join(problems))
            package = load_package(project.package)
            if not load_outline(db, project_id):
                save_outline(db, project_id, build_outline(ledger, package))
                db.flush()

        with session_scope() as work_db:
            project = work_db.get(Project, project_id)
            ledger = load_ledger(work_db, project_id)
            package = load_package(project.package)
            current = {"heading": ""}
            delta = {"text": "", "last": time.monotonic()}

            def flush_delta() -> None:
                text = delta["text"]
                if not text:
                    return
                delta["text"] = ""
                delta["last"] = time.monotonic()
                # Release any pending chapter writes before opening the short
                # delta session: SQLite allows a single writer at a time.
                work_db.commit()
                with session_scope() as delta_db:
                    jobs.publish_event(
                        job_id,
                        "delta",
                        {"heading": current["heading"], "text": text},
                        db=delta_db,
                    )

            def on_chapter_start(info: dict[str, Any]) -> None:
                # SQLite has a single writer: close the previous chapter's
                # transaction before writing the job/event rows.
                work_db.commit()
                flush_delta()
                current["heading"] = str(info.get("heading") or "")
                with session_scope() as start_db:
                    row = start_db.get(Job, job_id)
                    if row is None:
                        return
                    row.message = f"正在生成：{current['heading']}"
                    row.payload = {
                        **(row.payload or {}),
                        "heading": current["heading"],
                        "done": max(0, int(info.get("index") or 1) - 1),
                        "total": int(info.get("total") or 0),
                    }
                    jobs.publish_event(
                        job_id,
                        "chapter_start",
                        {
                            "heading": current["heading"],
                            "index": int(info.get("index") or 0),
                            "total": int(info.get("total") or 0),
                            "target_words": int(info.get("target_words") or 0),
                        },
                        db=start_db,
                    )

            def on_progress(info: dict[str, Any]) -> None:
                work_db.commit()
                flush_delta()
                with session_scope() as progress_db:
                    row = progress_db.get(Job, job_id)
                    if row is None:
                        return
                    row.progress = int(info.get("progress") or 0)
                    row.payload = {
                        **(row.payload or {}),
                        "heading": info.get("heading", ""),
                        "done": info.get("done", 0),
                        "total": info.get("total", 0),
                    }
                    prefix = "本章失败" if info.get("failed") else "已完成"
                    row.message = (
                        f"{prefix} {info.get('done', 0)}/{info.get('total', 0)} 章"
                    )
                    jobs.publish_event(
                        job_id,
                        "chapter",
                        {
                            "heading": info.get("heading", ""),
                            "progress": int(info.get("progress") or 0),
                            "done": int(info.get("done") or 0),
                            "total": int(info.get("total") or 0),
                            "words": int(info.get("words") or 0),
                            "source": info.get("source", ""),
                            "failed": bool(info.get("failed")),
                        },
                        db=progress_db,
                    )

            def on_delta(chunk: str) -> None:
                if not chunk:
                    return
                delta["text"] += chunk
                if len(delta["text"]) >= 320 or time.monotonic() - delta["last"] >= 0.25:
                    flush_delta()

            report = generate_document(
                work_db,
                project_id,
                _generation_client(work_db),
                ledger,
                package,
                stream=True,
                on_delta=on_delta,
                on_progress=on_progress,
                on_chapter_start=on_chapter_start,
                force=force,
                knowledge_provider=_knowledge_provider(work_db),
            )
            flush_delta()
            project.status = "drafting"
            work_db.flush()

        with session_scope() as done_db:
            row = done_db.get(Job, job_id)
            if row is not None:
                row.status = "done"
                row.progress = 100
                row.message = f"正文完成：{len(report.written)} 章"
                result = _report_payload(report)
                row.result = result
                _job_refund(done_db, row, len(report.written))
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(row), "report": result},
                    db=done_db,
                )
                done_db.flush()
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("生成任务 %s 失败", job_id)
        failed_message = "生成失败：模型服务不可用或响应异常，详情见服务日志"
        with session_scope() as db:
            row = db.get(Job, job_id)
            if row is not None:
                _job_refund(db, row, 0)
                _fail_job(db, row, failed_message)


def _run_image_batch_job(job_id: str, project_id: str, targets: list[dict[str, Any]]) -> None:
    """Background worker for plan-driven image generation.

    Each image uses its own short session so the provider call does not hold a
    long write transaction. Provider failures are already persisted as failed
    assets by ``generate_image``; they count toward the job result and the
    worker continues. Configuration errors abort the whole job.
    """
    total = len(targets)
    succeeded = 0
    failed = 0
    skipped = 0
    failed_labels: list[str] = []
    policy: dict = dict(images.DEFAULT_POLICY)
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            project = db.get(Project, project_id)
            if project is not None:
                policy = images.get_policy(project)
            job.status = "running"
            job.message = f"正在准备配图（0/{total}）…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        for index, target in enumerate(targets, start=1):
            label = str(target.get("label") or "配图")
            style = str(target.get("style") or policy.get("style") or "").strip()
            policy_size = str(policy.get("size") or "").strip()
            if policy_size.lower() == "auto":
                # auto 表示不覆盖：沿用模型配置 image_size（再回落 1024x1024）
                policy_size = ""
            size = str(target.get("size") or policy_size or "").strip()
            status = "failed"
            asset: ImageAsset | None = None
            try:
                with session_scope() as db:
                    project = db.get(Project, project_id)
                    if project is None:
                        raise ValueError("项目不存在")
                    existing = db.scalar(
                        select(ImageAsset)
                        .where(
                            ImageAsset.project_id == project.id,
                            ImageAsset.label == label,
                            ImageAsset.status == "ready",
                        )
                        .order_by(ImageAsset.created_at.desc())
                    )
                    if existing is not None:
                        status = "skipped"
                        asset = existing
                    else:
                        source = None
                        asset_id = str(target.get("asset_id") or "")
                        if asset_id:
                            source = db.get(ImageAsset, asset_id)
                            if source is not None and source.project_id != project.id:
                                source = None
                        if source is not None and source.status == "failed":
                            asset = images.regenerate_image(
                                db, project, source, style=style, size=size
                            )
                        else:
                            asset = images.generate_image(
                                db,
                                project,
                                label=label,
                                node_id=str(target.get("node_id") or ""),
                                heading=str(target.get("heading") or ""),
                                style=style,
                                size=size,
                            )
                        status = asset.status
            except images.ImageConfigError:
                raise
            except images.ImageError as exc:
                status = "failed"
                failed_labels.append(label)
                with session_scope() as db:
                    job = db.get(Job, job_id)
                    if job is not None:
                        job.message = f"配图失败：{label}（{exc}）"
                        db.flush()

            if status == "ready":
                succeeded += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
                if label not in failed_labels:
                    failed_labels.append(label)

            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                progress = int(index * 100 / total) if total else 100
                job.progress = progress
                job.payload = {
                    **(job.payload or {}),
                    "done": index,
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped,
                    "current": label,
                }
                if status == "ready":
                    job.message = f"配图 {index}/{total}：{label}"
                elif status == "skipped":
                    job.message = f"配图 {index}/{total}：{label}（已存在，跳过）"
                else:
                    job.message = f"配图 {index}/{total}：{label} 失败"
                event = {
                    "index": index,
                    "total": total,
                    "progress": progress,
                    "label": label,
                    "status": status,
                    "asset_id": asset.id if asset is not None else "",
                    "url": images.asset_payload(asset)["url"] if asset is not None else "",
                    "done": index,
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped,
                }
                jobs.publish_event(job_id, "image", event, db=db)
                db.flush()

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                job.message = f"配图完成：成功 {succeeded}，失败 {failed}"
                if skipped:
                    job.message += f"，跳过 {skipped}"
                job.result = {
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped,
                    "failed_labels": failed_labels,
                }
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": job.result},
                    db=db,
                )
                db.flush()
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("配图任务 %s 失败", job_id)
        failed_message = "配图任务失败：图片服务不可用或响应异常，详情见服务日志"
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)


def _run_batch_check_job(job_id: str, project_ids: list[str]) -> None:
    """Background worker: run ``check_project`` for each project in turn.

    Every item uses a short session so SQLite has a single writer between items;
    a failing project is recorded in ``results`` and the batch continues.
    """

    total = len(project_ids)
    results: list[dict[str, Any]] = []
    ok_count = 0
    failed_count = 0
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.message = f"正在批量核对（0/{total}）…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        for index, project_id in enumerate(project_ids, start=1):
            name = ""
            item: dict[str, Any]
            try:
                with session_scope() as db:
                    project = db.get(Project, project_id)
                    if project is None:
                        raise ValueError("项目不存在")
                    name = project.name
                    issues, _summary = check_project(db, project_id)
                    blocking = len([issue for issue in issues if issue.level == "blocking"])
                    item = {
                        "project_id": project_id,
                        "name": name,
                        "ok": True,
                        "issues": len(issues),
                        "blocking": blocking,
                    }
                ok_count += 1
            except Exception as exc:  # noqa: BLE001 - recorded per project
                item = {
                    "project_id": project_id,
                    "name": name,
                    "ok": False,
                    "error": str(exc),
                }
                failed_count += 1
            results.append(item)
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                progress = int(index * 100 / total) if total else 100
                job.progress = progress
                job.payload = {
                    **(job.payload or {}),
                    "done": index,
                    "total": total,
                    "ok": ok_count,
                    "failed": failed_count,
                    "results": results,
                }
                job.message = f"已核对 {index}/{total}：{name or project_id}"
                jobs.publish_event(
                    job_id,
                    "batch_item",
                    {
                        **item,
                        "index": index,
                        "total": total,
                        "progress": progress,
                        "done": index,
                        "ok_count": ok_count,
                        "failed_count": failed_count,
                    },
                    db=db,
                )

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                job.message = f"核对完成：成功 {ok_count} / 失败 {failed_count}"
                job.result = {
                    "total": total,
                    "ok": ok_count,
                    "failed": failed_count,
                    "results": results,
                }
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": job.result},
                    db=db,
                )
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("批量核对任务 %s 失败", job_id)
        failed_message = "批量核对失败：服务内部错误，详情见服务日志"
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)


def _run_kb_reindex_job(job_id: str, kb_id: str) -> None:
    """Background worker: rebuild a local KB token index, then its vectors.

    The HTTP request returns as soon as the job row exists, so a large knowledge
    base no longer blocks the API worker. Each stage uses its own short session
    and publishes a progress event; the final ``done`` event carries the payload
    the old synchronous endpoint used to return.
    """

    from .models import KnowledgeBase  # imported inside create_app at request time

    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.progress = 5
            job.message = "正在重建知识库索引…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        with session_scope() as db:
            kb = db.get(KnowledgeBase, kb_id)
            if kb is None:
                raise knowledge.KnowledgeError("知识库不存在")
            indexed = knowledge.index_local(db, kb)
            db.flush()
            kb_data = knowledge.kb_payload(kb, db)
            job = db.get(Job, job_id)
            if job is not None:
                job.progress = 60
                job.message = f"文本索引完成：{indexed} 个文本块"
                jobs.publish_event(
                    job_id,
                    "reindex",
                    {**jobs.job_payload(job), "indexed": indexed, "kb": kb_data},
                    db=db,
                )

        vector: dict[str, Any] | None = None
        if settings.kb_vector_enabled:
            with session_scope() as db:
                kb = db.get(KnowledgeBase, kb_id)
                if kb is None:
                    raise knowledge.KnowledgeError("知识库不存在")
                if model_config.resolve_embedding_endpoint(db) is not None:
                    job = db.get(Job, job_id)
                    if job is not None:
                        job.progress = 70
                        job.message = "正在重建向量索引…"
                        jobs.publish_event(
                            job_id, "status", jobs.job_payload(job), db=db
                        )
                    vector = knowledge.build_vectors(db, kb)
                    db.flush()
                    kb_data = knowledge.kb_payload(kb, db)
                    job = db.get(Job, job_id)
                    if job is not None:
                        job.progress = 95
                        job.message = "向量索引完成"
                        jobs.publish_event(
                            job_id,
                            "vector",
                            {
                                **jobs.job_payload(job),
                                "vector": vector,
                                "kb": kb_data,
                            },
                            db=db,
                        )

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                job.message = f"索引重建完成：{indexed} 个文本块"
                job.result = {"indexed": indexed, "vector": vector, "kb": kb_data}
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": job.result},
                    db=db,
                )
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("知识库索引任务 %s 失败", job_id)
        if isinstance(exc, knowledge.KnowledgeError):
            failed_message = "知识库索引失败：本地目录不可用或内容无法解析，详情见服务日志"
        else:
            failed_message = "知识库索引失败：服务内部错误，详情见服务日志"
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)


def _run_batch_export_job(job_id: str, project_ids: list[str], fmt: str) -> None:
    """Background worker: export each project, then bundle the files into a zip.

    Projects without body text fail only for non-xlsx formats; the batch keeps
    going and the final zip contains whatever succeeded.
    """

    total = len(project_ids)
    files: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    entries: list[tuple[Path, str]] = []
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.message = f"正在批量导出（0/{total}）…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        for index, project_id in enumerate(project_ids, start=1):
            name = ""
            item: dict[str, Any]
            try:
                with session_scope() as db:
                    project = db.get(Project, project_id)
                    if project is None:
                        raise ValueError("项目不存在")
                    name = project.name
                    ledger = load_ledger(db, project_id)
                    nodes = load_outline(db, project_id)
                    if fmt != "xlsx" and (
                        not nodes or not any(node.get("body") for node in nodes)
                    ):
                        raise ValueError("还没有正文，请先生成或编写章节")
                    output_dir = settings.project_dir(project_id) / "output"
                    if fmt == "docx":
                        path = export_docx(
                            output_dir / "bid.docx",
                            ledger,
                            nodes,
                            image_resolver=images.url_resolver(project_id),
                            image_policy=images.get_policy(project),
                        )
                    elif fmt == "md":
                        path = export_markdown(output_dir / "bid.md", ledger, nodes)
                    else:
                        from .export.xlsx import export_xlsx

                        path = export_xlsx(output_dir / "bid.xlsx", ledger)
                ext = "md" if fmt == "md" else fmt
                safe = re.sub(r'[\\/:*?"<>|]', "", name).strip() or "bid"
                arcname = f"{safe[:40]}-{index}.{ext}"
                entries.append((Path(path), arcname))
                files.append({"project_id": project_id, "name": name, "arcname": arcname})
                item = {
                    "project_id": project_id,
                    "name": name,
                    "ok": True,
                    "arcname": arcname,
                }
            except Exception as exc:  # noqa: BLE001 - recorded per project
                item = {
                    "project_id": project_id,
                    "name": name,
                    "ok": False,
                    "error": str(exc),
                }
                failed_items.append(
                    {"project_id": project_id, "name": name, "error": str(exc)}
                )
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                progress = int(index * 100 / total) if total else 100
                job.progress = progress
                job.payload = {
                    **(job.payload or {}),
                    "done": index,
                    "total": total,
                    "ok": len(files),
                    "failed": len(failed_items),
                }
                job.message = f"已导出 {index}/{total}：{name or project_id}"
                jobs.publish_event(
                    job_id,
                    "batch_item",
                    {
                        **item,
                        "index": index,
                        "total": total,
                        "progress": progress,
                        "done": index,
                        "ok_count": len(files),
                        "failed_count": len(failed_items),
                    },
                    db=db,
                )

        zip_dir = settings.home / "output"
        zip_dir.mkdir(parents=True, exist_ok=True)
        zip_path = zip_dir / f"batch-{job_id[:8]}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path, arcname in entries:
                bundle.write(path, arcname=arcname)

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                job.message = f"导出完成：成功 {len(files)}，失败 {len(failed_items)}"
                job.result = {
                    "zip_path": str(zip_path),
                    "files": files,
                    "failed": failed_items,
                    "total": total,
                    "ok": len(files),
                }
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": job.result},
                    db=db,
                )
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("批量导出任务 %s 失败", job_id)
        failed_message = "批量导出失败：服务内部错误，详情见服务日志"
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)


def create_app():
    try:
        from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
        from fastapi.responses import (
            FileResponse,
            JSONResponse,
            RedirectResponse,
            StreamingResponse,
        )
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError("HTTP API 需要安装可选依赖：pip install -e '.[api]'") from exc

    from .models import AuthSession, KbChunk, KnowledgeBase, ModelConfig, User
    from .security import encrypt_secret, hash_password, verify_password

    init_db()
    auth.ensure_admin()
    try:
        with session_scope() as db:
            auth.purge_expired_sessions(db)
    except Exception:  # noqa: BLE001 - startup must not fail on cleanup
        logger.exception("清理过期会话失败")
    try:
        _recover_interrupted_jobs()
    except Exception:  # noqa: BLE001 - startup must not fail on cleanup
        logger.exception("恢复中断任务失败")
    app = FastAPI(title="BidCraft", version=__version__, description="投标文件编制系统")

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        path = request.url.path
        public = (
            path
            in {
                "/health",
                "/auth/login",
                "/auth/register",
                "/auth/wechat/start",
                "/auth/wechat/callback",
                "/auth/wechat/mock",
                "/billing/wechat/notify",
                "/docs",
                "/redoc",
                "/openapi.json",
                "/favicon.ico",
            }
            or path.startswith(("/assets/", "/docs/"))
        )
        if public:
            return await call_next(request)
        api_prefixes = (
            "/projects",
            "/jobs",
            "/batch-jobs/",
            "/packages",
            "/admin/",
            "/auth",
            "/billing/",
        )
        if not path.startswith(api_prefixes):
            return await call_next(request)
        with session_scope() as db:
            user = auth.resolve_session(db, request.cookies.get(auth.SESSION_COOKIE))
            if user is None:
                return JSONResponse(status_code=401, content={"detail": "请先登录"})
            payload = auth.user_payload(user)
            request.state.user = payload
            set_current_user(payload)
        role = request.state.user["role"]
        if path.startswith("/admin/") and role != auth.ROLE_SUPER_ADMIN:
            return JSONResponse(status_code=403, content={"detail": "仅超级管理员可访问"})
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and role == auth.ROLE_VIEWER
            and path not in {"/auth/password", "/auth/logout"}
        ):
            return JSONResponse(status_code=403, content={"detail": "只读成员不能执行此操作"})
        return await call_next(request)

    @app.exception_handler(LookupError)
    async def not_found(_request, exc: LookupError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.post("/auth/login")
    def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        with session_scope() as db:
            try:
                user, token = auth.login(
                    db,
                    payload.username,
                    payload.password,
                    user_agent=request.headers.get("user-agent", ""),
                    ip=request.client.host if request.client else "",
                )
                auth.purge_expired_sessions(db)
            except PermissionError as exc:
                status = 429 if "次数过多" in str(exc) else 401
                raise HTTPException(status_code=status, detail=str(exc)) from exc
            response.set_cookie(
                auth.SESSION_COOKIE,
                token,
                max_age=settings.session_days * 86400,
                httponly=True,
                samesite="lax",
                secure=settings.secure_cookie,
                path="/",
            )
            return {"user": auth.user_payload(user)}

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            auth.SESSION_COOKIE,
            token,
            max_age=settings.session_days * 86400,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookie,
            path="/",
        )

    @app.post("/auth/register", status_code=201)
    def register(payload: RegisterRequest, request: Request, response: Response) -> dict[str, Any]:
        with session_scope() as db:
            try:
                user = auth.register_user(
                    db,
                    username=payload.username,
                    password=payload.password,
                    display_name=payload.display_name,
                )
                # Commit the new account before auth.login opens its own session
                # for the persistent login-throttle counter (SQLite single writer).
                db.commit()
                user, token = auth.login(
                    db,
                    user.username,
                    payload.password,
                    user_agent=request.headers.get("user-agent", ""),
                    ip=request.client.host if request.client else "",
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            set_session_cookie(response, token)
            return {"user": auth.user_payload(user)}

    @app.get("/auth/wechat/start")
    def wechat_start(next: str = "/") -> RedirectResponse:
        if not wechat.available():
            raise HTTPException(status_code=503, detail="微信登录未配置")
        return RedirectResponse(wechat.authorize_url(next))

    def _wechat_login(profile: dict, response: Response, next_path: str) -> RedirectResponse:
        with session_scope() as db:
            try:
                user = wechat.login_or_register(db, profile)
                token = auth.create_session(db, user)
                auth.purge_expired_sessions(db)
            except (ValueError, wechat.WeChatError) as exc:
                return RedirectResponse(
                    f"/login?reason=wechat&message={quote(str(exc))}", status_code=302
                )
        target = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
        redirect = RedirectResponse(target, status_code=302)
        set_session_cookie(redirect, token)
        return redirect

    @app.get("/auth/wechat/callback")
    def wechat_callback(
        request: Request, response: Response, code: str = "", state: str = ""
    ) -> RedirectResponse:
        try:
            next_path = wechat.verify_state_token(state)
        except wechat.WeChatError as exc:
            return RedirectResponse(
                f"/login?reason=wechat&message={quote(str(exc))}", status_code=302
            )
        if not code:
            return RedirectResponse(
                f"/login?reason=wechat&message={quote('未获得微信授权码')}", status_code=302
            )
        try:
            profile = wechat.exchange_code(code)
        except wechat.WeChatError as exc:
            return RedirectResponse(
                f"/login?reason=wechat&message={quote(str(exc))}", status_code=302
            )
        return _wechat_login(profile, response, next_path)

    @app.get("/auth/wechat/mock")
    def wechat_mock_login(
        response: Response, next: str = "/", openid: str = "mock-openid"
    ) -> RedirectResponse:
        if not settings.wechat_mock:
            raise HTTPException(status_code=404, detail="未开启微信模拟登录")
        return _wechat_login(wechat.mock_profile(openid), response, next)

    @app.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        with session_scope() as db:
            auth.revoke_session(db, request.cookies.get(auth.SESSION_COOKIE))
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"logged_out": True}

    @app.get("/auth/me")
    def me(request: Request) -> dict[str, Any]:
        return {"user": request.state.user}

    @app.post("/auth/password")
    def change_password(payload: PasswordChange, request: Request, response: Response):
        with session_scope() as db:
            user = db.get(User, request.state.user["id"])
            if user is None or not verify_password(payload.current_password, user.password_hash):
                raise HTTPException(status_code=400, detail="当前密码不正确")
            user.password_hash = hash_password(payload.new_password)
            auth.revoke_user_sessions(db, user.id)
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"changed": True, "relogin": True}

    # --------------------------------------------------------------- billing
    def billing_user(db) -> User:
        user_id = (current_user() or {}).get("id")
        user = db.get(User, user_id) if user_id else None
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return user

    def require_own_order(db, order_id: str) -> RechargeOrder:
        order = db.get(RechargeOrder, order_id)
        user = current_user() or {}
        if order is None or (
            order.user_id != user.get("id") and user.get("role") != auth.ROLE_SUPER_ADMIN
        ):
            raise HTTPException(status_code=404, detail="订单不存在")
        return order

    @app.get("/billing/summary")
    def billing_summary() -> dict[str, Any]:
        with session_scope() as db:
            user = billing_user(db)
            return {
                "user": auth.user_payload(user),
                "summary": billing.summary(db, user),
                "transactions": billing.transactions(db, user.id),
                "orders": billing.orders(db, user.id),
                "wechat_login": wechat.available(),
                "wechat_mock": settings.wechat_mock,
                "wechat_pay": wechatpay.available(),
                "wechat_pay_mock": settings.wechat_pay_mock,
            }

    @app.post("/billing/orders", status_code=201)
    def create_order(payload: OrderCreate) -> dict[str, Any]:
        with session_scope() as db:
            user = billing_user(db)
            package = billing.find_package(db, payload.package_id)
            if package is None:
                raise HTTPException(status_code=400, detail="充值套餐不存在")
            order = billing.create_order(db, user.id, package)
            if settings.wechat_pay_mock:
                order.provider = "mock"
                db.flush()
            elif wechatpay.available():
                try:
                    order.code_url = wechatpay.create_native(order)
                except wechatpay.PaymentError:
                    logger.exception("微信支付下单失败（订单 %s）", order.out_trade_no)
                    order.status = "closed"
                    db.flush()
                    raise HTTPException(
                        status_code=502, detail="微信支付下单失败，详情见服务日志"
                    ) from None
            else:
                raise HTTPException(status_code=503, detail="微信支付未配置")
            return billing.order_payload(order)

    @app.get("/billing/orders/{order_id}")
    def get_order(order_id: str) -> dict[str, Any]:
        with session_scope() as db:
            return billing.order_payload(require_own_order(db, order_id))

    @app.post("/billing/orders/{order_id}/mock-pay")
    def mock_pay(order_id: str) -> dict[str, Any]:
        if not settings.wechat_pay_mock:
            raise HTTPException(status_code=404, detail="未开启支付模拟")
        with session_scope() as db:
            order = require_own_order(db, order_id)
            billing.mark_paid(db, order, transaction_id=f"MOCK-{order.out_trade_no}", provider="mock")
            user = billing_user(db)
            return {
                "order": billing.order_payload(order),
                "user": auth.user_payload(user),
                "summary": billing.summary(db, user),
            }

    @app.get("/billing/orders/{order_id}/qr")
    def order_qr(order_id: str):
        with session_scope() as db:
            order = require_own_order(db, order_id)
            code_url = order.code_url
        if not code_url:
            raise HTTPException(status_code=404, detail="该订单没有支付二维码")
        try:
            import qrcode
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail="缺少 qrcode 依赖") from exc
        image = qrcode.make(code_url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return StreamingResponse(
            io.BytesIO(buffer.getvalue()),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/billing/redeem")
    def redeem(payload: RedeemRequest) -> dict[str, Any]:
        with session_scope() as db:
            user = billing_user(db)
            try:
                billing.redeem(db, user, payload.code)
            except billing.BillingError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "user": auth.user_payload(user),
                "summary": billing.summary(db, user),
                "transactions": billing.transactions(db, user.id),
            }

    @app.post("/billing/wechat/notify")
    async def wechat_notify(request: Request):
        body = await request.body()
        try:
            data = wechatpay.parse_notification(dict(request.headers), body)
        except wechatpay.PaymentError as exc:
            return JSONResponse(
                status_code=400, content={"code": "FAIL", "message": str(exc)}
            )
        with session_scope() as db:
            order = db.scalar(
                select(RechargeOrder).where(
                    RechargeOrder.out_trade_no == data["out_trade_no"]
                )
            )
            if order is None:
                return JSONResponse(
                    status_code=404, content={"code": "FAIL", "message": "订单不存在"}
                )
            amount = (data.get("amount") or {}).get("total")
            if amount is not None and int(amount) != order.amount_cents:
                return JSONResponse(
                    status_code=400, content={"code": "FAIL", "message": "金额不一致"}
                )
            if data.get("trade_state") == "SUCCESS":
                billing.mark_paid(
                    db,
                    order,
                    transaction_id=data.get("transaction_id", ""),
                    provider="wechat",
                )
        return {"code": "SUCCESS", "message": "成功"}

    # ---------------------------------------------------------------- admin
    def require_super(request: Request) -> None:
        if request.state.user.get("role") != auth.ROLE_SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="仅超级管理员可访问")

    def package_audit(action: str, name: str) -> None:
        with session_scope() as db:
            audit(db, None, action, {"name": name})

    def validate_kind(kind: str, provider: str) -> None:
        if kind not in {"chat", "image", "embedding"}:
            raise HTTPException(status_code=400, detail="kind 只能是 chat/image/embedding")
        if provider not in {"openai", "ollama", "custom"}:
            raise HTTPException(status_code=400, detail="provider 只能是 openai/ollama/custom")
        if provider == "ollama" and kind != "chat":
            raise HTTPException(status_code=400, detail="Ollama 目前仅支持对话模型")

    def validate_kb(kind: str, provider: str) -> None:
        if kind not in {"local", "api"}:
            raise HTTPException(status_code=400, detail="kind 只能是 local/api")
        allowed = {"local"} if kind == "local" else {"generic", "dify", "ragflow"}
        if provider not in allowed:
            raise HTTPException(status_code=400, detail=f"{kind} 知识库 provider 不合法")

    def unset_default_model(db, kind: str) -> None:
        for row in db.scalars(select(ModelConfig).where(ModelConfig.kind == kind)):
            row.is_default = False

    def unset_default_kb(db) -> None:
        for row in db.scalars(select(KnowledgeBase)):
            row.is_default = False

    @app.get("/admin/models")
    def list_models(request: Request, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        require_super(request)
        capped, start = _page_bounds(limit, offset)
        with session_scope() as db:
            rows = list(
                db.scalars(
                    select(ModelConfig)
                    .order_by(ModelConfig.kind, ModelConfig.name)
                    .limit(capped)
                    .offset(start)
                )
            )
            return [model_config.model_payload(row) for row in rows]

    @app.post("/admin/models", status_code=201)
    def create_model(payload: ModelCreate, request: Request) -> dict[str, Any]:
        require_super(request)
        validate_kind(payload.kind, payload.provider)
        if payload.is_default and not payload.enabled:
            raise HTTPException(status_code=400, detail="默认模型必须启用")
        with session_scope() as db:
            if db.scalar(select(ModelConfig).where(ModelConfig.name == payload.name)):
                raise HTTPException(status_code=400, detail="模型名称已存在")
            if payload.is_default:
                unset_default_model(db, payload.kind)
            row = ModelConfig(
                name=payload.name,
                kind=payload.kind,
                provider=payload.provider,
                base_url=payload.base_url,
                model=payload.model,
                api_key_enc=encrypt_secret(payload.api_key),
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
                options=payload.options,
                enabled=payload.enabled,
                is_default=payload.is_default,
            )
            db.add(row)
            db.flush()
            return model_config.model_payload(row)

    @app.patch("/admin/models/{model_id}")
    def update_model(model_id: str, payload: ModelUpdate, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(ModelConfig, model_id)
            if row is None:
                raise HTTPException(status_code=404, detail="模型不存在")
            data = payload.model_dump(exclude_unset=True)
            clear_key = data.pop("clear_api_key", False)
            api_key = data.pop("api_key", None)
            if "kind" in data or "provider" in data:
                validate_kind(data.get("kind", row.kind), data.get("provider", row.provider))
            if (
                "name" in data
                and data["name"] != row.name
                and db.scalar(select(ModelConfig).where(ModelConfig.name == data["name"]))
            ):
                raise HTTPException(status_code=400, detail="模型名称已存在")
            if data.get("is_default") and not data.get("enabled", row.enabled):
                raise HTTPException(status_code=400, detail="默认模型必须启用")
            if data.get("is_default"):
                unset_default_model(db, data.get("kind", row.kind))
            if clear_key:
                row.api_key_enc = ""
            elif api_key is not None and api_key != "":
                row.api_key_enc = encrypt_secret(api_key)
            for key, value in data.items():
                setattr(row, key, value)
            if row.is_default:
                unset_default_model(db, row.kind)
                row.is_default = True
            db.flush()
            return model_config.model_payload(row)

    @app.delete("/admin/models/{model_id}")
    def delete_model(model_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(ModelConfig, model_id)
            if row is None:
                raise HTTPException(status_code=404, detail="模型不存在")
            db.delete(row)
            return {"deleted": True, "id": model_id}

    @app.post("/admin/models/{model_id}/test")
    def test_model(model_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(ModelConfig, model_id)
            if row is None:
                raise HTTPException(status_code=404, detail="模型不存在")
            ok, message, latency = model_config.test_model(row)
            row.last_test_at = datetime.now(UTC).replace(tzinfo=None)
            row.last_test_ok = ok
            row.last_test_message = message[:1000]
            return {"ok": ok, "message": message, "latency_ms": latency}

    @app.get("/admin/kbs")
    def list_kbs(request: Request, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        require_super(request)
        capped, start = _page_bounds(limit, offset)
        with session_scope() as db:
            rows = list(
                db.scalars(
                    select(KnowledgeBase)
                    .order_by(KnowledgeBase.name)
                    .limit(capped)
                    .offset(start)
                )
            )
            return [knowledge.kb_payload(row, db) for row in rows]

    @app.post("/admin/kbs", status_code=201)
    def create_kb(payload: KbCreate, request: Request) -> dict[str, Any]:
        require_super(request)
        validate_kb(payload.kind, payload.provider)
        if payload.is_default and not payload.enabled:
            raise HTTPException(status_code=400, detail="默认知识库必须启用")
        with session_scope() as db:
            if db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == payload.name)):
                raise HTTPException(status_code=400, detail="知识库名称已存在")
            if payload.is_default:
                unset_default_kb(db)
            row = KnowledgeBase(
                name=payload.name,
                kind=payload.kind,
                provider=payload.provider,
                location=payload.location,
                api_key_enc=encrypt_secret(payload.api_key),
                dataset_id=payload.dataset_id,
                options=payload.options,
                enabled=payload.enabled,
                is_default=payload.is_default,
            )
            db.add(row)
            db.flush()
            return knowledge.kb_payload(row, db)

    @app.patch("/admin/kbs/{kb_id}")
    def update_kb(kb_id: str, payload: KbUpdate, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(KnowledgeBase, kb_id)
            if row is None:
                raise HTTPException(status_code=404, detail="知识库不存在")
            data = payload.model_dump(exclude_unset=True)
            clear_key = data.pop("clear_api_key", False)
            api_key = data.pop("api_key", None)
            if "kind" in data or "provider" in data:
                validate_kb(data.get("kind", row.kind), data.get("provider", row.provider))
            if data.get("kind") == "api" and row.kind == "local":
                db.execute(delete(KbChunk).where(KbChunk.kb_id == row.id))
                row.chunk_count = 0
                row.indexed_at = None
            if (
                "location" in data
                and data["location"] != row.location
                and row.kind == "local"
            ):
                db.execute(delete(KbChunk).where(KbChunk.kb_id == row.id))
                row.chunk_count = 0
                row.indexed_at = None
            if data.get("is_default") and not data.get("enabled", row.enabled):
                raise HTTPException(status_code=400, detail="默认知识库必须启用")
            if data.get("is_default"):
                unset_default_kb(db)
            if clear_key:
                row.api_key_enc = ""
            elif api_key is not None and api_key != "":
                row.api_key_enc = encrypt_secret(api_key)
            for key, value in data.items():
                setattr(row, key, value)
            if row.is_default:
                unset_default_kb(db)
                row.is_default = True
            db.flush()
            return knowledge.kb_payload(row, db)

    @app.delete("/admin/kbs/{kb_id}")
    def delete_kb(kb_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(KnowledgeBase, kb_id)
            if row is None:
                raise HTTPException(status_code=404, detail="知识库不存在")
            db.execute(delete(KbChunk).where(KbChunk.kb_id == kb_id))
            db.delete(row)
            return {"deleted": True, "id": kb_id}

    @app.post("/admin/kbs/{kb_id}/test")
    def test_kb(kb_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(KnowledgeBase, kb_id)
            if row is None:
                raise HTTPException(status_code=404, detail="知识库不存在")
            ok, message, latency = knowledge.test_kb(db, row)
            row.last_test_at = datetime.now(UTC).replace(tzinfo=None)
            row.last_test_ok = ok
            row.last_test_message = message[:1000]
            return {"ok": ok, "message": message, "latency_ms": latency}

    @app.post("/admin/kbs/{kb_id}/reindex", status_code=202)
    def reindex_kb(kb_id: str, request: Request) -> dict[str, Any]:
        """Queue a background rebuild of a local KB index and its vectors."""

        require_super(request)
        user = current_user() or {}
        with session_scope() as db:
            row = db.get(KnowledgeBase, kb_id)
            if row is None:
                raise HTTPException(status_code=404, detail="知识库不存在")
            if row.kind != "local":
                raise HTTPException(status_code=400, detail="只有本地知识库需要建索引")
            if _active_kb_reindex(db, kb_id) is not None:
                raise HTTPException(status_code=409, detail="该知识库已有重建索引任务在运行")
            job = Job(
                project_id=None,
                kind="kb_reindex",
                status="queued",
                message="知识库重建索引排队中…",
                payload={
                    "owner_id": user.get("id") or "",
                    "kb_id": kb_id,
                    "name": row.name,
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id
            jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
            audit(db, None, "kb.reindex", {"kb_id": kb_id, "job_id": job_id})
        worker = threading.Thread(
            target=_run_kb_reindex_job,
            args=(job_id, kb_id),
            daemon=True,
            name=f"bidcraft-kb-reindex-{job_id[:8]}",
        )
        worker.start()
        return {"job_id": job_id}

    @app.post("/admin/kbs/{kb_id}/embed")
    def embed_kb(kb_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(KnowledgeBase, kb_id)
            if row is None:
                raise HTTPException(status_code=404, detail="知识库不存在")
            if row.kind != "local":
                raise HTTPException(status_code=400, detail="仅本地知识库支持向量索引")
            if not row.chunk_count:
                raise HTTPException(status_code=400, detail="请先重建索引")
            if model_config.resolve_embedding_endpoint(db) is None:
                raise HTTPException(
                    status_code=400,
                    detail="未配置可用的向量模型；请在模型配置中添加 embedding 模型",
                )
            result = knowledge.build_vectors(db, row)
            return {**result, "kb": knowledge.kb_payload(row, db)}

    # --------------------------------------------------------------- plugins
    def plugin_name(value: str | None) -> str:
        name = (value or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="插件名称不能为空")
        if len(name) > 60:
            raise HTTPException(status_code=400, detail="插件名称不能超过 60 个字符")
        if "__" in name:
            raise HTTPException(
                status_code=400, detail="插件名称不能包含双下划线（用于工具名前缀分隔）"
            )
        return name

    def plugin_transport(value: str | None, *, default: str = "stdio") -> str:
        transport = (value or default).strip().lower()
        if transport not in {"stdio", "http"}:
            raise HTTPException(status_code=400, detail="transport 只能是 stdio/http")
        return transport

    def plugin_validate(transport: str, command: str, url: str) -> None:
        if transport == "stdio" and not command:
            raise HTTPException(status_code=400, detail="stdio 插件必须填写 command")
        if transport == "http":
            if not url:
                raise HTTPException(status_code=400, detail="http 插件必须填写 url")
            if not url.startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400, detail="插件 url 必须以 http:// 或 https:// 开头"
                )

    def plugin_items(value, *, field: str, limits: int = 64) -> list[str]:
        """接受数组、JSON 文本或逗号分隔字符串，去空去重后返回。"""
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = [part.strip() for part in text.split(",")]
            value = decoded
        if not isinstance(value, list):
            raise HTTPException(
                status_code=400, detail=f"{field} 必须是数组、JSON 文本或逗号分隔字符串"
            )
        items: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text and text not in items:
                items.append(text[:200])
        return items[:limits]

    def plugin_env(value, *, field: str = "env") -> dict[str, str]:
        if value is None:
            return {}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=400, detail=f"{field} 必须是 JSON 对象"
                ) from exc
            value = decoded
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail=f"{field} 必须是 JSON 对象")
        env: dict[str, str] = {}
        for key, item in value.items():
            name = str(key).strip()[:80]
            if name:
                env[name] = str(item)[:2000]
        return env

    @app.get("/admin/plugins/catalog")
    def plugin_catalog(request: Request) -> list[dict[str, Any]]:
        require_super(request)
        return [dict(item) for item in plugins.PLUGIN_CATALOG]

    @app.get("/admin/plugins")
    def list_plugins(
        request: Request, limit: int = 500, offset: int = 0
    ) -> list[dict[str, Any]]:
        require_super(request)
        capped, start = _page_bounds(limit, offset)
        with session_scope() as db:
            rows = list(
                db.scalars(
                    select(Plugin).order_by(Plugin.name).limit(capped).offset(start)
                )
            )
            return [plugins.plugin_payload(row) for row in rows]

    @app.post("/admin/plugins", status_code=201)
    def create_plugin(payload: PluginCreate, request: Request) -> dict[str, Any]:
        require_super(request)
        name = plugin_name(payload.name)
        transport = plugin_transport(payload.transport)
        command = (payload.command or "").strip()[:500]
        url = (payload.url or "").strip()[:500]
        plugin_validate(transport, command, url)
        args = plugin_items(payload.args, field="args")
        env = plugin_env(payload.env)
        allowlist = plugin_items(payload.tool_allowlist, field="tool_allowlist", limits=200)
        with session_scope() as db:
            if db.scalar(select(Plugin).where(Plugin.name == name)):
                raise HTTPException(status_code=409, detail="插件名称已存在")
            row = Plugin(
                name=name,
                transport=transport,
                command=command,
                args=args,
                env=env,
                url=url,
                enabled=bool(payload.enabled),
                tool_allowlist=allowlist,
            )
            db.add(row)
            db.flush()
            audit(
                db,
                None,
                "plugin.create",
                {"plugin_id": row.id, "name": name, "transport": transport},
            )
            return plugins.plugin_payload(row)

    @app.patch("/admin/plugins/{plugin_id}")
    def update_plugin(
        plugin_id: str, payload: PluginUpdate, request: Request
    ) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(Plugin, plugin_id)
            if row is None:
                raise HTTPException(status_code=404, detail="插件不存在")
            data = payload.model_dump(exclude_unset=True)
            changed: list[str] = []
            if data.get("name") is not None:
                name = plugin_name(data["name"])
                if name != row.name and db.scalar(select(Plugin).where(Plugin.name == name)):
                    raise HTTPException(status_code=409, detail="插件名称已存在")
                row.name = name
                changed.append("name")
            if data.get("transport") is not None:
                row.transport = plugin_transport(data["transport"])
                changed.append("transport")
            if data.get("command") is not None:
                row.command = str(data["command"]).strip()[:500]
                changed.append("command")
            if data.get("url") is not None:
                row.url = str(data["url"]).strip()[:500]
                changed.append("url")
            if "args" in data:
                row.args = plugin_items(data["args"], field="args")
                changed.append("args")
            if "env" in data:
                row.env = plugin_env(data["env"])
                changed.append("env")
            if "tool_allowlist" in data:
                row.tool_allowlist = plugin_items(
                    data["tool_allowlist"], field="tool_allowlist", limits=200
                )
                changed.append("tool_allowlist")
            if data.get("enabled") is not None:
                row.enabled = bool(data["enabled"])
                changed.append("enabled")
                if row.enabled:
                    plugins.reset_guard(db, row)
            plugin_validate(row.transport, row.command or "", row.url or "")
            db.flush()
            audit(
                db,
                None,
                "plugin.update",
                {"plugin_id": row.id, "name": row.name, "changed": sorted(changed)},
            )
            return plugins.plugin_payload(row)

    @app.delete("/admin/plugins/{plugin_id}")
    def delete_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(Plugin, plugin_id)
            if row is None:
                raise HTTPException(status_code=404, detail="插件不存在")
            audit(db, None, "plugin.delete", {"plugin_id": row.id, "name": row.name})
            db.delete(row)
            db.flush()
            return {"deleted": True, "id": plugin_id}

    @app.post("/admin/plugins/{plugin_id}/test")
    def test_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(Plugin, plugin_id)
            if row is None:
                raise HTTPException(status_code=404, detail="插件不存在")
            ref = plugins.snapshot(row)
            name = row.name
        ok, message, tools = plugins.test_plugin(ref)
        with session_scope() as db:
            row = db.get(Plugin, plugin_id)
            if row is not None:
                row.last_test_at = datetime.now(UTC).replace(tzinfo=None)
                row.last_test_ok = ok
                row.last_test_message = message[:1000]
                plugins.record_outcome(db, row, ok)
                audit(
                    db,
                    None,
                    "plugin.test",
                    {
                        "plugin_id": plugin_id,
                        "name": name,
                        "ok": ok,
                        "tool_count": len(tools),
                    },
                )
        return {"ok": ok, "message": message, "tools": tools}

    @app.get("/admin/billing")
    def admin_billing(request: Request, limit: int = 50) -> dict[str, Any]:
        require_super(request)
        capped = max(1, min(200, int(limit)))
        with session_scope() as db:
            settings_payload = billing.billing_settings(db)
            orders = list(
                db.scalars(
                    select(RechargeOrder)
                    .order_by(RechargeOrder.created_at.desc())
                    .limit(capped)
                )
            )
            codes = list(
                db.scalars(
                    select(RedeemCode).order_by(RedeemCode.created_at.desc()).limit(capped)
                )
            )
            return {
                "settings": settings_payload,
                "stats": billing.admin_stats(db),
                "orders": [billing.order_payload(order) for order in orders],
                "codes": [
                    {
                        "id": code.id,
                        "code": code.code,
                        "credits": code.credits,
                        "amount_cents": code.amount_cents,
                        "status": code.status,
                        "batch": code.batch,
                        "used_by": code.used_by,
                        "used_at": code.used_at.isoformat() if code.used_at else None,
                        "expires_at": code.expires_at.isoformat() if code.expires_at else None,
                        "created_at": code.created_at.isoformat() if code.created_at else None,
                    }
                    for code in codes
                ],
            }

    @app.put("/admin/billing")
    def save_admin_billing(payload: BillingSettingsPayload, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            try:
                saved = billing.save_billing_settings(db, payload.model_dump())
            except billing.BillingError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"settings": saved, "stats": billing.admin_stats(db)}

    @app.post("/admin/redeem-codes", status_code=201)
    def generate_codes(payload: RedeemGenerate, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            try:
                codes = billing.generate_redeem_codes(
                    db,
                    count=payload.count,
                    credits=payload.credits,
                    amount_cents=payload.amount_cents,
                    days=payload.days,
                    created_by=request.state.user["id"],
                )
            except billing.BillingError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"codes": codes}

    @app.post("/admin/users/{user_id}/credits")
    def adjust_credits(user_id: str, payload: CreditAdjust, request: Request) -> dict[str, Any]:
        require_super(request)
        if payload.amount == 0:
            raise HTTPException(status_code=400, detail="调整数量不能为 0")
        with session_scope() as db:
            user = db.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="用户不存在")
            try:
                billing.add_credits(
                    db,
                    user.id,
                    payload.amount,
                    kind="admin",
                    note=payload.note or f"管理员调整 {payload.amount:+d}",
                    ref_type="admin",
                    ref_id=request.state.user["id"],
                )
            except billing.InsufficientCredits as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "user": auth.user_payload(user),
                "transactions": billing.transactions(db, user.id),
            }

    @app.get("/admin/users")
    def list_users(
        request: Request, limit: int = 500, offset: int = 0
    ) -> list[dict[str, Any]]:
        require_super(request)
        capped, start = _page_bounds(limit, offset)
        with session_scope() as db:
            rows = list(
                db.scalars(
                    select(User)
                    .order_by(User.created_at)
                    .limit(capped)
                    .offset(start)
                )
            )
            return [auth.user_payload(row) for row in rows]

    @app.post("/admin/users", status_code=201)
    def create_user(payload: UserCreate, request: Request) -> dict[str, Any]:
        require_super(request)
        if payload.role not in auth.ROLES:
            raise HTTPException(status_code=400, detail="角色不合法")
        with session_scope() as db:
            if db.scalar(select(User).where(User.username == payload.username)):
                raise HTTPException(status_code=400, detail="用户名已存在")
            row = User(
                username=payload.username,
                display_name=payload.display_name or payload.username,
                password_hash=hash_password(payload.password),
                role=payload.role,
                active=True,
            )
            db.add(row)
            db.flush()
            return auth.user_payload(row)

    @app.patch("/admin/users/{user_id}")
    def update_user(user_id: str, payload: UserUpdate, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(User, user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="用户不存在")
            data = payload.model_dump(exclude_unset=True)
            password = data.pop("password", None)
            if data.get("role") is not None and data["role"] not in auth.ROLES:
                raise HTTPException(status_code=400, detail="角色不合法")
            if user_id == request.state.user["id"]:
                if data.get("active") is False:
                    raise HTTPException(status_code=400, detail="不能停用当前登录账号")
                if data.get("role") and data["role"] != auth.ROLE_SUPER_ADMIN:
                    raise HTTPException(status_code=400, detail="不能降低当前账号权限")
            if row.role == auth.ROLE_SUPER_ADMIN and (
                data.get("role") not in {None, auth.ROLE_SUPER_ADMIN}
                or data.get("active") is False
            ):
                others = db.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(
                        User.role == auth.ROLE_SUPER_ADMIN,
                        User.active.is_(True),
                        User.id != row.id,
                    )
                )
                if not others:
                    raise HTTPException(status_code=400, detail="至少保留一个启用的超级管理员")
            if password:
                row.password_hash = hash_password(password)
                auth.revoke_user_sessions(db, row.id)
            for key, value in data.items():
                setattr(row, key, value)
            db.flush()
            return auth.user_payload(row)

    @app.delete("/admin/users/{user_id}")
    def delete_user(user_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        if user_id == request.state.user["id"]:
            raise HTTPException(status_code=400, detail="不能删除当前登录账号")
        with session_scope() as db:
            row = db.get(User, user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="用户不存在")
            if row.role == auth.ROLE_SUPER_ADMIN:
                others = db.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(
                        User.role == auth.ROLE_SUPER_ADMIN,
                        User.active.is_(True),
                        User.id != row.id,
                    )
                )
                if not others:
                    raise HTTPException(status_code=400, detail="至少保留一个启用的超级管理员")
            db.execute(delete(AuthSession).where(AuthSession.user_id == row.id))
            db.delete(row)
            return {"deleted": True, "id": user_id}

    # -------------------------------------------------------- rule packages
    def rule_package_record(name: str, document: str | None = None) -> dict[str, Any]:
        with session_scope() as db:
            usage = _package_usage(db)
        payload = _rule_package_admin_payload(name, usage, document)
        if payload is None:  # pragma: no cover - defensive
            raise HTTPException(status_code=404, detail="规则包不存在")
        return payload

    def check_package_name(name: str) -> str:
        try:
            return package_store.validate_name(name)
        except package_store.StoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def check_package_document(document: str, name: str = "") -> None:
        result = validate_package_text(document, name)
        if not result["ok"]:
            detail = "；".join(str(problem) for problem in result["problems"])
            raise HTTPException(status_code=400, detail=detail or "规则包校验失败")

    @app.get("/admin/rule-packages")
    def admin_list_rule_packages(
        request: Request, include_hidden: bool = False
    ) -> list[dict[str, Any]]:
        require_super(request)
        with session_scope() as db:
            usage = _package_usage(db)
        return [
            {**item, "usage": usage.get(str(item["name"]), 0)}
            for item in list_packages(include_hidden=include_hidden)
        ]

    @app.post("/admin/rule-packages/validate")
    def admin_validate_rule_package(
        payload: RulePackageValidateRequest, request: Request
    ) -> dict[str, Any]:
        require_super(request)
        return validate_package_text(payload.document, payload.name)

    @app.get("/admin/rule-packages/{name}")
    def admin_get_rule_package(name: str, request: Request) -> dict[str, Any]:
        require_super(request)
        name = check_package_name(name)
        if not package_store.package_exists(name):
            raise HTTPException(status_code=404, detail="规则包不存在")
        try:
            document = package_store.read_document(name)
        except package_store.StoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return rule_package_record(name, document)

    @app.post("/admin/rule-packages", status_code=201)
    def admin_create_rule_package(
        payload: RulePackageDocument, request: Request
    ) -> dict[str, Any]:
        require_super(request)
        name = document_package_name(payload.document)
        if not name:
            check_package_document(payload.document)
            raise HTTPException(status_code=400, detail="YAML 缺少 package 字段")
        name = check_package_name(name)
        if package_store.package_exists(name):
            raise HTTPException(status_code=409, detail=f"规则包已存在: {name}")
        check_package_document(payload.document, name)
        package_store.save_user_package(name, payload.document)
        package_store.unhide_package(name)
        package_audit("package.create", name)
        return rule_package_record(name, payload.document)

    @app.put("/admin/rule-packages/{name}")
    def admin_update_rule_package(
        name: str, payload: RulePackageDocument, request: Request
    ) -> dict[str, Any]:
        require_super(request)
        name = check_package_name(name)
        if not package_store.package_exists(name):
            raise HTTPException(status_code=404, detail="规则包不存在")
        check_package_document(payload.document, name)
        package_store.save_user_package(name, payload.document)
        package_store.unhide_package(name)
        package_audit("package.update", name)
        return rule_package_record(name, payload.document)

    @app.delete("/admin/rule-packages/{name}")
    def admin_delete_rule_package(name: str, request: Request) -> dict[str, Any]:
        require_super(request)
        name = check_package_name(name)
        if not package_store.package_exists(name):
            raise HTTPException(status_code=404, detail="规则包不存在")
        with session_scope() as db:
            usage = _package_usage(db).get(name, 0)
        if usage:
            raise HTTPException(
                status_code=409, detail=f"仍有 {usage} 个项目使用该规则包"
            )
        package_store.hide_package(name)
        package_audit("package.delete", name)
        return {"name": name, "hidden": True}

    @app.post("/admin/rule-packages/{name}/restore")
    def admin_restore_rule_package(
        name: str, request: Request, reset_override: bool = False
    ) -> dict[str, Any]:
        require_super(request)
        name = check_package_name(name)
        if not package_store.package_exists(name):
            raise HTTPException(status_code=404, detail="规则包不存在")
        if reset_override and not package_store.is_builtin(name):
            raise HTTPException(status_code=400, detail="仅内置规则包可恢复内置原文")
        if reset_override:
            package_store.remove_user_package(name)
        package_store.unhide_package(name)
        try:
            document = package_store.read_document(name)
        except package_store.StoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        package_audit("package.restore", name)
        return rule_package_record(name, document)

    # ---------------------------------------------------------------- quotas
    def quota_name(value: str | None) -> str:
        name = (value or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="定额名称不能为空")
        if len(name) > 120:
            raise HTTPException(status_code=400, detail="定额名称不能超过 120 个字符")
        return name

    def quota_price(value: float | None) -> float:
        try:
            price = float(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="定额单价必须是数字") from exc
        if not math.isfinite(price) or price < 0:
            raise HTTPException(status_code=400, detail="定额单价必须是非负有限数字")
        return round(price, 4)

    @app.get("/admin/quotas")
    def list_quotas(
        request: Request, search: str = "", limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        require_super(request)
        term = (search or "").strip()
        with session_scope() as db:
            statement = select(QuotaItem)
            if term:
                pattern = f"%{term}%"
                statement = statement.where(
                    QuotaItem.name.ilike(pattern)
                    | QuotaItem.spec.ilike(pattern)
                    | QuotaItem.category.ilike(pattern)
                )
            capped = max(1, min(int(limit), 500))
            start = max(0, int(offset))
            rows = list(
                db.scalars(
                    statement.order_by(QuotaItem.name, QuotaItem.id)
                    .limit(capped)
                    .offset(start)
                )
            )
            return [boq.quota_payload(row) for row in rows]

    @app.get("/admin/quotas/match")
    def match_quota_candidates(
        request: Request, name: str = "", spec: str = ""
    ) -> list[dict[str, Any]]:
        require_super(request)
        with session_scope() as db:
            quotas = list(
                db.scalars(select(QuotaItem).order_by(QuotaItem.name, QuotaItem.id))
            )
            item = {"name": name, "spec": spec}
            return [
                {
                    "id": quota.id,
                    "name": quota.name,
                    "spec": quota.spec or "",
                    "unit": quota.unit or "",
                    "unit_price": round(float(quota.unit_price or 0.0), 4),
                    "score": round(float(score), 4),
                }
                for score, quota in boq.match_quotas(item, quotas, limit=5)
            ]

    @app.post("/admin/quotas", status_code=201)
    def create_quota(payload: QuotaCreate, request: Request) -> dict[str, Any]:
        require_super(request)
        name = quota_name(payload.name)
        price = quota_price(payload.unit_price)
        with session_scope() as db:
            quota = QuotaItem(
                name=name,
                spec=(payload.spec or "").strip()[:200],
                unit=(payload.unit or "").strip()[:20],
                unit_price=price,
                category=(payload.category or "").strip()[:40],
                note=(payload.note or "").strip()[:200],
            )
            db.add(quota)
            db.flush()
            audit(db, None, "quota.create", {"quota_id": quota.id, "name": name})
            return boq.quota_payload(quota)

    @app.patch("/admin/quotas/{quota_id}")
    def update_quota(
        quota_id: str, payload: QuotaUpdate, request: Request
    ) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            quota = db.get(QuotaItem, quota_id)
            if quota is None:
                raise LookupError(f"定额不存在：{quota_id}")
            changed: dict[str, Any] = {}
            if payload.name is not None:
                quota.name = quota_name(payload.name)
                changed["name"] = quota.name
            if payload.unit_price is not None:
                quota.unit_price = quota_price(payload.unit_price)
                changed["unit_price"] = quota.unit_price
            for field, limit in (
                ("spec", 200),
                ("unit", 20),
                ("category", 40),
                ("note", 200),
            ):
                value = getattr(payload, field)
                if value is not None:
                    setattr(quota, field, value.strip()[:limit])
                    changed[field] = getattr(quota, field)
            db.flush()
            audit(
                db,
                None,
                "quota.update",
                {"quota_id": quota.id, "name": quota.name, "changed": sorted(changed)},
            )
            return boq.quota_payload(quota)

    @app.delete("/admin/quotas/{quota_id}")
    def delete_quota(quota_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            quota = db.get(QuotaItem, quota_id)
            if quota is None:
                raise LookupError(f"定额不存在：{quota_id}")
            audit(db, None, "quota.delete", {"quota_id": quota.id, "name": quota.name})
            db.delete(quota)
            db.flush()
            return {"deleted": True, "id": quota_id}

    # ----------------------------------------------------------------- terms
    @app.get("/admin/terms")
    def list_terms(
        request: Request,
        search: str = "",
        kind: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        require_super(request)
        capped, start = _page_bounds(limit, offset)
        with session_scope() as db:
            try:
                rows = terms.list_terms(
                    db, search=search, kind=kind, limit=capped, offset=start
                )
            except terms.TermError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return [terms.term_payload(row) for row in rows]

    @app.post("/admin/terms", status_code=201)
    def create_term(payload: TermCreate, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            try:
                row = terms.create_term(
                    db,
                    kind=payload.kind,
                    source=payload.source,
                    target=payload.target,
                    note=payload.note,
                    enabled=payload.enabled,
                )
            except terms.TermExists as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except terms.TermError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            audit(
                db,
                None,
                "term.create",
                {"term_id": row.id, "kind": row.kind, "source": row.source},
            )
            return terms.term_payload(row)

    @app.patch("/admin/terms/{term_id}")
    def update_term(term_id: str, payload: TermUpdate, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(TermItem, term_id)
            if row is None:
                raise LookupError(f"术语不存在：{term_id}")
            changes = {
                field: getattr(payload, field)
                for field in ("kind", "source", "target", "note", "enabled")
                if getattr(payload, field) is not None
            }
            try:
                terms.update_term(db, row, **changes)
            except terms.TermExists as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except terms.TermError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            audit(
                db,
                None,
                "term.update",
                {
                    "term_id": row.id,
                    "kind": row.kind,
                    "source": row.source,
                    "changed": sorted(changes),
                },
            )
            return terms.term_payload(row)

    @app.delete("/admin/terms/{term_id}")
    def delete_term(term_id: str, request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            row = db.get(TermItem, term_id)
            if row is None:
                raise LookupError(f"术语不存在：{term_id}")
            audit(
                db,
                None,
                "term.delete",
                {"term_id": row.id, "kind": row.kind, "source": row.source},
            )
            terms.delete_term(db, row)
            return {"deleted": True, "id": term_id}

    # ------------------------------------------------------ wechat config
    class WechatConfigUpdate(BaseModel):
        app_id: str | None = None
        app_secret: str | None = None
        clear_secret: bool = False
        redirect_uri: str | None = None
        enabled: bool | None = None
        mock: bool | None = None

    def wechat_config_payload(db) -> dict[str, Any]:
        from .models import WechatConfig

        row = db.get(WechatConfig, "default")
        cfg = wechat.effective(db)
        return {
            "app_id": (row.app_id if row else settings.wechat_app_id) or "",
            "has_secret": bool(row.app_secret_enc) if row else bool(settings.wechat_app_secret),
            "redirect_uri": (row.redirect_uri if row else settings.wechat_redirect_uri) or "",
            "enabled": bool(row.enabled) if row else False,
            "mock": bool(cfg["mock"]),
            "available": wechat.available(db),
            "source": cfg["source"],
        }

    @app.get("/admin/wechat")
    def admin_get_wechat(request: Request) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            return wechat_config_payload(db)

    @app.put("/admin/wechat")
    def admin_update_wechat(payload: WechatConfigUpdate, request: Request) -> dict[str, Any]:
        require_super(request)
        from .models import WechatConfig
        from .security import encrypt_secret

        with session_scope() as db:
            row = db.get(WechatConfig, "default")
            if row is None:
                row = WechatConfig(id="default")
                db.add(row)
            changed: list[str] = []
            if payload.app_id is not None:
                row.app_id = payload.app_id.strip()[:120]
                changed.append("app_id")
            if payload.clear_secret:
                row.app_secret_enc = ""
                changed.append("clear_secret")
            elif payload.app_secret:
                row.app_secret_enc = encrypt_secret(payload.app_secret)
                changed.append("app_secret")
            if payload.redirect_uri is not None:
                row.redirect_uri = payload.redirect_uri.strip()[:500]
                changed.append("redirect_uri")
            if payload.enabled is not None:
                row.enabled = bool(payload.enabled)
                changed.append("enabled")
            if payload.mock is not None:
                row.mock = bool(payload.mock)
                changed.append("mock")
            if not changed:
                raise HTTPException(status_code=400, detail="没有需要更新的字段")
            db.flush()
            audit(db, None, "wechat.config", {"keys": changed})
            return wechat_config_payload(db)

    @app.post("/admin/wechat/test")
    def admin_test_wechat(request: Request) -> dict[str, Any]:
        require_super(request)
        import httpx

        with session_scope() as db:
            cfg = wechat.effective(db)
        if cfg["mock"]:
            return {"ok": True, "message": "模拟模式可用：登录会生成本地测试账号，不访问微信服务器"}
        if not (cfg["app_id"] and cfg["app_secret"] and cfg["redirect_uri"]):
            return {"ok": False, "message": "未配置完整的 AppID / AppSecret / 回调地址"}
        try:
            response = httpx.get(
                wechat.AUTHORIZE_URL,
                params={"appid": cfg["app_id"], "redirect_uri": cfg["redirect_uri"]},
                timeout=wechat.TIMEOUT,
            )
        except httpx.HTTPError:
            logger.exception("微信开放平台连通性测试失败")
            return {"ok": False, "message": "无法连接微信开放平台，请检查网络或稍后重试"}
        if response.status_code >= 500:
            return {"ok": False, "message": f"微信开放平台返回 {response.status_code}，请稍后重试"}
        return {"ok": True, "message": "配置完整，微信开放平台可达；请用真实扫码验证回调"}

    # ---------------------------------------------------------------- meta
    @app.get("/health")
    def health() -> dict[str, Any]:
        with session_scope() as db:
            endpoint = model_config.resolve_chat_endpoint(db)
            embedding = model_config.resolve_embedding_endpoint(db)
            models = db.scalar(select(func.count()).select_from(ModelConfig)) or 0
            kbs = db.scalar(select(func.count()).select_from(KnowledgeBase)) or 0
        return {
            "version": __version__,
            "model_configured": bool(endpoint),
            "model_label": endpoint.label if endpoint else "",
            "embedding_configured": bool(embedding),
            "embedding_model": embedding.model_name if embedding else "",
            "offline": settings.offline,
            "models": models,
            "knowledge_bases": kbs,
            "auth_required": True,
            "registration": True,
            "wechat_login": wechat.available(),
            "wechat_mock": settings.wechat_mock,
            "wechat_pay": wechatpay.available(),
            "wechat_pay_mock": settings.wechat_pay_mock,
            "home": str(settings.home),
            "web_ready": (Path(__file__).resolve().parent / "web_dist" / "index.html").is_file(),
        }

    @app.get("/packages")
    def packages(limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        """Selectable industry packages (the abstract base package is hidden)."""
        capped, start = _page_bounds(limit, offset)
        return [item for item in list_packages() if not item.get("abstract")][
            start : start + capped
        ]

    # ------------------------------------------------------------ projects
    @app.get("/projects")
    def projects(limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        capped, start = _page_bounds(limit, offset)
        with session_scope() as db:
            user = current_user() or {}
            if user.get("role") == auth.ROLE_SUPER_ADMIN:
                rows = list(
                    db.scalars(
                        select(Project)
                        .order_by(Project.updated_at.desc())
                        .limit(capped)
                        .offset(start)
                    )
                )
                return [
                    _project_payload(item, _project_stats(db, item.id), "admin")
                    for item in rows
                ]
            memberships = {
                row.project_id: row.role
                for row in db.scalars(
                    select(ProjectMember).where(ProjectMember.user_id == user.get("id"))
                )
            }
            visible: dict[str, tuple[Project, str]] = {
                item.id: (item, "owner")
                for item in db.scalars(
                    select(Project).where(Project.owner_id == user.get("id"))
                )
            }
            if memberships:
                for item in db.scalars(
                    select(Project).where(Project.id.in_(list(memberships)))
                ):
                    visible.setdefault(item.id, (item, memberships[item.id]))
            ordered = sorted(
                visible.values(),
                key=lambda pair: pair[0].updated_at or datetime.min,
                reverse=True,
            )
            return [
                _project_payload(item, _project_stats(db, item.id), role)
                for item, role in ordered[start : start + capped]
            ]

    @app.post("/projects", status_code=201)
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

    @app.post("/projects/batch-check", status_code=202)
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
            jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
            audit(db, None, "project.batch_check", {"count": len(project_ids)})
        worker = threading.Thread(
            target=_run_batch_check_job,
            args=(job_id, project_ids),
            daemon=True,
            name=f"bidcraft-batch-check-{job_id[:8]}",
        )
        worker.start()
        return {"job_id": job_id}

    @app.post("/projects/batch-export", status_code=202)
    def batch_export(payload: BatchExportRequest) -> dict[str, Any]:
        """Queue a background export and return the job whose zip can be downloaded."""

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
            jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
            audit(db, None, "project.batch_export", {"count": len(project_ids), "format": fmt})
        worker = threading.Thread(
            target=_run_batch_export_job,
            args=(job_id, project_ids, fmt),
            daemon=True,
            name=f"bidcraft-batch-export-{job_id[:8]}",
        )
        worker.start()
        return {"job_id": job_id}

    @app.get("/projects/{ref}")
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

    @app.patch("/projects/{ref}")
    def update_project(ref: str, payload: ProjectUpdate) -> dict[str, Any]:
        """Rename a project or set its bid section (标段/包件)."""

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")
        with session_scope() as db:
            project, my_role = _project(db, ref)
            _require_write(my_role)
            keys: list[str] = []
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
            audit(db, project.id, "project.update", {"keys": keys})
            return _project_payload(project, _project_stats(db, project.id), my_role)

    @app.delete("/projects/{ref}")
    def delete_project(ref: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_owner(_role)
            project_id = project.id
            project_name = project.name
            node_ids = list(
                db.scalars(select(OutlineNode.id).where(OutlineNode.project_id == project.id))
            )
            if node_ids:
                db.execute(delete(Section).where(Section.node_id.in_(node_ids)))
            job_ids = list(
                db.scalars(select(Job.id).where(Job.project_id == project.id))
            )
            if job_ids:
                db.execute(delete(JobEvent).where(JobEvent.job_id.in_(job_ids)))
            for model in (
                OutlineNode,
                Issue,
                Delivery,
                Job,
                Fact,
                Material,
                Parameter,
                ScoringItem,
                Requirement,
                Risk,
                SourceFile,
                ImageAsset,
                BoqSnapshot,
            ):
                db.execute(delete(model).where(model.project_id == project.id))
            db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            db.execute(delete(AuditLog).where(AuditLog.project_id == project_id))
            project_dir = settings.project_dir(project.id)
            db.delete(project)
            db.flush()
            audit(db, None, "project.delete", {"name": project_name, "project_id": project_id})
        shutil.rmtree(project_dir, ignore_errors=True)
        return {"deleted": True, "id": project_id}

    # ------------------------------------------------------------- members
    def _member_payload(member: ProjectMember, user: User | None) -> dict[str, Any]:
        return {
            "user_id": member.user_id,
            "username": user.username if user is not None else "",
            "display_name": (user.display_name or user.username)
            if user is not None
            else "",
            "role": member.role,
            "created_at": _iso(member.created_at),
        }

    def _member(db, project_id: str, user_id: str) -> ProjectMember:
        member = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        if member is None:
            raise LookupError("成员不存在")
        return member

    @app.get("/projects/{ref}/members")
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

    @app.post("/projects/{ref}/members", status_code=201)
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

    @app.patch("/projects/{ref}/members/{user_id}")
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

    @app.delete("/projects/{ref}/members/{user_id}")
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

    # -------------------------------------------------------------- audit
    def _audit_item(row: AuditLog, project_name: dict[str, str] | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": row.id,
            "action": row.action,
            "username": row.username,
            "display_name": row.display_name,
            "detail": row.detail or {},
            "created_at": _iso(row.created_at),
        }
        if project_name is not None:
            item["project_id"] = row.project_id
            item["project_name"] = (
                project_name.get(row.project_id) if row.project_id else None
            )
        return item

    def _clamp_limit(limit: int) -> int:
        return max(1, min(200, limit))

    @app.get("/projects/{ref}/audit")
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

    @app.get("/admin/audit")
    def admin_audit(
        request: Request,
        limit: int = 50,
        before: int | None = None,
        project_id: str = "",
    ) -> dict[str, Any]:
        require_super(request)
        with session_scope() as db:
            statement = (
                select(AuditLog)
                .order_by(AuditLog.id.desc())
                .limit(_clamp_limit(limit))
            )
            if before is not None:
                statement = statement.where(AuditLog.id < before)
            if project_id:
                statement = statement.where(AuditLog.project_id == project_id)
            rows = list(db.scalars(statement))
            ids = {row.project_id for row in rows if row.project_id}
            names = (
                {
                    item.id: item.name
                    for item in db.scalars(select(Project).where(Project.id.in_(ids)))
                }
                if ids
                else {}
            )
            return {
                "items": [_audit_item(row, names) for row in rows],
                "next_before": rows[-1].id if len(rows) == _clamp_limit(limit) else None,
            }

    # -------------------------------------------------------------- images
    def _image_http_error(exc: images.ImageError) -> HTTPException:
        if isinstance(exc, images.ImageConfigError):
            status = 503
        elif isinstance(exc, images.ImageTooLarge):
            status = 413
        else:
            status = 400
        return HTTPException(status_code=status, detail=str(exc))

    @app.get("/projects/{ref}/images")
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

    @app.post("/projects/{ref}/images/generate-missing", status_code=202)
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
            job = Job(
                project_id=project.id,
                kind="images",
                status="queued",
                message=f"配图任务排队中（{len(targets)} 个图名）…",
                payload={
                    "total": len(targets),
                    "done": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "skipped": 0,
                    "include_failed": include_failed,
                    "labels": [target["label"] for target in targets],
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id
            jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
            audit(db, project.id, "image.batch", {"count": len(targets)})
        worker = threading.Thread(
            target=_run_image_batch_job,
            args=(job_id, project.id, targets),
            daemon=True,
            name=f"bidcraft-images-{job_id[:8]}",
        )
        worker.start()
        return {"job_id": job_id}

    @app.post("/projects/{ref}/images", status_code=201)
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

    @app.post("/projects/{ref}/images/{image_id}/regenerate", status_code=201)
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

    @app.post("/projects/{ref}/images/upload", status_code=201)
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

    @app.get("/projects/{ref}/image-policy")
    def get_image_policy(ref: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            return images.get_policy(project)

    @app.put("/projects/{ref}/image-policy")
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

    @app.get("/projects/{ref}/images/plan")
    def image_plan(ref: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            return images.image_plan(db, project)

    @app.post("/projects/{ref}/images/apply-plan")
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

    @app.get("/projects/{ref}/images/{image_or_file}")
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

    @app.delete("/projects/{ref}/images/{image_id}")
    def delete_image(ref: str, image_id: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            if not images.delete_asset(db, project.id, image_id):
                raise LookupError(f"图片不存在：{image_id}")
            audit(db, project.id, "image.delete", {"id": image_id})
            return {"deleted": True, "id": image_id}

    # ------------------------------------------------------------- sources
    @app.get("/projects/{ref}/sources")
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

    @app.post("/projects/{ref}/sources")
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
                max_bytes=MAX_TENDER_UPLOAD_BYTES,
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
                audit(db, project.id, "source.upload", {"filename": filename})
                return {
                    "project": _project_payload(project, _project_stats(db, project.id)),
                    "ledger_summary": ledger_summary(ledger),
                    "notes": info["notes"],
                    "missing_required": [item.key for item in ledger.missing_required()],
                }

    @app.get("/projects/{ref}/sources/{source_id}/file")
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

    @app.post("/projects/{ref}/parse")
    def reparse(ref: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            try:
                ledger, info = parse_project(db, project.id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            audit(db, project.id, "parse.run", {"notes": len(info["notes"])})
            return {
                "project": _project_payload(project, _project_stats(db, project.id)),
                "ledger_summary": ledger_summary(ledger),
                "notes": info["notes"],
                "missing_required": [item.key for item in ledger.missing_required()],
            }

    # -------------------------------------------------------------- ledger
    @app.get("/projects/{ref}/ledger")
    def ledger(ref: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            data = load_ledger(db, project.id)
            return {**data.model_dump(), "summary": ledger_summary(data)}

    @app.patch("/projects/{ref}/facts/{key}")
    def set_fact(ref: str, key: str, payload: FactUpdate) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            try:
                row = update_fact(db, project.id, key, value=payload.value, status=payload.status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            audit(db, project.id, "fact.update", {"key": row.key, "status": row.status})
            return {
                "key": row.key,
                "label": row.label,
                "value": row.value,
                "status": row.status,
                "required": row.required,
            }

    # ------------------------------------------------------------- outline
    @app.get("/projects/{ref}/outline")
    def outline(ref: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            return {"chapters": _outline_payload(db, project.id)}

    @app.post("/projects/{ref}/outline")
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

    @app.put("/projects/{ref}/outline")
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

    # ------------------------------------------------------------- chapters
    def _node(db, project_id: str, node_id: str) -> OutlineNode:
        node = db.get(OutlineNode, node_id)
        if node is None or node.project_id != project_id:
            raise LookupError(f"章节不存在: {node_id}")
        return node

    def _chapter_payload(db, node: OutlineNode) -> dict[str, Any]:
        section = db.scalar(select(Section).where(Section.node_id == node.id))
        return {
            "id": node.id,
            "heading": node.heading,
            "intent": node.intent,
            "target_words": node.target_words,
            "needs": node.needs or [],
            "body": section.body if section else "",
            "words": section.words if section else 0,
            "status": section.status if section else node.status,
            "revision": (section.revision or 0) if section else 0,
        }

    @app.get("/projects/{ref}/chapters/{node_id}")
    def chapter(ref: str, node_id: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            return _chapter_payload(db, _node(db, project.id, node_id))

    @app.put("/projects/{ref}/chapters/{node_id}")
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
            if section is None:
                section = Section(node_id=node.id)
                db.add(section)
            section.body = payload.body
            section.words = count_words(payload.body)
            section.status = "edited"
            section.revision = current_revision + 1
            node.status = "edited"
            db.flush()
            audit(db, project.id, "chapter.save", {"heading": node.heading})
            return _chapter_payload(db, node)

    @app.post("/projects/{ref}/chapters/{node_id}/generate")
    def generate_chapter(ref: str, node_id: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            node = _node(db, project.id, node_id)
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
            try:
                charged = billing.charge_chapters(
                    db, user, 1, project_id=project.id, note=f"生成本章：{node.heading}"
                )
            except billing.InsufficientCredits as exc:
                raise HTTPException(status_code=402, detail=str(exc)) from exc
            report = generate_document(
                db,
                project.id,
                client,
                ledger,
                package,
                only_node=node.id,
                knowledge_provider=_knowledge_provider(db),
            )
            if charged and not report.written:
                billing.refund_chapters(db, user, charged, project_id=project.id)
            project.status = "drafting"
            db.flush()
            audit(db, project.id, "chapter.generate", {"heading": node.heading})
            return _report_payload(report)

    # ---------------------------------------------------------- generation
    @app.post("/projects/{ref}/write")
    def write(ref: str) -> dict[str, Any]:
        """Synchronous generation kept for CLI parity and small integrations."""
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
            user = billing_user(db)
            client = _generation_client(db)
            count = _pending_chapters(db, project.id)
            _credit_guard(db, user, billing.billing_settings(db)["chapter_cost"] * max(0, count))
            if user.is_external and not client.available:
                raise HTTPException(
                    status_code=409, detail="尚未配置可用的对话模型，请联系超级管理员"
                )
            try:
                charged = billing.charge_chapters(
                    db, user, count, project_id=project.id, note="同步生成全书正文"
                )
            except billing.InsufficientCredits as exc:
                raise HTTPException(status_code=402, detail=str(exc)) from exc
            report = generate_document(
                db,
                project.id,
                client,
                ledger,
                package,
                knowledge_provider=_knowledge_provider(db),
            )
            if charged and count > len(report.written):
                unit = charged // count if count else 0
                billing.refund_chapters(
                    db, user, unit * (count - len(report.written)), project_id=project.id
                )
            project.status = "drafting"
            db.flush()
            return _report_payload(report)

    @app.post("/projects/{ref}/generate", status_code=202)
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
            client = _generation_client(db)
            count = _pending_chapters(db, project.id, force=force)
            if count <= 0:
                raise HTTPException(status_code=409, detail="没有待生成的章节")
            _credit_guard(db, user, billing.billing_settings(db)["chapter_cost"] * max(0, count))
            if user.is_external and not client.available:
                raise HTTPException(
                    status_code=409, detail="尚未配置可用的对话模型，请联系超级管理员"
                )
            try:
                charged = billing.charge_chapters(
                    db, user, count, project_id=project.id, note="生成全书正文"
                )
            except billing.InsufficientCredits as exc:
                raise HTTPException(status_code=402, detail=str(exc)) from exc
            job = Job(
                project_id=project.id,
                kind="generate",
                status="queued",
                message="排队中…",
                payload={
                    "charged": charged,
                    "chapters": count,
                    "user_id": user.id,
                    "cost_per": charged // count if count else 0,
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id
            jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
            audit(db, project.id, "project.generate", {"chapters": count})
        worker = threading.Thread(
            target=_run_generation_job,
            args=(job_id, project.id, force),
            daemon=True,
            name=f"bidcraft-generate-{job_id[:8]}",
        )
        worker.start()
        return {"job_id": job_id}

    @app.get("/projects/{ref}/jobs")
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

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        with session_scope() as db:
            return jobs.job_payload(_job(db, job_id))

    @app.get("/jobs/{job_id}/events")
    def job_events(job_id: str, request: Request, after: int = 0) -> StreamingResponse:
        """Resumable Server-Sent Events for one generation job.

        The browser sends ``Last-Event-ID`` automatically when EventSource
        reconnects; ``?after=`` is available for non-EventSource clients.
        """

        header = request.headers.get("last-event-id") or ""
        try:
            last_id = max(int(header), int(after or 0))
        except ValueError:
            last_id = max(0, int(after or 0))
        with session_scope() as db:
            job = _job(db, job_id)
            snapshot = jobs.job_payload(job)

        def event_stream():
            cursor = last_id
            last_ping = time.monotonic()
            terminal = False
            yield "retry: 3000\n\n"
            yield jobs.sse_frame(None, "snapshot", snapshot)
            while True:
                rows = jobs.events_since(job_id, cursor, limit=200)
                with session_scope() as db:
                    current = db.get(Job, job_id)
                if current is None:
                    break
                for row in rows:
                    cursor = int(row.id)
                    yield jobs.sse_frame(cursor, row.kind, row.data or {})
                    if row.kind in {"done", "failed"}:
                        terminal = True
                if terminal or (current.status in jobs.TERMINAL_STATUSES and not rows):
                    break
                if not rows:
                    if time.monotonic() - last_ping >= 15:
                        yield ": ping\n\n"
                        last_ping = time.monotonic()
                    time.sleep(0.25)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------- batch jobs
    @app.get("/batch-jobs/{job_id}/download")
    def download_batch_zip(job_id: str) -> FileResponse:
        """Download the zip produced by a finished batch export."""

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None or job.kind != "batch_export" or job.status != "done":
                raise LookupError("任务不存在")
            user = current_user() or {}
            owner_id = str((job.payload or {}).get("owner_id") or "")
            if user.get("role") != auth.ROLE_SUPER_ADMIN and owner_id != str(
                user.get("id") or ""
            ):
                raise LookupError("任务不存在")
            zip_path = str((job.result or {}).get("zip_path") or "")
            root = (settings.home / "output").resolve()
            path = Path(zip_path).resolve() if zip_path else None
            if path is None or root not in path.parents or not path.is_file():
                raise LookupError("导出包不存在")
        return FileResponse(path, filename=path.name)

    # --------------------------------------------------------------- check
    @app.post("/projects/{ref}/check")
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

    @app.get("/projects/{ref}/issues")
    def issues(ref: str, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        capped, start = _page_bounds(limit, offset)
        with session_scope() as db:
            project, _role = _project(db, ref)
            rows = list(
                db.scalars(select(Issue).where(Issue.project_id == project.id))
            )
            rows.sort(key=lambda row: SEVERITY_ORDER.get(row.level, 9))
            return [_issue_payload(row) for row in rows[start : start + capped]]

    # -------------------------------------------------------------- export
    @app.post("/projects/{ref}/export")
    def export(ref: str, payload: ExportRequest) -> FileResponse:
        fmt = payload.format.lower()
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            ledger = load_ledger(db, project.id)
            nodes = load_outline(db, project.id)
            if fmt != "xlsx" and (not nodes or not any(node.get("body") for node in nodes)):
                raise HTTPException(status_code=409, detail="还没有正文，请先生成或编写章节")
            output_dir = settings.project_dir(project.id) / "output"
            audit(db, project.id, "export.run", {"format": fmt})
            if fmt == "docx":
                path = export_docx(
                    output_dir / "bid.docx",
                    ledger,
                    nodes,
                    image_resolver=images.url_resolver(project.id),
                    image_policy=images.get_policy(project),
                )
            elif fmt in {"md", "markdown"}:
                path = export_markdown(output_dir / "bid.md", ledger, nodes)
            elif fmt == "xlsx":
                from .export.xlsx import export_xlsx

                path = export_xlsx(output_dir / "bid.xlsx", ledger)
            elif fmt == "pdf":
                from .export.pdf import PdfExportError, export_pdf

                docx_path = export_docx(
                    output_dir / "bid.docx",
                    ledger,
                    nodes,
                    image_resolver=images.url_resolver(project.id),
                    image_policy=images.get_policy(project),
                )
                try:
                    path = export_pdf(docx_path)
                except PdfExportError as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
            else:
                raise HTTPException(status_code=400, detail="format 只能是 docx/md/pdf/xlsx")
            safe = re.sub(r'[\\/:*?"<>|]', "", project.name).strip() or "BidCraft"
            label = {
                "docx": "投标文件",
                "pdf": "投标文件",
                "md": "投标文件",
                "xlsx": "事实台账",
            }[fmt if fmt != "markdown" else "md"]
            return FileResponse(path, filename=f"{safe[:60]}-{label}{path.suffix}")

    # -------------------------------------------------------------- packages
    def _bid_packages(db, project_id: str) -> list[BidPackage]:
        return list(
            db.scalars(
                select(BidPackage)
                .where(BidPackage.project_id == project_id)
                .order_by(BidPackage.position, BidPackage.created_at, BidPackage.id)
            )
        )

    def _bid_package(db, project: Project, package_id: str) -> BidPackage:
        package = db.get(BidPackage, package_id)
        if package is None or package.project_id != project.id:
            raise LookupError(f"包件不存在：{package_id}")
        return package

    def _boq_package_ids(db, project_id: str, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        return set(
            db.scalars(
                select(BoqSnapshot.package_id).where(
                    BoqSnapshot.project_id == project_id,
                    BoqSnapshot.package_id.in_(ids),
                )
            )
        )

    def _resolve_boq_package(db, project: Project, package_id: str | None):
        """Map boq.resolve_package's 400 semantics onto the HTTP layer."""
        try:
            return boq.resolve_package(db, project, package_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/projects/{ref}/packages")
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

    @app.post("/projects/{ref}/packages", status_code=201)
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

    @app.patch("/projects/{ref}/packages/{package_id}")
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

    @app.delete("/projects/{ref}/packages/{package_id}")
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

    # ------------------------------------------------------------------ boq
    @app.get("/projects/{ref}/boq")
    def boq_alignment(ref: str, package_id: str | None = None) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            package = _resolve_boq_package(db, project, package_id)
            return boq.build_payload(
                db, project.id, package_id=package.id if package else None
            )

    @app.post("/projects/{ref}/boq")
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
                max_bytes=MAX_BOQ_UPLOAD_BYTES,
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

    @app.post("/projects/{ref}/boq/apply")
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

    @app.post("/projects/{ref}/boq/price")
    def boq_price(ref: str, payload: BoqPriceRequest) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            try:
                package = boq.resolve_package(db, project, payload.package_id)
                resolved = package.id if package else None
                result = boq.set_unit_price(
                    db, project, payload.boq_row, payload.unit_price, package_id=resolved
                )
            except boq.BoqConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            audit(
                db,
                project.id,
                "boq.price",
                {
                    "row": payload.boq_row,
                    "unit_price": payload.unit_price,
                    "package_id": resolved,
                },
            )
            return result

    @app.get("/projects/{ref}/boq/versions")
    def boq_version_list(
        ref: str, package_id: str | None = None
    ) -> list[dict[str, Any]]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            try:
                return boq.list_versions(db, project, package_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/projects/{ref}/boq/versions", status_code=201)
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

    @app.get("/projects/{ref}/boq/versions/{version_id}")
    def boq_version_detail(ref: str, version_id: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            version = boq.get_version(db, project.id, version_id)
            if version is None:
                raise LookupError("版本不存在")
            return boq.version_payload(version, include_prices=True)

    @app.get("/projects/{ref}/boq/versions/{version_id}/diff")
    def boq_version_diff(
        ref: str, version_id: str, other: str | None = None
    ) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            try:
                return boq.diff_version(db, project, version_id, other)
            except boq.BoqConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/projects/{ref}/boq/versions/{version_id}/restore")
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

    @app.delete("/projects/{ref}/boq/versions/{version_id}")
    def boq_version_delete(ref: str, version_id: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            boq.delete_version(db, project, version_id)
            audit(db, project.id, "boq.version_delete", {"version_id": version_id})
            return {"deleted": True, "id": version_id}

    @app.get("/projects/{ref}/price-policy")
    def price_policy_get(ref: str) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            return boq.get_price_policy(project)

    @app.put("/projects/{ref}/price-policy")
    def price_policy_put(ref: str, payload: PricePolicyUpdate) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            policy = boq.set_price_policy(db, project, payload.model_dump())
            audit(db, project.id, "price.policy", policy)
            return policy

    @app.post("/projects/{ref}/boq/quota-fill")
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

    @app.post("/projects/{ref}/boq/adjust")
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

    @app.delete("/projects/{ref}/boq")
    def boq_delete(ref: str, package_id: str | None = None) -> dict[str, Any]:
        with session_scope() as db:
            project, _role = _project(db, ref)
            _require_write(_role)
            package = _resolve_boq_package(db, project, package_id)
            resolved = package.id if package else None
            boq.remove_snapshot(db, project, package_id=resolved)
            audit(db, project.id, "boq.delete", {"package_id": resolved})
            return {"deleted": True}

    @app.post("/projects/{ref}/boq/export")
    def boq_export(ref: str, package_id: str | None = None) -> FileResponse:
        from .export.boq_xlsx import export_boq_xlsx

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

    # ------------------------------------------------------------------ web
    dist = Path(__file__).resolve().parent / "web_dist"
    if (dist / "index.html").is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = (dist / path).resolve()
            if candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def web_missing() -> dict[str, Any]:
            return {
                "name": "BidCraft",
                "version": __version__,
                "message": "Web 客户端尚未构建：cd web && npm install && npm run build",
                "api_docs": "/docs",
            }

    return app


app = None
try:  # keep importing bidcraft.api cheap when the optional extra is absent
    app = create_app()
except RuntimeError:  # pragma: no cover
    app = None
