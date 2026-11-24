"""HTTP API and B/S web host (optional extra: ``pip install -e '.[api]'``).

The API is a thin wrapper over the same pipeline used by the CLI. It uses
cookie sessions and role-based access control; there is no telemetry. Bind to
127.0.0.1 for single-user mode, or put it behind TLS and your own network
controls when serving a team."""

import ipaddress
import uuid
from collections import OrderedDict, deque
from threading import Lock

from ..config import configure_logging, reset_request_id, set_request_id
from .shared import (  # noqa: F401 - 门面：保持 api.<name> 名字空间与 monkeypatch 语义
    BATCH_STALE_SECONDS,
    BOQ_UPLOAD_SUFFIXES,
    EXPORT_GATE_FORMATS,
    ISSUE_BULK_LIMIT,
    ISSUE_RESOLVED_STATUSES,
    ISSUE_TIERS,
    KB_REINDEX_STALE_SECONDS,
    MAX_BOQ_UPLOAD_BYTES,
    MAX_PAGE_SIZE,
    MAX_TENDER_UPLOAD_BYTES,
    MEMBER_ROLES,
    PROJECT_WRITE_ROLES,
    REQUIREMENT_RESPONSE_MIN_NOTE,
    SEVERITY_ORDER,
    STARTUP_INTERRUPTED_MESSAGE,
    TENDER_SOURCE_ROLES,
    TENDER_UPLOAD_SUFFIXES,
    UPLOAD_CHUNK_BYTES,
    UTC,
    AccountDelete,
    Any,
    AuditLog,
    AuthSession,
    BaseModel,
    BatchExportRequest,
    BatchIdsRequest,
    BidPackage,
    BillingProfilePayload,
    BillingSettingsPayload,
    BoqAdjustRequest,
    BoqApplyRequest,
    BoqPriceRequest,
    BoqQuotaFillRequest,
    BoqSnapshot,
    BoqVersionCreate,
    BoqVersionReview,
    BoqVersionSubmit,
    ChapterReview,
    ChapterSave,
    CreditAdjust,
    Delivery,
    ExportGateError,
    ExportRequest,
    ExportTemplateUpdate,
    Fact,
    FactUpdate,
    Field,
    File,
    FileResponse,
    Forbidden,
    Form,
    GenerateRequest,
    HTTPException,
    ImageAsset,
    ImageBatchRequest,
    ImageCreate,
    ImagePolicyUpdate,
    InvoiceCreate,
    InvoiceReject,
    Issue,
    IssueBulkStatusUpdate,
    IssueStatusUpdate,
    Job,
    JobEvent,
    JSONResponse,
    KbChunk,
    KbCreate,
    KbDocMeta,
    KbUpdate,
    KnowledgeBase,
    Literal,
    LoginRequest,
    Material,
    MemberCreate,
    MemberUpdate,
    ModelConfig,
    ModelCreate,
    ModelUpdate,
    OrderCreate,
    OutlineChapter,
    OutlineNode,
    OutlineRequest,
    OutlineSave,
    PackageCreate,
    PackageUpdate,
    Parameter,
    PasswordChange,
    Path,
    PersonnelCreate,
    PersonnelUpdate,
    Plugin,
    PluginCreate,
    PluginUpdate,
    PricePolicyUpdate,
    Project,
    ProjectCreate,
    ProjectDuplicate,
    ProjectInstantiate,
    ProjectMember,
    ProjectUpdate,
    QualificationCreate,
    QualificationUpdate,
    QuotaCreate,
    QuotaItem,
    QuotaUpdate,
    RechargeOrder,
    RedeemCode,
    RedeemGenerate,
    RedeemRequest,
    RedirectResponse,
    RegisterRequest,
    Request,
    Requirement,
    RequirementResponseUpdate,
    Response,
    Risk,
    RulePackageDocument,
    RulePackageValidateRequest,
    ScoreRow,
    ScoresUpdate,
    ScoringItem,
    Section,
    Session,
    SourceFile,
    StreamingResponse,
    SubAccountCreate,
    SubAccountUpdate,
    TermCreate,
    TermItem,
    TermUpdate,
    TextQaUpdate,
    UploadFile,
    User,
    UserCreate,
    UserUpdate,
    VipGrant,
    WechatConfigUpdate,
    __version__,
    _active_kb_reindex,
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
    _fail_job,
    _generation_client,
    _http_error,
    _image_http_error,
    _iso,
    _issue_is_open,
    _issue_node_map,
    _issue_payload,
    _issue_tier,
    _job,
    _job_refund,
    _knowledge_provider,
    _member,
    _member_payload,
    _node,
    _outline_payload,
    _package_usage,
    _page_bounds,
    _pending_chapters,
    _profile_keys,
    _project,
    _project_payload,
    _project_stats,
    _recover_interrupted_jobs,
    _reject_sub_billing,
    _report_payload,
    _require_owner,
    _require_write,
    _requirement_payload,
    _resolve_boq_package,
    _risk_overview_payload,
    _rule_package_admin_payload,
    _run_batch_check_job,
    _run_batch_export_job,
    _run_generation_job,
    _run_image_batch_job,
    _run_kb_reindex_job,
    _safe_filename,
    _save_upload,
    _source_payload,
    _validate_issue_note,
    _validate_requirement_note,
    _wechat_login,
    accounts,
    add_source,
    audit,
    auth,
    authorize_job,
    authorize_node,
    authorize_project,
    authorize_projects,
    billing,
    billing_user,
    boq,
    build_outline,
    check_owner,
    check_package_document,
    check_package_name,
    check_project,
    check_write,
    clock,
    compliance,
    count_words,
    create_project,
    current_user,
    date,
    datetime,
    delete,
    document_package_name,
    duplicate_project,
    encrypt_secret,
    enforce_export_gate,
    export_docx,
    export_markdown,
    func,
    generate_document,
    hash_password,
    images,
    init_db,
    invoices,
    io,
    issue_is_open,
    jobqueue,
    jobs,
    knowledge,
    ledger_gate,
    ledger_summary,
    list_packages,
    load_ledger,
    load_outline,
    load_package,
    logger,
    logging,
    material_policy,
    math,
    mimetypes,
    model_config,
    package_audit,
    package_store,
    parse_project,
    personnel,
    plugin_env,
    plugin_items,
    plugin_name,
    plugin_pack,
    plugin_transport,
    plugin_validate,
    plugins,
    profile,
    provenance,
    qualifications,
    quota_name,
    quota_price,
    quote,
    re,
    record_audit,
    require_own_order,
    require_super,
    reset_current_user,
    rule_package_record,
    save_outline,
    scoring,
    select,
    session_scope,
    set_current_user,
    set_session_cookie,
    settings,
    shutil,
    tempfile,
    terms,
    textqa,
    textqa_payload,
    threading,
    time,
    timedelta,
    unset_default_kb,
    unset_default_model,
    update_fact,
    user_docs,
    validate_kb,
    validate_kind,
    validate_package_text,
    validate_startup,
    verify_password,
    wechat,
    wechat_config_payload,
    wechatpay,
)

dist = Path(__file__).resolve().parent.parent / "web_dist"


class _RateLimiter:
    """进程内滑动窗口限流：TTL 过期 + 硬上限 LRU 淘汰。

    旧实现只删空 deque，但每次检查都会先 append，bucket 永不为空，长期运行
    会无界增长并在持锁时全表扫描。这里按最近活跃顺序维护 bucket，定期清理
    过期项，并把 key 总数限制在 ``MAX_KEYS`` 以内（淘汰最久未使用的 bucket）。
    """

    MAX_KEYS = 2048
    SWEEP_EVERY_CHECKS = 128
    SWEEP_INTERVAL_SECONDS = 30.0

    def __init__(self, window_seconds: int | None = None) -> None:
        self.window_seconds = window_seconds
        self._buckets: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock = Lock()
        self._checks = 0
        self._swept_at = time.monotonic()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buckets)

    def _window(self) -> int:
        return self.window_seconds or settings.rate_limit_window_seconds

    def check(self, bucket: str, key: str, limit: int) -> bool:
        now = time.monotonic()
        cutoff = now - self._window()
        name = (bucket, key)
        with self._lock:
            queue = self._buckets.get(name)
            if queue is None:
                queue = deque()
                self._buckets[name] = queue
            else:
                self._buckets.move_to_end(name)
            while queue and queue[0] <= cutoff:
                queue.popleft()
            allowed = len(queue) < limit
            if allowed:
                queue.append(now)
            self._checks += 1
            if (
                self._checks % self.SWEEP_EVERY_CHECKS == 0
                or now - self._swept_at >= self.SWEEP_INTERVAL_SECONDS
            ):
                self._sweep(now)
            return allowed

    def _sweep(self, now: float) -> None:
        cutoff = now - self._window()
        for name, values in list(self._buckets.items()):
            while values and values[0] <= cutoff:
                values.popleft()
            if not values:
                self._buckets.pop(name, None)
        # LRU 淘汰：最多保留 MAX_KEYS 个 bucket，避免恶意 IP 撑爆内存；
        # 全局 bucket（key="*"）不参与淘汰，防止刷 IP 绕过全局上限。
        while len(self._buckets) > self.MAX_KEYS:
            for name in list(self._buckets):
                if name[1] == "*":
                    self._buckets.move_to_end(name)
                    continue
                self._buckets.pop(name, None)
                break
            else:
                break
        self._swept_at = now


def create_app():
    # 基础日志先于启动校验配置，确保 prod 校验失败的原因也被记录。
    configure_logging()
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
    # 启动恢复不在这里做：create_app() 会被每个 ASGI worker 进程调用，
    # 多实例同时恢复会互相影响。恢复改由 worker 进程入口显式执行
    # （`bidcraft serve` / `bidcraft worker` 都会调用 recover_on_startup()）。
    # 每次 create_app() 都重新读取开关：测试与部署会在构造前改 settings，
    # 若在模块加载时缓存，后续 create_app() 会拿到过期值。
    docs_on = settings.docs_enabled
    from contextlib import asynccontextmanager

    from .. import jobqueue as _jobqueue

    @asynccontextmanager
    async def _lifespan(application):  # noqa: ANN001
        # 优雅退出：HTTP drain 完成后通知 worker 停止认领并等待在途任务收尾。
        # 分离部署里 API 进程通常不跑 worker，wait_for_workers 立即返回。
        yield
        if _jobqueue.workers_running():
            _jobqueue.request_shutdown()
            _jobqueue.wait_for_workers(timeout=settings.shutdown_grace_seconds)

    app = FastAPI(
        title="BidCraft",
        version=__version__,
        description="投标文件编制系统",
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
        lifespan=_lifespan,
    )
    app.state.started_at = datetime.now(UTC)

    if not docs_on:

        @app.get("/docs", include_in_schema=False)
        @app.get("/redoc", include_in_schema=False)
        @app.get("/openapi.json", include_in_schema=False)
        def docs_disabled() -> None:
            raise HTTPException(status_code=404, detail="接口文档已关闭")

    # ---- 边缘防护：请求 id → 客户端 IP / CSRF / 限流 → 鉴权 → 审计 → 安全头 ----
    # 每个 create_app() 持有独立计数器：测试可反复建 app，进程重启即重置；
    # 多 worker 下只是"尽力而为"的进程内限流，生产仍应在反代层再限一次。

    _login_test_paths = frozenset({"/auth/login"})
    _sensitive_paths = frozenset(
        {
            "/auth/register",
            "/auth/password",
            "/auth/password/reset",
            "/auth/reset-password",
            "/auth/forgot-password",
            "/billing/redeem",
            "/admin/wechat/test",
        }
    )
    _model_test_re = re.compile(r"^/admin/(?:models|kbs|plugins)/[^/]+/test$")
    _limiter = _RateLimiter()

    def _client_address(request: Request) -> str:
        """信任反代时取 X-Forwarded-For 最左值，否则取直连地址。"""

        if settings.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                candidate = forwarded.split(",", 1)[0].strip()
                if candidate and len(candidate) <= 64 and not any(
                    char in candidate for char in "\r\n"
                ):
                    try:
                        ipaddress.ip_address(candidate)
                    except ValueError:
                        pass  # 非法值不采信，退回直连地址
                    else:
                        return candidate
        return request.client.host if request.client else ""

    def _expected_origin(request: Request) -> str:
        """CSRF 同源判定用的公开 origin；转发头只在信任代理时采信。"""

        scheme, host = request.url.scheme, request.url.netloc
        if settings.trust_proxy:
            scheme = request.headers.get("x-forwarded-proto", scheme).split(",", 1)[0].strip() or scheme
            host = request.headers.get("x-forwarded-host", host).split(",", 1)[0].strip() or host
        return f"{scheme}://{host}".rstrip("/").lower()

    def _rate_kind(path: str) -> str | None:
        if path in _login_test_paths:
            return "login"
        if path in _sensitive_paths or _model_test_re.match(path):
            return "sensitive"
        return None

    def _rate_check(bucket: str, key: str, limit: int) -> bool:
        return _limiter.check(bucket, key, limit)

    # 注意：Starlette 的 user_middleware 是"后注册者在外层"。request_id 必须最后
    # 注册，才能给 edge_guard/auth_guard 提前返回的 403/401/429 也带上 X-Request-ID。
    @app.middleware("http")
    async def edge_guard(request: Request, call_next):
        path = request.url.path
        ip = _client_address(request)
        if request.client is not None and ip != request.client.host:
            # 同步 scope，后续路由里的 request.client.host 也拿到真实来源 IP。
            request.scope["client"] = (ip, request.client.port)

        # Cookie 会话下的状态变更请求：无 Origin 头（普通脚本/curl/TestClient）放行；
        # 显式 Origin 必须同源；Sec-Fetch-Site 只接受 same-origin/none。
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("cookie"):
            origin = request.headers.get("origin")
            if origin:
                candidate = origin.strip().rstrip("/").lower()
                if candidate != _expected_origin(request) and candidate not in settings.public_origins:
                    return JSONResponse(status_code=403, content={"detail": "跨站请求被拒绝"})
            else:
                site = (request.headers.get("sec-fetch-site") or "").strip().lower()
                if site and site not in {"same-origin", "none"}:
                    return JSONResponse(status_code=403, content={"detail": "跨站请求被拒绝"})

        if settings.rate_limit_enabled:
            kind = _rate_kind(path)
            if kind == "login":
                ip_limit = settings.rate_limit_login_ip
                global_limit = settings.rate_limit_login_global
            elif kind == "sensitive":
                ip_limit = settings.rate_limit_sensitive_ip
                global_limit = settings.rate_limit_sensitive_global
            else:
                ip_limit = global_limit = 0
            if ip_limit:
                checks = (
                    (kind, ip or "unknown", ip_limit),
                    (f"{kind}-global", "*", global_limit),
                )
                for bucket, key, limit in checks:
                    if not _rate_check(bucket, key, limit):
                        return JSONResponse(
                            status_code=429,
                            content={"detail": "请求过于频繁，请稍后再试"},
                            headers={"Retry-After": str(settings.rate_limit_window_seconds)},
                        )
        return await call_next(request)

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
            "/enterprise",
            "/commercial",
            "/similarity",
            "/orchestration",
            "/agents",
            "/collaboration",
            "/evaluation",
        )
        if not path.startswith(api_prefixes):
            return await call_next(request)
        with session_scope() as db:
            user = auth.resolve_session(db, request.cookies.get(auth.SESSION_COOKIE))
            if user is None:
                return JSONResponse(status_code=401, content={"detail": "请先登录"})
            payload = auth.user_payload(user)
            request.state.user = payload
            # 身份只在本请求内有效：拿到 token 后用 try/finally 还原，避免泄漏到
            # 同一 context 里后续执行的工作；后台任务另走显式参数，不依赖这里。
            token = set_current_user(payload)
        try:
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
        finally:
            reset_current_user(token)

    @app.middleware("http")
    async def auth_audit(request: Request, call_next):
        """补齐退出/改密这两个原本无审计行的身份操作（口令绝不入审计）。"""

        response = await call_next(request)
        action = None
        if request.url.path == "/auth/password" and response.status_code < 400:
            action = "auth.password_change"
        if action:
            actor = getattr(request.state, "user", None)
            try:
                with session_scope() as db:
                    record_audit(
                        db,
                        None,
                        action,
                        {"username": (actor or {}).get("username", "")},
                        actor=actor,
                    )
            except Exception:  # noqa: BLE001 - 审计失败不影响已完成的改密
                logger.warning("审计写入失败 action=%s", action, exc_info=True)
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # SPA 依赖 React 内联 style 属性与 Vite 运行时注入，style-src 保留
        # 'unsafe-inline'；脚本仍只允许 'self'，不引入 remote code execution。
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        # 纵深防御：禁用敏感浏览器能力，并阻止跨站把本站作为弹窗/子资源嵌入。
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if settings.secure_cookie:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    # 最后注册 → 最外层：包括边缘防护与鉴权中间件提前返回的 4xx 在内的所有响应
    # 都会经过这里，X-Request-ID 与日志关联 id 不再在早期 401/403/429 上丢失。
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        raw = (
            request.headers.get("x-request-id")
            or request.headers.get("x-correlation-id")
            or uuid.uuid4().hex
        )
        # 仅允许可安全回显的 ASCII 字符，避免响应头注入。
        rid = "".join(char for char in raw[:128] if 33 <= ord(char) <= 126) or uuid.uuid4().hex
        request.state.request_id = rid
        token = set_request_id(rid)
        try:
            response = await call_next(request)
            response.headers.setdefault("X-Request-ID", rid)
            return response
        finally:
            reset_request_id(token)

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
        collaboration as _routes_collaboration,
    )
    from .routes import (
        commercial as _routes_commercial,
    )
    from .routes import (
        enterprise as _routes_enterprise,
    )
    from .routes import (
        evaluation as _routes_evaluation,
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
        orchestration as _routes_orchestration,
    )
    from .routes import (
        packages as _routes_packages,
    )
    from .routes import (
        projects as _routes_projects,
    )
    from .routes import (
        similarity as _routes_similarity,
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
    app.include_router(_routes_enterprise.router)
    app.include_router(_routes_commercial.router)
    app.include_router(_routes_similarity.router)
    app.include_router(_routes_evaluation.router)
    app.include_router(_routes_orchestration.router)
    app.include_router(_routes_collaboration.router)

    # 聚合可观测性：把 DB 计算的全局指标挂到既有 /admin/health 响应上（不新增路由，
    # 公开 /health 保持最小化）。若路由侧已提供 global_metrics 则不覆盖。
    from ..health import global_metrics

    def _iter_routes(_routes):
        for _route in _routes:
            yield _route
            # FastAPI 0.16x 的 include_router 把子路由收在 _IncludedRouter 里，
            # 真正处理请求的是 effective context 中的 dependant。
            yield from getattr(_route, "effective_route_contexts", lambda: ())()

    _wrapped: set[int] = set()
    for _route in _iter_routes(app.routes):
        if getattr(_route, "path", "") != "/admin/health":
            continue
        _dependant = getattr(_route, "dependant", None)
        if _dependant is None or id(_dependant) in _wrapped:
            continue
        _endpoint = _dependant.call

        def _admin_health_with_metrics(*args, _endpoint=_endpoint, **kwargs):
            payload = _endpoint(*args, **kwargs)
            if isinstance(payload, dict) and "global_metrics" not in payload:
                with session_scope() as db:
                    payload["global_metrics"] = global_metrics(db)
            return payload

        _dependant.call = _admin_health_with_metrics
        _wrapped.add(id(_dependant))











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
