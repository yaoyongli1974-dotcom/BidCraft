"""HTTP API and B/S web host (optional extra: ``pip install -e '.[api]'``).

The API is a thin wrapper over the same pipeline used by the CLI. It uses
cookie sessions and role-based access control; there is no telemetry. Bind to
127.0.0.1 for single-user mode, or put it behind TLS and your own network
controls when serving a team.
"""

import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import (
    __version__,
    auth,
    billing,
    boq,
    clock,
    images,
    plugins,
    provenance,
    textqa,
    wechat,
)
from ..audit import record_audit
from ..config import settings, validate_startup
from ..context import current_user, set_current_user
from ..db import init_db, session_scope
from ..export_gate import (
    EXPORT_GATE_FORMATS,  # noqa: F401 - 保留 from bidcraft.api import 的既有导出面
    ISSUE_RESOLVED_STATUSES,  # noqa: F401 - 同上，定义迁至 export_gate
    issue_is_open,
)
from ..generation.outline import load_outline
from ..models import (
    AuditLog,
    BidPackage,
    BoqSnapshot,
    Fact,
    Issue,
    Job,
    OutlineNode,
    Project,
    ProjectMember,
    RechargeOrder,
    Requirement,
    Section,
    SourceFile,
    User,
)
from ..pipeline import resolve_project
from ..rules import store as package_store
from ..rules.loader import (
    list_packages,
    validate_package_text,
)
from ..services.job_support import (  # noqa: F401 - 保留 api 名字空间，路由闭包按原名解析
    BATCH_STALE_SECONDS,
    KB_REINDEX_STALE_SECONDS,
    STARTUP_INTERRUPTED_MESSAGE,
    _active_kb_reindex,
    _cancel_stale_jobs,
    _fail_job,
    _generation_client,
    _job_refund,
    _knowledge_provider,
    _recover_interrupted_jobs,
    _report_payload,
)
from ..workers import (  # noqa: F401 - 同上；测试也 monkeypatch 这些名字
    _run_batch_check_job,
    _run_batch_export_job,
    _run_generation_job,
    _run_image_batch_job,
    _run_kb_reindex_job,
)

SEVERITY_ORDER = {"blocking": 0, "critical": 1, "major": 2, "minor": 3, "info": 4}

#: ISSUE_RESOLVED_STATUSES / EXPORT_GATE_FORMATS 定义在 export_gate（HTTP/MCP/CLI 共用口径）。
#: 风险总览四档：minor 与 info 合并进「提示」档（items 仍保留原始 level）。
ISSUE_TIERS = (
    ("blocking", "废标/阻断"),
    ("critical", "严重"),
    ("major", "主要"),
    ("minor", "提示"),
)
ISSUE_BULK_LIMIT = 200

logger = logging.getLogger("bidcraft.api")

MAX_TENDER_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_BOQ_UPLOAD_BYTES = 20 * 1024 * 1024
TENDER_UPLOAD_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md", ".xlsx", ".xlsm"})
BOQ_UPLOAD_SUFFIXES = frozenset({".xlsx", ".xlsm"})
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_PAGE_SIZE = 1000

# Roles that represent tender documents (the BOQ workbook is managed separately
# by the 清单对齐 feature and must never show up as a tender source).
TENDER_SOURCE_ROLES = ("tender", "annex")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    package: str = "weakcurrent-cabling"
    # 标段/包件：仅作项目元信息，不参与多包件共享台账（后续迭代）。
    bid_section: str = ""


class ScoreRow(BaseModel):
    id: str
    actual_score: float | None = None
    actual_bidder: str | None = None
    score_note: str | None = None


class ScoresUpdate(BaseModel):
    rows: list[ScoreRow] = []


class ProjectUpdate(BaseModel):
    name: str | None = None
    bid_section: str | None = None
    # R23 设为模板/取消模板（仅 owner/admin）。
    is_template: bool | None = None
    # R19 审批链：兼容 R18 扁平用户 id 列表与 v2 阶段列表
    # [{approvers: [用户id], mode?: "all"|"any"}]（≤5 级，仅 owner 可改）；
    # 空列表=单级负责人审批。双格式解析与校验在 handler 内完成。
    approval_chain: list[str] | list[dict[str, Any]] | None = None


class ProjectDuplicate(BaseModel):
    # R22 项目复制：name 缺省/空白 → 「源项目名 副本」；长度校验在
    # duplicate_project 内完成（超 255 → ValueError → 400，而非 422）。
    name: str | None = None
    include_boq: bool = True
    include_images: bool = True


class ProjectInstantiate(BaseModel):
    # R23 从模板创建：形状同 ProjectDuplicate，名称校验同样在
    # duplicate_project 内完成（超 255 → ValueError → 400，而非 422）。
    name: str | None = None
    include_boq: bool = True
    include_images: bool = True


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


class ChapterReview(BaseModel):
    """章节复核流转：草稿 → 待复核 → 已确认 → 已锁定。"""

    action: Literal["submit", "confirm", "revert", "lock", "unlock"]
    note: str = Field(default="", max_length=500)


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
    force: bool = False
    force_reason: str = ""


class IssueStatusUpdate(BaseModel):
    status: Literal["handled", "ignored", "open"]
    note: str = ""


class IssueBulkStatusUpdate(BaseModel):
    ids: list[str] = Field(default_factory=list)
    status: Literal["handled", "ignored", "open"]
    note: str = ""


class RequirementResponseUpdate(BaseModel):
    response_status: Literal["responded", "na", "pending"]
    note: str = ""


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


class BoqVersionSubmit(BaseModel):
    note: str = ""


class BoqVersionReview(BaseModel):
    decision: str
    note: str = ""


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


class QualificationCreate(BaseModel):
    kind: str = "other"
    name: str = ""
    holder: str = ""
    cert_no: str = ""
    issuer: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    note: str = ""


class QualificationUpdate(BaseModel):
    kind: str | None = None
    name: str | None = None
    holder: str | None = None
    cert_no: str | None = None
    issuer: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    note: str | None = None


class PersonnelCreate(BaseModel):
    name: str = ""
    title: str = ""
    specialty: str = ""
    cert_no: str = ""
    # ``Any`` so out-of-range / non-integer values reach the module validator
    # and produce a 400 (contract), not FastAPI's default 422.
    social_months: Any = 0
    locked: Any = False
    note: str = ""


class PersonnelUpdate(BaseModel):
    name: str | None = None
    title: str | None = None
    specialty: str | None = None
    cert_no: str | None = None
    social_months: Any = None
    locked: Any = None
    note: str | None = None


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
    #: 勾选同意《服务条款》《隐私政策》（默认 True 以兼容旧客户端）。
    accept_terms: bool = True
    terms_version: str = ""


class OrderCreate(BaseModel):
    package_id: str


class InvoiceCreate(BaseModel):
    title: str = ""
    amount_cents: int = 0
    tax_no: str = ""
    email: str = ""
    note: str = ""


class InvoiceReject(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


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
    vip_packages: list[dict[str, Any]] | None = None
    vip_discount: float | None = None
    sub_account_limit: int | None = None


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


class VipGrant(BaseModel):
    days: int = Field(ge=0, le=3650)
    note: str = ""


class SubAccountCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = ""


class SubAccountUpdate(BaseModel):
    display_name: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)
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


class KbDocMeta(BaseModel):
    source: str = Field(min_length=1, max_length=1000)
    #: 必填：缺省不再默认成 other，避免把未分类素材当免检类别。
    kind: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=200)
    cert_no: str = Field(default="", max_length=120)
    contract_no: str = Field(default="", max_length=120)
    owner: str = Field(default="", max_length=120)
    amount: str = Field(default="", max_length=60)
    acceptance: str = Field(default="", max_length=200)
    social_months: str = Field(default="", max_length=60)
    locked: str = Field(default="", max_length=60)
    valid_to: str = Field(default="", max_length=10)
    source_url: str = Field(default="", max_length=500)
    verifiable_at: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=200)
    fields: dict[str, Any] | None = None


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
    # 轻量沙箱：越界/非法由 plugins.clamp_* 回退默认值，不返回 400。
    mem_limit_mb: int | None = None
    cpu_seconds: int | None = None


class PluginUpdate(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | str | None = None
    env: dict[str, str] | str | None = None
    url: str | None = None
    enabled: bool | None = None
    tool_allowlist: list[str] | str | None = None
    mem_limit_mb: int | None = None
    cpu_seconds: int | None = None


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

    return record_audit(db, project_id, action, detail)


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
        "is_template": bool(project.is_template),
        "owner_id": project.owner_id,
        "my_role": my_role,
        "number": (project.meta or {}).get("number", ""),
        "tender_number": (project.meta or {}).get("tender_number", ""),
        "bid_section": (project.meta or {}).get("bid_section", "") or "",
        "approval_chain": boq.normalize_chain((project.meta or {}).get("approval_chain")),
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


def _issue_is_open(issue: Issue) -> bool:
    return issue_is_open(issue)


def _issue_tier(level: str) -> str:
    return level if level in {"blocking", "critical", "major"} else "minor"


REQUIREMENT_RESPONSE_MIN_NOTE = 4


def _requirement_payload(db: Session, requirement: Requirement) -> dict[str, Any]:
    heading = ""
    node_id = requirement.node_id
    if node_id:
        node = db.get(OutlineNode, node_id)
        if node is not None:
            heading = node.heading or ""
    return {
        "id": requirement.id,
        "kind": requirement.kind,
        "marker": requirement.marker or "",
        "text": requirement.text,
        "mandatory": bool(requirement.mandatory),
        "source_quote": requirement.source_quote or "",
        "response_status": requirement.response_status or "pending",
        "response_note": requirement.response_note or "",
        "responded_by": requirement.responded_by or "",
        "responded_at": clock.iso_beijing(requirement.responded_at),
        "section_id": requirement.section_id,
        "node_id": node_id,
        "section_heading": heading,
        "auto_covered": bool(requirement.auto_covered),
    }


def _validate_requirement_note(status: str, note: str) -> str:
    cleaned = (note or "").strip()
    if status == "na" and len(cleaned) < REQUIREMENT_RESPONSE_MIN_NOTE:
        raise _http_error(
            400,
            f"标记为不适用必须填写原因（至少 {REQUIREMENT_RESPONSE_MIN_NOTE} 个字）",
        )
    return cleaned


def _apply_requirement_status(
    requirement: Requirement, status: str, note: str, username: str
) -> None:
    requirement.response_status = status
    if status == "pending":
        requirement.response_note = ""
        requirement.responded_by = None
        requirement.responded_at = None
        return
    requirement.response_note = note
    requirement.responded_by = username or "system"
    requirement.responded_at = clock.utc_now()


def _issue_node_map(db: Session, issues: list[Issue]) -> dict[str, tuple[str, int]]:
    """Batch-resolve anchored OutlineNodes (heading/position) — one query, no N+1."""

    node_ids = {issue.node_id for issue in issues if issue.node_id}
    if not node_ids:
        return {}
    rows = db.scalars(select(OutlineNode).where(OutlineNode.id.in_(node_ids)))
    return {row.id: (row.heading, row.position) for row in rows}


def _issue_payload(
    issue: Issue, node_map: dict[str, tuple[str, int]] | None = None
) -> dict[str, Any]:
    heading, position = (node_map or {}).get(issue.node_id or "", ("", None))
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
        "node_id": issue.node_id,
        "section_id": issue.section_id,
        "heading": heading,
        "position": position,
        "checked_at": clock.iso_beijing(issue.checked_at),
        "resolved_at": clock.iso_beijing(issue.resolved_at),
        "resolved_by": issue.resolved_by or "",
        "resolution_note": issue.resolution_note or "",
    }


def _risk_overview_payload(db: Session, project_id: str, rows: list[Issue]) -> dict[str, Any]:
    tiers = {
        level: {"level": level, "label": label, "total": 0, "open": 0, "handled": 0, "ignored": 0}
        for level, label in ISSUE_TIERS
    }
    categories: dict[str, dict[str, Any]] = {}
    blocking_open = 0
    warn_open = 0
    checked_at = None
    for row in rows:
        tier = tiers[_issue_tier(row.level)]
        tier["total"] += 1
        is_open = _issue_is_open(row)
        if is_open:
            tier["open"] += 1
        elif row.status == "ignored":
            tier["ignored"] += 1
        else:
            # handled 以及历史值 resolved/closed 都计入「已处理」。
            tier["handled"] += 1
        category = categories.setdefault(
            row.category or "",
            {"category": row.category or "", "total": 0, "open": 0, "blocking_open": 0},
        )
        category["total"] += 1
        if is_open:
            category["open"] += 1
            if row.level == "blocking":
                category["blocking_open"] += 1
                blocking_open += 1
            elif row.level in {"critical", "major"}:
                warn_open += 1
        if row.checked_at is not None and (checked_at is None or row.checked_at > checked_at):
            checked_at = row.checked_at
    verdict = "blocked" if blocking_open else ("warn" if warn_open else "pass")
    node_map = _issue_node_map(db, rows)
    items = sorted(
        rows,
        key=lambda row: (SEVERITY_ORDER.get(row.level, 9), 0 if _issue_is_open(row) else 1),
    )
    by_category = sorted(
        categories.values(),
        key=lambda entry: (-entry["open"], -entry["total"], entry["category"]),
    )
    return {
        "project_id": project_id,
        "verdict": verdict,
        "checked_at": clock.iso_beijing(checked_at),
        "gate": {
            "blocked": blocking_open > 0,
            "blocking_open": blocking_open,
            "reason": f"存在 {blocking_open} 项未处置的废标/阻断风险" if blocking_open else "",
        },
        "tiers": [tiers[level] for level, _label in ISSUE_TIERS],
        "by_category": by_category,
        "items": [_issue_payload(row, node_map) for row in items],
    }


def _validate_issue_note(status: str, note: str) -> str:
    cleaned = (note or "").strip()
    if status == "ignored" and not cleaned:
        raise _http_error(400, "忽略风险必须填写原因")
    return cleaned


def _apply_issue_status(issue: Issue, status: str, note: str, username: str) -> None:
    if status == "open":
        issue.status = "open"
        issue.resolved_at = None
        issue.resolved_by = None
        issue.resolution_note = ""
        return
    issue.status = status
    issue.resolved_at = clock.utc_now()
    issue.resolved_by = username
    issue.resolution_note = note


def _outline_payload(db, project_id: str) -> list[dict[str, Any]]:
    chapters = []
    flags = provenance.node_flags(db, project_id)
    for node in load_outline(db, project_id):
        body = node.pop("body", "")
        flag = flags.get(node["id"], {})
        chapters.append(
            {
                **node,
                "has_body": bool(body),
                "citations": int(flag.get("citations", 0)),
                "citations_stale": int(flag.get("stale", 0)),
            }
        )
    return chapters


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



def _profile_keys(profile_values: dict[str, Any]) -> list[str]:
    """List the non-empty profile fields for the audit detail."""

    return [key for key, value in profile_values.items() if value not in ("", [], None)]


# 路由模块（api/routes/*）从本模块导入的公共名字。ruff 看不到跨模块引用，统一 noqa。
import io  # noqa: F401,E402
import mimetypes  # noqa: F401,E402
import re  # noqa: F401,E402
import shutil  # noqa: F401,E402
import tempfile  # noqa: F401,E402
import threading  # noqa: F401,E402
import time  # noqa: F401,E402
from datetime import date, timedelta  # noqa: F401,E402

from fastapi import File, Form, HTTPException, Request, Response, UploadFile  # noqa: F401,E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse  # noqa: F401,E402
from sqlalchemy import delete  # noqa: F401,E402

from .. import (  # noqa: F401,E402
    accounts,
    compliance,
    invoices,
    jobqueue,
    jobs,
    knowledge,
    material_policy,
    model_config,
    personnel,
    plugin_pack,
    profile,
    qualifications,
    scoring,
    terms,
    user_docs,
    wechatpay,
)
from ..duplicate import duplicate_project  # noqa: F401,E402
from ..export.docx import export_docx, export_markdown  # noqa: F401,E402
from ..export_gate import ExportGateError, enforce_export_gate  # noqa: F401,E402
from ..generation.outline import build_outline, save_outline  # noqa: F401,E402
from ..generation.writer import count_words, generate_document  # noqa: F401,E402
from ..ledger import ledger_summary, load_ledger, update_fact  # noqa: F401,E402
from ..models import (  # noqa: F401,E402
    AuthSession,
    Delivery,
    ImageAsset,
    JobEvent,
    KbChunk,
    Material,
    Parameter,
    Plugin,
    QuotaItem,
    RedeemCode,
    Risk,
    ScoringItem,
    TermItem,
)
from ..pipeline import add_source, check_project, create_project, parse_project  # noqa: F401,E402
from ..rules.engine import ledger_gate  # noqa: F401,E402
from ..rules.loader import document_package_name, load_package  # noqa: F401,E402
from ..security import encrypt_secret, hash_password, verify_password  # noqa: F401,E402

# FastAPI 类型原先只在 create_app 内 import，提升的 helper 需要模块级可见。
# create_app 内保留同名局部 import（optional extra 缺失时在那里抛 RuntimeError）。
try:
    from fastapi import HTTPException, Request, Response
    from fastapi.responses import RedirectResponse
except ImportError:  # pragma: no cover - optional extra 缺失时 create_app 会抛 RuntimeError
    HTTPException = Request = Response = RedirectResponse = None  # type: ignore

# ---------------------------------------------------------------------------
# 以下 helper / 模型原先定义在 create_app 内部，提升到模块级供 api/routes/* 复用。
# ---------------------------------------------------------------------------

from ..models import KnowledgeBase, ModelConfig


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


def _wechat_login(profile: dict, response: Response, next_path: str) -> RedirectResponse:  # noqa: F811 - 参数名与模块名同形
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


def _reject_sub_billing(user: User) -> None:
    if user.parent_id:
        raise HTTPException(status_code=403, detail="子账号费用由主账号承担，无需单独充值")


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


def plugin_name(value: str | None) -> str:
    try:
        return plugins.plugin_name(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def plugin_transport(value: str | None, *, default: str = "stdio") -> str:
    try:
        return plugins.plugin_transport(value, default=default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def plugin_validate(transport: str, command: str, url: str) -> None:
    try:
        plugins.plugin_validate(transport, command, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def plugin_items(value, *, field: str, limits: int = 64) -> list[str]:
    """接受数组、JSON 文本或逗号分隔字符串，去空去重后返回。"""
    try:
        return plugins.plugin_items(value, field=field, limits=limits)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def plugin_env(value, *, field: str = "env") -> dict[str, str]:
    try:
        return plugins.plugin_env(value, field=field)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


class TextQaUpdate(BaseModel):
    typos: Any = None


def textqa_payload(db) -> dict[str, Any]:
    typos = textqa.load_custom(db)
    return {
        "typos": typos,
        "count": len(typos),
        "builtin_count": len(textqa.TYPO_RULES),
    }


class WechatConfigUpdate(BaseModel):
    app_id: str | None = None
    app_secret: str | None = None
    clear_secret: bool = False
    redirect_uri: str | None = None
    enabled: bool | None = None
    mock: bool | None = None


def wechat_config_payload(db) -> dict[str, Any]:
    from ..models import WechatConfig

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


def _image_http_error(exc: images.ImageError) -> HTTPException:
    if isinstance(exc, images.ImageConfigError):
        status = 503
    elif isinstance(exc, images.ImageTooLarge):
        status = 413
    else:
        status = 400
    return HTTPException(status_code=status, detail=str(exc))


def _node(db, project_id: str, node_id: str) -> OutlineNode:
    node = db.get(OutlineNode, node_id)
    if node is None or node.project_id != project_id:
        raise LookupError(f"章节不存在: {node_id}")
    return node


def _chapter_payload(db, node: OutlineNode) -> dict[str, Any]:
    section = db.scalar(select(Section).where(Section.node_id == node.id))
    citations = provenance.node_citations(db, node.project_id, node.id)
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
        "citations": len(citations),
        "citations_stale": sum(1 for item in citations if item["stale"]),
        "review_status": (section.review_status if section else "draft") or "draft",
        "review_note": section.review_note if section else "",
        "review_requested_at": clock.iso_beijing(section.review_requested_at)
        if section
        else None,
        "review_requested_by": (section.review_requested_by or "") if section else "",
        "confirmed_at": clock.iso_beijing(section.confirmed_at) if section else None,
        "confirmed_by": (section.confirmed_by or "") if section else "",
        "locked_at": clock.iso_beijing(section.locked_at) if section else None,
        "locked_by": (section.locked_by or "") if section else "",
    }


class AccountDelete(BaseModel):
    password: str
    confirm: str


class BillingProfilePayload(BaseModel):
    title: str = ""
    tax_no: str = ""
    email: str = ""


class ExportTemplateUpdate(BaseModel):
    id: str


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


dist = Path(__file__).resolve().parent.parent / "web_dist"



def create_app():
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError("HTTP API 需要安装可选依赖：pip install -e '.[api]'") from exc


    # 启动即校验配置：dev/test 只记 warning，prod 关键项缺失直接阻断（不会拖到业务阶段）。
    validate_startup()
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
    # 每次 create_app() 都重新读取开关：测试与部署会在构造前改 settings，
    # 若在模块加载时缓存，后续 create_app() 会拿到过期值。
    docs_on = settings.docs_enabled
    app = FastAPI(
        title="BidCraft",
        version=__version__,
        description="投标文件编制系统",
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    app.state.started_at = datetime.now(UTC)

    if not docs_on:

        @app.get("/docs", include_in_schema=False)
        @app.get("/redoc", include_in_schema=False)
        @app.get("/openapi.json", include_in_schema=False)
        def docs_disabled() -> None:
            raise HTTPException(status_code=404, detail="接口文档已关闭")

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
            "/approvals/",
            "/auth",
            "/billing/",
            "/account/",
            "/my/",
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

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        if settings.secure_cookie:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.exception_handler(LookupError)
    async def not_found(_request, exc: LookupError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    # 路由按业务域拆分到 api/routes/*，按首次出现顺序注册以保持路径匹配语义。
    from .routes import (
        account as _routes_account,
    )
    from .routes import (
        admin as _routes_admin,
    )
    from .routes import (
        approvals as _routes_approvals,
    )
    from .routes import (
        auth as _routes_auth,
    )
    from .routes import (
        batch_jobs as _routes_batch_jobs,
    )
    from .routes import (
        billing as _routes_billing,
    )
    from .routes import (
        health as _routes_health,
    )
    from .routes import (
        jobs as _routes_jobs,
    )
    from .routes import (
        legal as _routes_legal,
    )
    from .routes import (
        my as _routes_my,
    )
    from .routes import (
        packages as _routes_packages,
    )
    from .routes import (
        projects as _routes_projects,
    )
    app.include_router(_routes_auth.router)
    app.include_router(_routes_billing.router)
    app.include_router(_routes_account.router)
    app.include_router(_routes_admin.router)
    app.include_router(_routes_health.router)
    app.include_router(_routes_packages.router)
    app.include_router(_routes_projects.router)
    app.include_router(_routes_my.router)
    app.include_router(_routes_jobs.router)
    app.include_router(_routes_batch_jobs.router)
    app.include_router(_routes_legal.router)
    app.include_router(_routes_approvals.router)











    # --------------------------------------------------------------- billing










    # ---------------------------------------------------------- sub-accounts





    # ---------------------------------------------------------------- admin





















    # --------------------------------------------------------------- plugins






















    # -------------------------------------------------------- rule packages










    # ---------------------------------------------------------------- quotas







    # ----------------------------------------------------------------- terms




    # ------------------------------------------------------ textqa config




    # ------------------------------------------------------ wechat config





    # ---------------------------------------------------------------- meta



    # ------------------------------------------------------------ projects




    # R23 模板库：静态 "templates" 段必须先于 /projects/{ref} 等动态路由注册，
    # 否则 "templates" 会被当作项目 ref。










    # ------------------------------------------------------------- members






    # -------------------------------------------------------------- audit




    # -------------------------------------------------------------- images












    # ------------------------------------------------------------ user docs




    # ------------------------------------------------------- qualifications








    # ----------------------------------------------------------- personnel




    # ------------------------------------------------------------- sources




    # -------------------------------------------------------------- ledger


    # ------------------------------------------------------------- outline



    # ------------------------------------------------------------- chapters








    # ---------------------------------------------------------- generation





    # ------------------------------------------------------------- batch jobs

    # --------------------------------------------------------------- check







    # -------------------------------------------------------------- export






















    # -------------------------------------------------------------- packages








    # ------------------------------------------------------------------ boq



















    # -------------------------------------------------------------- approvals

    # ------------------------------------------------------------------ web
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
