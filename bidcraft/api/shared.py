"""API 共享命名空间：helper / 常量 / 请求模型重导出。

从 app.py 外迁，作为 routes 与 app 门面的单向依赖源。保留所有原名字（含下划线与 runner/helper 别名），确保 monkeypatch 语义与 ``jobqueue`` 的 ``sys.modules['bidcraft.api.app']`` 运行期查找继续有效。
"""

# 路由模块（api/routes/*）从本模块导入的公共名字。ruff 看不到跨模块引用，统一 noqa。
import io  # noqa: F401,E402
import logging
import math
import mimetypes  # noqa: F401,E402
import re  # noqa: F401,E402
import shutil  # noqa: F401,E402
import tempfile  # noqa: F401,E402
import threading  # noqa: F401,E402
import time  # noqa: F401,E402
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta  # noqa: F401,E402
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import File, Form, HTTPException, Request, Response, UploadFile  # noqa: F401,E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse  # noqa: F401,E402
from pydantic import BaseModel, Field
from sqlalchemy import (
    and_,  # noqa: F401,E402
    case,  # noqa: F401,E402
    delete,  # noqa: F401,E402
    func,
    select,
)
from sqlalchemy.orm import Session

from .. import (  # noqa: F401,E402
    __version__,
    accounts,
    auth,
    billing,
    boq,
    clock,
    compliance,
    images,
    invoices,
    jobqueue,
    jobs,
    knowledge,
    knowledge_assets,
    material_policy,
    model_config,
    personnel,
    plugin_pack,
    plugins,
    profile,
    provenance,
    qualifications,
    scoring,
    terms,
    textqa,
    user_docs,
    wechat,
    wechatpay,
)
from ..audit import record_audit
from ..authorization import (
    MEMBER_ROLES,  # noqa: F401 - 兼容路由 from ..app import MEMBER_ROLES
    PROJECT_WRITE_ROLES,  # noqa: F401 - 同上，保留既有导出面
    Forbidden,
    authorize_job,
    authorize_node,
    authorize_project,
    authorize_projects,
    check_owner,
    check_write,
)
from ..config import settings, validate_startup
from ..context import current_user, reset_current_user, set_current_user
from ..db import init_db, session_scope
from ..duplicate import duplicate_project  # noqa: F401,E402
from ..export.docx import export_docx, export_markdown  # noqa: F401,E402
from ..export_gate import (  # noqa: F401,E402
    EXPORT_GATE_FORMATS,  # noqa: F401 - 保留 from bidcraft.api import 的既有导出面
    ISSUE_RESOLVED_STATUSES,  # noqa: F401 - 同上，定义迁至 export_gate
    ExportGateError,
    enforce_export_gate,
    issue_is_open,
)
from ..generation.chapter_plan import (  # noqa: F401,E402
    CONFIRMED_STATUSES,
    DRAFT,
    PROMPT_VERSION,
    build_plan_context,
    chapter_plan_payload,
    confirm_chapter_plan,
    generate_chapter_plan,
    load_active_plan,
    load_plan_history,
    mark_plan_used,
    plan_context_text,
    update_chapter_plan,
    validate_chapter_plan,
)
from ..generation.outline import build_outline, load_outline, save_outline  # noqa: F401,E402
from ..generation.writer import count_words, generate_document, is_pending  # noqa: F401,E402
from ..ledger import ledger_summary, load_ledger, update_fact  # noqa: F401,E402
from ..models import (  # noqa: F401,E402
    AuditLog,
    AuthSession,
    BidPackage,
    BoqSnapshot,
    ChapterPlan,
    Delivery,
    Fact,
    ImageAsset,
    Issue,
    Job,
    JobEvent,
    KbChunk,
    KnowledgeBase,
    Material,
    ModelConfig,
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
from ..pipeline import (  # noqa: F401,E402
    add_source,
    check_project,
    create_project,
    parse_documents,
    parse_project,
    persist_parse,
    read_parse_inputs,
)
from ..rules import store as package_store
from ..rules.engine import ledger_gate  # noqa: F401,E402
from ..rules.loader import (  # noqa: F401,E402
    document_package_name,
    list_packages,
    load_package,
    validate_package_text,
)
from ..security import encrypt_secret, hash_password, verify_password  # noqa: F401,E402
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
from ..substantive import REQ_TYPE_LABELS, classify_requirement  # noqa: F401,E402
from ..verdict import judge as judge_requirement
from ..verdict import resolve_deadline
from ..workers import (  # noqa: F401 - 同上；测试也 monkeypatch 这些名字
    _run_batch_check_job,
    _run_batch_export_job,
    _run_evaluation_job,
    _run_generation_job,
    _run_image_batch_job,
    _run_kb_reindex_job,
    _run_similarity_job,
)

# FastAPI 类型原先只在 create_app 内 import，提升的 helper 需要模块级可见。
# create_app 内保留同名局部 import（optional extra 缺失时在那里抛 RuntimeError）。
try:
    from fastapi import HTTPException, Request, Response
    from fastapi.responses import RedirectResponse
except ImportError:  # pragma: no cover - optional extra 缺失时 create_app 会抛 RuntimeError
    HTTPException = Request = Response = RedirectResponse = None  # type: ignore

from .schemas import (  # noqa: F401,E402
    AccountDelete,
    BatchExportRequest,
    BatchIdsRequest,
    BillingProfilePayload,
    BillingSettingsPayload,
    BoqAdjustRequest,
    BoqApplyRequest,
    BoqPriceRequest,
    BoqQuotaFillRequest,
    BoqVersionCreate,
    BoqVersionReview,
    BoqVersionSubmit,
    ChapterPlanConfirm,
    ChapterPlanGenerateRequest,
    ChapterPlanUpdate,
    ChapterReview,
    ChapterSave,
    CreditAdjust,
    ExportRequest,
    ExportTemplateUpdate,
    FactUpdate,
    GenerateRequest,
    ImageBatchRequest,
    ImageCreate,
    ImagePolicyUpdate,
    InvoiceCreate,
    InvoiceReject,
    IssueBulkStatusUpdate,
    IssueStatusUpdate,
    KbAssetReview,
    KbCreate,
    KbDocMeta,
    KbDuplicateGroupResolve,
    KbUpdate,
    LessonPolicyUpdate,
    LoginRequest,
    MemberCreate,
    MemberUpdate,
    ModelCreate,
    ModelUpdate,
    OrderCreate,
    OutlineChapter,
    OutlineRequest,
    OutlineSave,
    PackageCreate,
    PackageUpdate,
    PasswordChange,
    PersonnelCreate,
    PersonnelUpdate,
    PluginCreate,
    PluginUpdate,
    PricePolicyUpdate,
    ProjectCreate,
    ProjectDuplicate,
    ProjectInstantiate,
    ProjectUpdate,
    QualificationCreate,
    QualificationUpdate,
    QuotaCreate,
    QuotaUpdate,
    RedeemGenerate,
    RedeemRequest,
    RegisterRequest,
    RequirementResponseUpdate,
    RulePackageDocument,
    RulePackageValidateRequest,
    ScoreRow,
    ScoresUpdate,
    SubAccountCreate,
    SubAccountUpdate,
    TermCreate,
    TermUpdate,
    TextQaUpdate,
    UserCreate,
    UserUpdate,
    VipGrant,
    WechatConfigUpdate,
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

REQUIREMENT_RESPONSE_MIN_NOTE = 4

def _iso(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


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

    薄封装：真实判定统一在 :mod:`bidcraft.authorization`，HTTP 层只保留名字
    与异常映射（``LookupError``→404），CLI/MCP 共用同一口径。
    """

    return authorize_project(db, ref)


def _require_write(role: str) -> None:
    try:
        check_write(role)
    except Forbidden as exc:
        raise _http_error(403, str(exc)) from exc


def _require_owner(role: str) -> None:
    try:
        check_owner(role)
    except Forbidden as exc:
        raise _http_error(403, str(exc)) from exc


def audit(
    db, project_id: str | None, action: str, detail: dict | None = None
) -> AuditLog:
    """Record a successful mutating action for the current request user."""

    return record_audit(db, project_id, action, detail)


def lessons_payload(
    db, project, node_id: str | None = None, *, include_revoked: bool = False
) -> dict:
    """只读经验摘要：运行时转交 evaluation.learning 的真实实现。

    延迟导入避免学习引擎的可选依赖影响 API 启动；路由层可以 monkeypatch
    本名字做契约测试，生产路径始终调用 learning.lessons_payload。
    """

    from ..evaluation.learning import lessons_payload as _lessons_payload

    return _lessons_payload(db, project, node_id=node_id, include_revoked=include_revoked)


def _job(db, job_id: str) -> Job:
    """Load a job and enforce the same project ownership as other endpoints.

    薄封装：判定统一在 :func:`bidcraft.authorization.authorize_job`。
    """

    return authorize_job(db, job_id)


def _batch_projects(db, ids: list[str]) -> list[Project]:
    """Resolve requested project ids to those the caller may operate on.

    薄封装：判定统一在 :func:`bidcraft.authorization.authorize_projects`，
    并顺带修复了成员分支漏校验 ``role in MEMBER_ROLES`` 的一致性问题。
    """

    return authorize_projects(db, ids)


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


_PROJECT_STAT_KEYS = (
    "sources",
    "facts_total",
    "facts_filled",
    "missing_required",
    "outline",
    "written",
    "issues_open",
)


def _project_stats_batch(db, project_ids: list[str]) -> dict[str, dict[str, Any]]:
    """批量版 :func:`_project_stats`：任意数量的项目固定发起 5 条聚合查询。

    每条查询按 ``project_id`` 分组，缺行的项目补 0，因此返回结构与单项目版
    完全一致；列表接口不再按行调用 7 次 COUNT（N+1）。
    """

    ids = list(dict.fromkeys(str(pid) for pid in project_ids if pid))
    stats = {pid: {key: 0 for key in _PROJECT_STAT_KEYS} for pid in ids}
    if not ids:
        return stats

    def apply(rows, *keys: str) -> None:
        for row in rows:
            target = stats.get(str(row[0]))
            if target is None:
                continue
            for key, value in zip(keys, row[1:], strict=True):
                target[key] = int(value or 0)

    apply(
        db.execute(
            select(SourceFile.project_id, func.count())
            .where(
                SourceFile.project_id.in_(ids),
                SourceFile.role.in_(TENDER_SOURCE_ROLES),
            )
            .group_by(SourceFile.project_id)
        ).all(),
        "sources",
    )
    # 三个事实统计共用一次 GROUP BY，用条件聚合替代三条独立 COUNT。
    apply(
        db.execute(
            select(
                Fact.project_id,
                func.count(),
                func.sum(case((Fact.value != "", 1), else_=0)),
                func.sum(
                    case((and_(Fact.required.is_(True), Fact.value == ""), 1), else_=0)
                ),
            )
            .where(Fact.project_id.in_(ids))
            .group_by(Fact.project_id)
        ).all(),
        "facts_total",
        "facts_filled",
        "missing_required",
    )
    apply(
        db.execute(
            select(OutlineNode.project_id, func.count())
            .where(OutlineNode.project_id.in_(ids))
            .group_by(OutlineNode.project_id)
        ).all(),
        "outline",
    )
    apply(
        db.execute(
            select(OutlineNode.project_id, func.count())
            .join(Section, Section.node_id == OutlineNode.id)
            .where(OutlineNode.project_id.in_(ids), Section.words > 0)
            .group_by(OutlineNode.project_id)
        ).all(),
        "written",
    )
    apply(
        db.execute(
            select(Issue.project_id, func.count())
            .where(Issue.project_id.in_(ids), Issue.status == "open")
            .group_by(Issue.project_id)
        ).all(),
        "issues_open",
    )
    return stats


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
    # 延迟导入：治理策略的归一化属于 evaluation.learning（可选依赖）。
    from ..evaluation.learning import load_lesson_policy

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
        "lesson_policy": load_lesson_policy(project),
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


@dataclass
class ProjectRequirementContext:
    """单次 API 请求内、单个项目的条款渲染上下文（R05）。

    一次 load_outline 同时得到大纲正文（供判定）与 ``node_id → heading`` 索引
    （供 section_heading 展示），避免 ``_requirement_payload`` 对每条条款执行
    ``db.get(OutlineNode)`` 的 N+1 查询。项目级判定输入（``project_meta`` /
    ``deadline`` / ``matcher``）同样按请求只算一次，供所有条款复用，避免
    ``judge_for_project`` 逐条重载项目 + 事实台账 + 语义匹配器。上下文按请求
    创建、按 project_id 隔离，请求结束即丢弃，不存在跨请求陈旧缓存。字段带
    默认值以保持构造兼容。
    """

    project_id: str = ""
    outline_nodes: list[dict] = field(default_factory=list)
    node_map: dict[str, str] = field(default_factory=dict)
    project_meta: dict[str, Any] = field(default_factory=dict)
    deadline: date | None = None
    matcher: Any = None

    def heading_of(self, node_id: str | None) -> str:
        return self.node_map.get(node_id or "", "")


def build_requirement_context(
    db: Session, project_id: str, outline_cache: dict[str, list[dict]] | None = None
) -> ProjectRequirementContext:
    """一次请求级加载：大纲正文、heading 索引与项目级判定输入（R05/Stage6）。

    与 ``pipeline.check_project`` 同口径：deadline 从事实台账解析、meta 兜底，
    matcher 由可选 LLM 语义层构造（不可用时安全回退规则）。列表接口每条条款
    复用这些值，项目/台账/匹配器查询次数与条款数无关。
    """

    from ..verdict_llm import build_verdict_matcher

    nodes = get_outline_cached(db, project_id, outline_cache)
    node_map = {
        str(node["id"]): (node.get("heading") or "")
        for node in nodes
        if node.get("id")
    }
    project = db.get(Project, project_id)
    meta = (getattr(project, "meta", None) or {}) if project is not None else {}
    try:
        ledger = load_ledger(db, project_id)
        deadline = resolve_deadline(ledger.facts, meta)
    except Exception:  # noqa: BLE001 - 展示判定不可因台账读取失败而 500
        deadline = resolve_deadline([], meta)
    matcher = build_verdict_matcher(db)
    return ProjectRequirementContext(
        project_id=project_id,
        outline_nodes=nodes,
        node_map=node_map,
        project_meta=meta,
        deadline=deadline,
        matcher=matcher,
    )


def get_outline_cached(
    db: Session, project_id: str, cache: dict[str, list[dict]] | None = None
) -> list[dict]:
    """取项目大纲正文；``cache`` 是**单请求内**按 project_id 共享的缓存（T06）。

    - 同一请求里读多条 requirement 时只加载一次大纲，消除 N+1；
    - 缓存以 project_id 为键，天然隔离不同项目数据；
    - 缓存由调用方按请求创建，请求结束即丢弃，大纲更新后的下一个请求必然
      重新加载，不存在跨请求的陈旧缓存。不传 cache 时行为与直接调用一致（兼容）。
    """

    if cache is None:
        return load_outline(db, project_id)
    if project_id not in cache:
        cache[project_id] = load_outline(db, project_id)
    return cache[project_id]


def judge_for_project(
    db: Session,
    requirement: Requirement,
    nodes: list[dict],
    *,
    deadline: date | None = None,
    matcher: Any = None,
    project_meta: dict[str, Any] | None = None,
) -> Any:
    """与 pipeline.check_project 同口径判定单条条款（截止日期 + 可选 matcher）。

    GET/PATCH /requirements 旧实现直接调 judge()，拿不到事实台账里的招标截止
    日期与可选语义 matcher，和 /check 生成的 SUB 问题判定漂移。这里统一解析
    deadline 并复用 matcher；节点由调用方一次加载后传入，不额外查大纲。

    Stage6：``build_requirement_context`` 已按请求算好 ``project_meta`` /
    ``deadline`` / ``matcher``，列表逐条传入即可复用，不再各自重载项目、台账
    与匹配器。三参数均省略时保留旧的安全回退路径（PATCH 单条/直接调用），
    判定口径、四态逻辑、证据与阈值完全不变。传入 ``project_meta`` 表示调用方
    已解析过项目级输入；此时 ``deadline=None`` 即台账中确无可用截止日期，
    不再重复加载台账。
    """

    from ..verdict_llm import build_verdict_matcher

    if project_meta is None:
        project = db.get(Project, requirement.project_id)
        meta = (getattr(project, "meta", None) or {}) if project is not None else {}
    else:
        meta = project_meta
    if matcher is None:
        matcher = build_verdict_matcher(db)
    if deadline is None and project_meta is None:
        try:
            ledger = load_ledger(db, requirement.project_id)
            deadline = resolve_deadline(ledger.facts, meta)
        except Exception:  # noqa: BLE001 - 展示判定不可因台账读取失败而 500
            deadline = resolve_deadline([], meta)
    return judge_requirement(requirement, nodes, matcher=matcher, deadline=deadline)


def _requirement_payload(
    db: Session,
    requirement: Requirement,
    outline_cache: dict[str, list[dict]] | None = None,
    context: ProjectRequirementContext | None = None,
) -> dict[str, Any]:
    node_id = requirement.node_id
    req_type = classify_requirement(requirement.text or "")
    # 五层判定 + section_heading 共用一次大纲加载（T06/R05）：
    # 有 context 时用请求级预加载的 node_map（消除逐条 db.get 的 N+1）。
    if context is not None:
        nodes = context.outline_nodes
        heading = context.heading_of(node_id)
    else:
        # 兼容未传 context 的调用方：仍按请求缓存加载大纲，heading 从同一批
        # 大纲节点取，而不再逐条 db.get(OutlineNode)。
        nodes = get_outline_cached(db, requirement.project_id, outline_cache)
        heading = ""
        if node_id:
            heading = next(
                (n.get("heading") or "" for n in nodes if str(n.get("id")) == node_id),
                "",
            )
    # Stage6：把请求级上下文里的项目级判定输入透传给判定函数，避免逐条重载
    # 项目/台账/匹配器。仅当上下文确实预加载了这些值时透传，保持手工构造的
    # 极简 context（以及 monkeypatch 三参数 judge）的既有兼容性。
    judge_kwargs: dict[str, Any] = {}
    if context is not None:
        ctx_meta = getattr(context, "project_meta", None)
        ctx_matcher = getattr(context, "matcher", None)
        if ctx_meta is not None and ctx_matcher is not None:
            judge_kwargs = {
                "project_meta": ctx_meta,
                "deadline": getattr(context, "deadline", None),
                "matcher": ctx_matcher,
            }
    verdict = judge_for_project(db, requirement, nodes, **judge_kwargs)
    return {
        "id": requirement.id,
        "kind": requirement.kind,
        "marker": requirement.marker or "",
        "requirement_type": req_type,
        "requirement_type_label": REQ_TYPE_LABELS.get(req_type, "通用条款"),
        "text": requirement.text,
        "mandatory": bool(requirement.mandatory),
        "source_quote": requirement.source_quote or "",
        "page": int(requirement.page or 0),
        "clause_no": requirement.clause_no or "",
        "response_status": requirement.response_status or "pending",
        "response_note": requirement.response_note or "",
        "responded_by": requirement.responded_by or "",
        "responded_at": clock.iso_beijing(requirement.responded_at),
        "response_verdict": requirement.response_verdict or "",
        "response_verdict_label": requirement.response_verdict_label or "",
        "response_verdict_reason": requirement.response_verdict_reason or "",
        "section_id": requirement.section_id,
        "node_id": node_id,
        "section_heading": heading,
        "auto_covered": bool(requirement.auto_covered),
        "verdict": verdict.state,
        "verdict_label": verdict.label,
        "verdict_reason": verdict.reason,
        "verdict_level": verdict.level,
        "verdict_layers": verdict.evidence,
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
    requirement: Requirement,
    status: str,
    note: str,
    username: str,
    verdict: dict | None = None,
) -> None:
    """记录人工确认：人员 / 时间 / 原因 + 确认时的原始自动判定结果。

    ``verdict`` 由调用方用当前正文算出（见 requirement_response_update 的
    ``_verdict_snapshot``），确认后快照写入 Requirement，连同审计详情可完整追溯
    「谁、何时、基于什么判定、为何确认」。
    """

    requirement.response_status = status
    if status == "pending":
        requirement.response_note = ""
        requirement.responded_by = None
        requirement.responded_at = None
        requirement.response_verdict = ""
        requirement.response_verdict_label = ""
        requirement.response_verdict_reason = ""
        return
    requirement.response_note = note
    requirement.responded_by = username or "system"
    requirement.responded_at = clock.utc_now()
    snapshot = verdict or {}
    requirement.response_verdict = snapshot.get("state", "")
    requirement.response_verdict_label = snapshot.get("label", "")
    requirement.response_verdict_reason = snapshot.get("reason", "")


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
                # load_outline 的 body 可能是 None / 纯空白；只看非空文本，
                # 否则所有节点都会显示「无正文」。
                "has_body": bool((body or "").strip()),
                "citations": int(flag.get("citations", 0)),
                "citations_stale": int(flag.get("stale", 0)),
            }
        )
    return chapters


def _effective_chapter_cost(db, user, raw_cost: int) -> int:
    """Mirror ``billing.charge_chapters``' effective price for a raw total.

    ``raw_cost`` is ``chapter_cost * count`` (as passed by every guard caller).
    VIP accounts pay ``ceil(raw_cost * vip_discount)`` from their own/parent
    balance; staff accounts pay nothing.  Keeping this formula here (instead of
    duplicating it at each API call site) prevents a VIP user with enough credits
    for the discounted price from getting a false 402.
    """

    raw = int(raw_cost or 0)
    if raw <= 0:
        return 0
    account = billing.billing_account(db, user)
    if billing.billing_exempt(account):
        return 0
    cost = raw
    if billing.is_vip(account):
        cost = math.ceil(cost * float(billing.billing_settings(db)["vip_discount"]))
    return cost


def _credit_guard(db, user, cost: int) -> None:
    """Fail with 402 before a missing-model 409 so the UI can offer recharge.

    ``cost`` is the undiscounted total; the comparison uses the same effective
    cost that :func:`billing.charge_chapters` will actually debit.
    """
    effective = _effective_chapter_cost(db, user, cost)
    if effective <= 0:
        return
    balance = billing.summary(db, user)["balance"]
    if balance < effective:
        raise _http_error(
            402, f"点数不足，本次需要 {effective} 点，当前 {balance} 点"
        )


def _http_error(status: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status, detail=detail)


def _pending_chapters(db, project_id: str, force: bool = False) -> int:
    """待重生成章节数：与 writer 的恢复口径共用 is_pending。

    过去只看 body 是否存在，超时/截断后落库的残稿、failed/fallback 状态与
    明显不足字数的章节不会被重生成；现在统一由 writer.is_pending 判断。
    """

    nodes = load_outline(db, project_id)
    locked_reviews = {"confirmed", "locked"}
    pending = 0
    for node in nodes:
        # ``force`` must never override human confirmation/locks: a confirmed
        # chapter is not "pending work" even though a full regeneration was
        # requested.
        section_id = str(node.get("section_id") or "")
        section = db.get(Section, section_id) if section_id else None
        review = str(
            (section.review_status if section is not None else None)
            or node.get("review_status")
            or ""
        ).strip().lower()
        if review in locked_reviews:
            continue
        if force or is_pending(node):
            pending += 1
    return pending


def _profile_keys(profile_values: dict[str, Any]) -> list[str]:
    """List the non-empty profile fields for the audit detail."""

    return [key for key, value in profile_values.items() if value not in ("", [], None)]


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


def plugin_validate(
    transport: str, command: str, url: str, *, allow_private_net: bool = False
) -> None:
    try:
        plugins.plugin_validate(
            transport, command, url, allow_private_net=allow_private_net
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def plugin_scope(value: str | None) -> str:
    try:
        return plugins.plugin_scope(value)
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


def textqa_payload(db) -> dict[str, Any]:
    typos = textqa.load_custom(db)
    return {
        "typos": typos,
        "count": len(typos),
        "builtin_count": len(textqa.TYPO_RULES),
    }


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
    """薄封装：节点归属校验统一在 :func:`bidcraft.authorization.authorize_node`。"""

    return authorize_node(db, project_id, node_id)


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

__all__ = [
    'BATCH_STALE_SECONDS',
    'BOQ_UPLOAD_SUFFIXES',
    'EXPORT_GATE_FORMATS',
    'ISSUE_BULK_LIMIT',
    'ISSUE_RESOLVED_STATUSES',
    'ISSUE_TIERS',
    'KB_REINDEX_STALE_SECONDS',
    'MAX_BOQ_UPLOAD_BYTES',
    'MAX_PAGE_SIZE',
    'MAX_TENDER_UPLOAD_BYTES',
    'MEMBER_ROLES',
    'PROJECT_WRITE_ROLES',
    'REQUIREMENT_RESPONSE_MIN_NOTE',
    'REQ_TYPE_LABELS',
    'SEVERITY_ORDER',
    'STARTUP_INTERRUPTED_MESSAGE',
    'TENDER_SOURCE_ROLES',
    'TENDER_UPLOAD_SUFFIXES',
    'UPLOAD_CHUNK_BYTES',
    'UTC',
    'AccountDelete',
    'Any',
    'AuditLog',
    'AuthSession',
    'BaseModel',
    'BatchExportRequest',
    'BatchIdsRequest',
    'BidPackage',
    'BillingProfilePayload',
    'BillingSettingsPayload',
    'BoqAdjustRequest',
    'BoqApplyRequest',
    'BoqPriceRequest',
    'BoqQuotaFillRequest',
    'BoqSnapshot',
    'BoqVersionCreate',
    'BoqVersionReview',
    'BoqVersionSubmit',
    'ChapterReview',
    'ChapterSave',
    'CreditAdjust',
    'Delivery',
    'ExportGateError',
    'ExportRequest',
    'ExportTemplateUpdate',
    'Fact',
    'FactUpdate',
    'Field',
    'File',
    'FileResponse',
    'Forbidden',
    'Form',
    'GenerateRequest',
    'HTTPException',
    'ImageAsset',
    'ImageBatchRequest',
    'ImageCreate',
    'ImagePolicyUpdate',
    'InvoiceCreate',
    'InvoiceReject',
    'Issue',
    'IssueBulkStatusUpdate',
    'IssueStatusUpdate',
    'JSONResponse',
    'Job',
    'JobEvent',
    'KbAssetReview',
    'KbChunk',
    'KbCreate',
    'KbDocMeta',
    'KbDuplicateGroupResolve',
    'KbUpdate',
    'KnowledgeBase',
    'LessonPolicyUpdate',
    'Literal',
    'LoginRequest',
    'Material',
    'MemberCreate',
    'MemberUpdate',
    'ModelConfig',
    'ModelCreate',
    'ModelUpdate',
    'OrderCreate',
    'OutlineChapter',
    'OutlineNode',
    'OutlineRequest',
    'OutlineSave',
    'PackageCreate',
    'PackageUpdate',
    'Parameter',
    'PasswordChange',
    'Path',
    'PersonnelCreate',
    'PersonnelUpdate',
    'Plugin',
    'PluginCreate',
    'PluginUpdate',
    'PricePolicyUpdate',
    'Project',
    'ProjectCreate',
    'ProjectDuplicate',
    'ProjectInstantiate',
    'ProjectMember',
    'ProjectRequirementContext',
    'ProjectUpdate',
    'QualificationCreate',
    'QualificationUpdate',
    'QuotaCreate',
    'QuotaItem',
    'QuotaUpdate',
    'RechargeOrder',
    'RedeemCode',
    'RedeemGenerate',
    'RedeemRequest',
    'RedirectResponse',
    'RegisterRequest',
    'Request',
    'Requirement',
    'RequirementResponseUpdate',
    'Response',
    'Risk',
    'RulePackageDocument',
    'RulePackageValidateRequest',
    'ScoreRow',
    'ScoresUpdate',
    'ScoringItem',
    'Section',
    'Session',
    'SourceFile',
    'StreamingResponse',
    'SubAccountCreate',
    'SubAccountUpdate',
    'TermCreate',
    'TermItem',
    'TermUpdate',
    'TextQaUpdate',
    'UploadFile',
    'User',
    'UserCreate',
    'UserUpdate',
    'VipGrant',
    'WechatConfigUpdate',
    '__version__',
    '_active_kb_reindex',
    '_apply_issue_status',
    '_apply_requirement_status',
    '_audit_item',
    '_batch_projects',
    '_bid_package',
    '_bid_package_payload',
    '_bid_packages',
    '_boq_package_ids',
    '_cancel_stale_jobs',
    '_chapter_payload',
    '_clamp_limit',
    '_credit_guard',
    '_discard_source',
    '_effective_chapter_cost',
    '_fail_job',
    '_generation_client',
    '_http_error',
    '_image_http_error',
    '_iso',
    '_issue_is_open',
    '_issue_node_map',
    '_issue_payload',
    '_issue_tier',
    '_job',
    '_job_refund',
    '_knowledge_provider',
    '_member',
    '_member_payload',
    '_node',
    '_outline_payload',
    '_package_usage',
    '_page_bounds',
    '_pending_chapters',
    '_profile_keys',
    '_project',
    '_project_payload',
    '_project_stats',
    '_recover_interrupted_jobs',
    '_reject_sub_billing',
    '_report_payload',
    '_require_owner',
    '_require_write',
    '_requirement_payload',
    '_resolve_boq_package',
    '_risk_overview_payload',
    '_rule_package_admin_payload',
    '_run_batch_check_job',
    '_run_batch_export_job',
    '_run_evaluation_job',
    '_run_generation_job',
    '_run_image_batch_job',
    '_run_kb_reindex_job',
    '_run_similarity_job',
    '_safe_filename',
    '_save_upload',
    '_source_payload',
    '_validate_issue_note',
    '_validate_requirement_note',
    '_wechat_login',
    'accounts',
    'add_source',
    'audit',
    'auth',
    'authorize_job',
    'authorize_node',
    'authorize_project',
    'authorize_projects',
    'billing',
    'billing_user',
    'boq',
    'build_outline',
    'build_requirement_context',
    'check_owner',
    'check_package_document',
    'check_package_name',
    'check_project',
    'check_write',
    'classify_requirement',
    'clock',
    'compliance',
    'count_words',
    'create_project',
    'current_user',
    'date',
    'datetime',
    'delete',
    'document_package_name',
    'duplicate_project',
    'encrypt_secret',
    'enforce_export_gate',
    'export_docx',
    'export_markdown',
    'func',
    'generate_document',
    'get_outline_cached',
    'hash_password',
    'images',
    'init_db',
    'invoices',
    'io',
    'issue_is_open',
    'jobqueue',
    'jobs',
    'knowledge',
    'knowledge_assets',
    'ledger_gate',
    'ledger_summary',
    'lessons_payload',
    'list_packages',
    'load_ledger',
    'load_outline',
    'load_package',
    'logger',
    'logging',
    'material_policy',
    'math',
    'mimetypes',
    'model_config',
    'package_audit',
    'package_store',
    'parse_project',
    'personnel',
    'plugin_env',
    'plugin_items',
    'plugin_name',
    'plugin_pack',
    'plugin_transport',
    'plugin_validate',
    'plugins',
    'profile',
    'provenance',
    'qualifications',
    'quota_name',
    'quota_price',
    'quote',
    're',
    'record_audit',
    'require_own_order',
    'require_super',
    'reset_current_user',
    'rule_package_record',
    'save_outline',
    'scoring',
    'select',
    'session_scope',
    'set_current_user',
    'set_session_cookie',
    'settings',
    'shutil',
    'tempfile',
    'terms',
    'textqa',
    'textqa_payload',
    'threading',
    'time',
    'timedelta',
    'unset_default_kb',
    'unset_default_model',
    'update_fact',
    'user_docs',
    'validate_kb',
    'validate_kind',
    'validate_package_text',
    'validate_startup',
    'verify_password',
    'wechat',
    'wechat_config_payload',
    'wechatpay',
]
