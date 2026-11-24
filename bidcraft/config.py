from __future__ import annotations

import base64
import contextvars
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("bidcraft.config")

#: 请求关联 id（由 HTTP 中间件写入，日志格式自动带上；无请求时为 "-"）。
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bidcraft_request_id", default=""
)

#: 生产环境管理员口令的最短长度与明显占位值。占位口令一律拒绝，避免
#: ``deploy/.env.example`` 里的示例值被直接带上生产。
MIN_ADMIN_PASSWORD_LENGTH = 12
WEAK_ADMIN_PASSWORDS = frozenset(
    {
        "admin",
        "admin123",
        "admin12345",
        "bidcraft",
        "changeme",
        "change-me",
        "change_me",
        "default",
        "letmein",
        "password",
        "password123",
        "placeholder",
        "secret",
        "test",
        "test-admin",
        "your-password",
    }
)

#: 崩溃窗口未结扣费标记（pending_charges）的默认对账 TTL（秒）。
DEFAULT_PENDING_CHARGE_TTL_SECONDS = 1800

#: 进程内滑动窗口限流的保守默认值（可通过环境变量调整）。
DEFAULT_RATE_WINDOW_SECONDS = 300
DEFAULT_LOGIN_IP_LIMIT = 60
DEFAULT_LOGIN_GLOBAL_LIMIT = 1000
DEFAULT_SENSITIVE_IP_LIMIT = 30
DEFAULT_SENSITIVE_GLOBAL_LIMIT = 300

#: 运行环境。仅影响配置校验强度与日志提示，不改变业务行为。
#: - dev/test：缺失可选参数只记 warning，不阻断启动（测试与本地开发）
#: - prod：缺失关键参数（支付/模型/管理员密码）直接阻断启动
ENVIRONMENTS = ("dev", "test", "prod")
DEFAULT_ENV = "dev"


def _env_name() -> str:
    raw = os.getenv("BIDCRAFT_ENV", DEFAULT_ENV).strip().lower()
    return raw if raw in ENVIRONMENTS else DEFAULT_ENV


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_weak_password(value: str) -> bool:
    """明显的占位/弱口令判定（不打印口令本身）。"""

    normalized = (value or "").strip().lower()
    if not normalized:
        return False
    if normalized in WEAK_ADMIN_PASSWORDS:
        return True
    if ("change" in normalized and "me" in normalized) or "placeholder" in normalized:
        return True
    return bool(normalized.isdigit() or len(set(normalized)) <= 2)


def redact_secret(value: str | None, keep: int = 4) -> str:
    """把密钥/口令脱敏成 ``ab****cd`` 形式，用于日志与健康检查展示。

    空值返回空串；短于 ``keep * 2`` 的值全量打码，不泄露长度以外的信息。
    """

    text = value or ""
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}{'*' * 4}{text[-keep:]}"


def db_backend(url: str) -> str:
    """从连接串读出方言名，未知/畸形串一律按 sqlite 处理（保守）。"""

    raw = (url or "").strip().lower()
    for prefix in ("postgresql+psycopg", "postgresql+psycopg2", "postgres+psycopg"):
        if raw.startswith(prefix):
            return "postgresql"
    if raw.startswith(("postgresql://", "postgres://", "jdbc:postgresql")):
        return "postgresql"
    return "sqlite"


def normalize_db_url(url: str) -> str:
    """统一 PostgreSQL 连接串的驱动名，便于只安装一个驱动。

    SQLAlchemy 的 ``postgresql://`` 默认走 psycopg2，``postgres://`` 不是合法
    方言名；这里都归一到 ``postgresql+psycopg://``（psycopg 3，``postgres`` extra）。
    SQLite 与其它串原样返回。
    """

    raw = (url or "").strip()
    lowered = raw.lower()
    if lowered.startswith(("postgresql://", "postgres://")):
        return "postgresql+psycopg://" + raw.split("://", 1)[1]
    return raw


def redact_db_url(url: str) -> str:
    """把连接串里的口令打码，用于 /admin/health 与日志，绝不返回明文。"""

    raw = (url or "").strip()
    if not raw:
        return ""
    scheme, sep, rest = raw.partition("://")
    if not sep or "@" not in rest:
        return raw
    userinfo, at, host = rest.rpartition("@")
    if ":" not in userinfo:
        return raw
    user, _colon, _password = userinfo.partition(":")
    return f"{scheme}://{user}:****@{host}" if at else raw


@dataclass(slots=True)
class Settings:
    """Runtime settings.

    Security posture: the process makes no outbound connection unless a model
    endpoint is explicitly configured. There is no telemetry, no analytics and
    no remote error reporting anywhere in this codebase.
    """

    env: str = field(default_factory=_env_name)
    home: Path = field(default_factory=lambda: Path(os.getenv("BIDCRAFT_HOME", ".bidcraft")))
    # 数据库连接串。默认 SQLite；设置 BIDCRAFT_DB_URL 可切到 PostgreSQL。
    db_url: str = field(default_factory=lambda: os.getenv("BIDCRAFT_DB_URL", "").strip())
    model_base_url: str = field(default_factory=lambda: os.getenv("BIDCRAFT_MODEL_BASE_URL", ""))
    model_api_key: str = field(default_factory=lambda: os.getenv("BIDCRAFT_MODEL_API_KEY", ""))
    model_name: str = field(default_factory=lambda: os.getenv("BIDCRAFT_MODEL_NAME", ""))
    model_temperature: float = field(
        default_factory=lambda: float(os.getenv("BIDCRAFT_MODEL_TEMPERATURE", "0.4"))
    )
    model_timeout: float = field(
        default_factory=lambda: float(os.getenv("BIDCRAFT_MODEL_TIMEOUT", "180"))
    )
    plugin_timeout: float = field(
        default_factory=lambda: float(os.getenv("BIDCRAFT_PLUGIN_TIMEOUT", "20"))
    )
    offline: bool = field(default_factory=lambda: _bool("BIDCRAFT_OFFLINE"))
    #: 实质性条款判定是否启用 LLM 语义匹配；默认关闭，完全走本地规则。
    #: 关闭/模型不可用时自动回退 RuleSemanticMatcher，数字与等级仍由约束层裁决。
    verdict_llm: bool = field(default_factory=lambda: _bool("BIDCRAFT_VERDICT_LLM"))
    admin_username: str = field(default_factory=lambda: os.getenv("BIDCRAFT_ADMIN_USER", "admin"))
    admin_password: str = field(default_factory=lambda: os.getenv("BIDCRAFT_ADMIN_PASSWORD", ""))
    session_days: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_SESSION_DAYS", "7"))
    )
    secure_cookie: bool = field(default_factory=lambda: _bool("BIDCRAFT_SECURE_COOKIE"))
    docs_enabled: bool = field(default_factory=lambda: _bool("BIDCRAFT_DOCS_ENABLED"))
    #: ``minimal`` / ``public`` → 公开探活只返回最小字段；其它值（如 private/full）
    #: 认为仅供内网使用，/health 才附带版本、绝对路径与库内计数。无论取值如何，
    #: /health 本身始终可达，供容器/负载均衡探活。
    health_public: bool = field(
        default_factory=lambda: os.getenv("BIDCRAFT_HEALTH_PUBLIC", "minimal")
        .strip().lower()
        in {"minimal", "public"}
    )
    #: 反向代理后面才开启：信任 X-Forwarded-For 作为客户端 IP（限流与审计用）。
    #: 直连部署保持关闭，避免伪造 IP 绕过限流。
    trust_proxy: bool = field(default_factory=lambda: _bool("BIDCRAFT_TRUST_PROXY"))
    #: CSRF 校验额外接受的公开 Origin（逗号分隔，如 https://a.example,https://b.example）。
    #: 同源判定仍优先；这里只用于反代/多域名场景，且仅在请求带 Cookie 的状态变更时生效。
    public_origin: str = field(default_factory=lambda: os.getenv("BIDCRAFT_PUBLIC_ORIGIN", ""))
    #: 内置备份额外打包的数据目录（相对 BIDCRAFT_HOME，逗号分隔）；设为空串可关闭。
    backup_data_dirs: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_BACKUP_DIRS", "projects,packages,output,docs")
    )
    #: 多实例（PostgreSQL）部署须在所有进程使用同一个 Fernet 主密钥。默认仍从
    #: BIDCRAFT_HOME/secret.key 读取；设置本变量则优先使用环境变量（不落盘）。
    secret_key: str = field(default_factory=lambda: os.getenv("BIDCRAFT_SECRET_KEY", "").strip())
    #: 基础日志：text 为带时间与请求 id 的普通格式，json 为单行结构化输出。
    log_level: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_LOG_LEVEL", "INFO").strip().upper()
    )
    log_format: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_LOG_FORMAT", "text").strip().lower()
    )
    #: 认证敏感端点的进程内滑动窗口限流。只在单进程内生效，多 worker 请配合
    #: 反向代理层限流；默认值特意留足余量，不影响压测与既有客户端。
    rate_limit_enabled: bool = field(default_factory=lambda: _bool("BIDCRAFT_RATE_LIMIT", True))
    rate_limit_window_seconds: int = field(
        default_factory=lambda: int(
            os.getenv("BIDCRAFT_RATE_WINDOW_SECONDS", str(DEFAULT_RATE_WINDOW_SECONDS))
        )
    )
    rate_limit_login_ip: int = field(
        default_factory=lambda: int(
            os.getenv("BIDCRAFT_RATE_LOGIN_IP", str(DEFAULT_LOGIN_IP_LIMIT))
        )
    )
    rate_limit_login_global: int = field(
        default_factory=lambda: int(
            os.getenv("BIDCRAFT_RATE_LOGIN_GLOBAL", str(DEFAULT_LOGIN_GLOBAL_LIMIT))
        )
    )
    rate_limit_sensitive_ip: int = field(
        default_factory=lambda: int(
            os.getenv("BIDCRAFT_RATE_SENSITIVE_IP", str(DEFAULT_SENSITIVE_IP_LIMIT))
        )
    )
    rate_limit_sensitive_global: int = field(
        default_factory=lambda: int(
            os.getenv("BIDCRAFT_RATE_SENSITIVE_GLOBAL", str(DEFAULT_SENSITIVE_GLOBAL_LIMIT))
        )
    )
    wechat_app_id: str = field(default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_APP_ID", ""))
    wechat_app_secret: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_APP_SECRET", "")
    )
    wechat_redirect_uri: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_REDIRECT_URI", "")
    )
    wechat_mock: bool = field(default_factory=lambda: _bool("BIDCRAFT_WECHAT_MOCK"))
    wechat_pay_mch_id: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_PAY_MCH_ID", "")
    )
    wechat_pay_serial_no: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_PAY_SERIAL_NO", "")
    )
    wechat_pay_private_key: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_PAY_PRIVATE_KEY", "")
    )
    wechat_pay_platform_cert: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_PAY_PLATFORM_CERT", "")
    )
    wechat_pay_apiv3_key: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_PAY_APIV3_KEY", "")
    )
    wechat_pay_notify_url: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_WECHAT_PAY_NOTIFY_URL", "")
    )
    wechat_pay_mock: bool = field(default_factory=lambda: _bool("BIDCRAFT_WECHAT_PAY_MOCK"))
    signup_credits: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_SIGNUP_CREDITS", "5"))
    )
    chapter_cost: int = field(default_factory=lambda: int(os.getenv("BIDCRAFT_CHAPTER_COST", "1")))
    max_concurrent_jobs: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_MAX_JOBS", "2"))
    )
    # DB 驱动任务队列：全局排队上限与单用户排队配额（背压），0 表示不限制。
    max_queued_jobs: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_MAX_QUEUED_JOBS", "100"))
    )
    max_user_queued_jobs: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_MAX_USER_QUEUED_JOBS", "10"))
    )
    # 任务租约：认领后 lease_expires_at=now+lease；心跳每 heartbeat 秒续期。
    job_lease_seconds: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_JOB_LEASE_SECONDS", "180"))
    )
    job_heartbeat_seconds: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_JOB_HEARTBEAT_SECONDS", "60"))
    )
    # 失败/中断后的最大认领次数（含首次），超过则失败。
    job_max_attempts: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_JOB_MAX_ATTEMPTS", "3"))
    )
    # 崩溃窗口未结扣费的对账 TTL：只有 pending 超过此时长、且项目没有在途任务时才退还，
    # 避免长耗时生成被误退后白做。启动恢复、worker reaper 周期扫描与
    # `bidcraft reconcile-charges` 都使用此值。
    pending_charge_ttl_seconds: int = field(
        default_factory=lambda: int(
            os.getenv("BIDCRAFT_PENDING_CHARGE_TTL", str(DEFAULT_PENDING_CHARGE_TTL_SECONDS))
        )
    )
    # 是否在提交任务时于本进程拉起 worker。单进程（bidcraft serve）保持 True；
    # API/Worker 分离部署时设 BIDCRAFT_EMBEDDED_WORKERS=0，API 进程只入队不执行，
    # 由独立的 `bidcraft worker` 进程认领任务。
    embedded_workers: bool = field(
        default_factory=lambda: _bool("BIDCRAFT_EMBEDDED_WORKERS", True)
    )
    # 优雅退出：SIGTERM 后等待在途任务在检查点收尾的最长时间（秒）。
    shutdown_grace_seconds: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_SHUTDOWN_GRACE_SECONDS", "30"))
    )
    ocr_enabled: bool = field(default_factory=lambda: _bool("BIDCRAFT_OCR", True))
    mineru_url: str = field(default_factory=lambda: os.getenv("BIDCRAFT_MINERU_URL", "").strip())
    mineru_enabled: bool = field(default_factory=lambda: _bool("BIDCRAFT_MINERU", True))
    mineru_timeout: float = field(
        default_factory=lambda: float(os.getenv("BIDCRAFT_MINERU_TIMEOUT", "180"))
    )
    ocr_languages: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_OCR_LANGS", "chi_sim+eng")
    )
    ocr_dpi: int = field(default_factory=lambda: int(os.getenv("BIDCRAFT_OCR_DPI", "300")))
    ocr_timeout: float = field(
        default_factory=lambda: float(os.getenv("BIDCRAFT_OCR_TIMEOUT", "120"))
    )
    ocr_max_pages: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_OCR_MAX_PAGES", "100"))
    )
    ocr_tesseract_cmd: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_TESSERACT", "tesseract")
    )
    ocr_pdftoppm_cmd: str = field(
        default_factory=lambda: os.getenv("BIDCRAFT_PDFTOPPM", "pdftoppm")
    )
    image_words_per_image: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_IMAGE_WORDS_PER_IMAGE", "3000"))
    )
    image_max_per_chapter: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_IMAGE_MAX_PER_CHAPTER", "4"))
    )
    kb_vector_enabled: bool = field(
        default_factory=lambda: _bool("BIDCRAFT_KB_VECTOR", True)
    )
    kb_vector_weight: float = field(
        default_factory=lambda: float(os.getenv("BIDCRAFT_KB_VECTOR_WEIGHT", "0.6"))
    )
    kb_vector_min_score: float = field(
        default_factory=lambda: float(os.getenv("BIDCRAFT_KB_VECTOR_MIN_SCORE", "0.1"))
    )
    kb_vector_max_chunks: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_KB_VECTOR_MAX_CHUNKS", "4000"))
    )
    kb_vector_batch: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_KB_VECTOR_BATCH", "16"))
    )

    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser().resolve()
        self.ocr_dpi = max(72, min(600, self.ocr_dpi))
        self.ocr_max_pages = max(0, self.ocr_max_pages)
        self.image_words_per_image = max(500, min(20000, self.image_words_per_image))
        self.image_max_per_chapter = max(1, min(12, self.image_max_per_chapter))
        self.kb_vector_weight = max(0.0, min(1.0, self.kb_vector_weight))
        self.kb_vector_min_score = max(0.0, min(1.0, self.kb_vector_min_score))
        self.kb_vector_max_chunks = max(0, self.kb_vector_max_chunks)
        self.kb_vector_batch = max(1, min(64, self.kb_vector_batch))
        self.plugin_timeout = max(5.0, min(120.0, self.plugin_timeout))
        self.pending_charge_ttl_seconds = max(0, self.pending_charge_ttl_seconds)
        self.rate_limit_window_seconds = max(1, min(3600, self.rate_limit_window_seconds))
        for name in (
            "rate_limit_login_ip",
            "rate_limit_login_global",
            "rate_limit_sensitive_ip",
            "rate_limit_sensitive_global",
        ):
            setattr(self, name, max(1, int(getattr(self, name))))
        self.log_level = self.log_level if self.log_level in {
            "CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"
        } else "INFO"
        self.log_format = self.log_format if self.log_format in {"text", "json"} else "text"
        if not self.db_url:
            self.db_url = f"sqlite+pysqlite:///{self.home / 'bidcraft.db'}"
        self.db_url = normalize_db_url(self.db_url)

    @property
    def db_backend(self) -> str:
        """``sqlite`` / ``postgresql``；用于方言分支与运维展示。"""

        return db_backend(self.db_url)

    @property
    def is_postgres(self) -> bool:
        return self.db_backend == "postgresql"

    @property
    def db_url_display(self) -> str:
        """脱敏后的连接串（隐藏口令），供健康检查与日志展示。"""

        return redact_db_url(self.db_url)

    @property
    def public_origins(self) -> tuple[str, ...]:
        """规范化后的公开 Origin 列表（去空格/尾斜杠/统一小写）。"""

        return tuple(
            item.strip().rstrip("/").lower()
            for item in self.public_origin.split(",")
            if item.strip()
        )

    @property
    def backup_data_dirs_list(self) -> list[str]:
        """需要随备份打包的数据目录名（相对 BIDCRAFT_HOME）。"""

        return [
            item.strip().strip("/\\")
            for item in self.backup_data_dirs.split(",")
            if item.strip()
        ]

    @property
    def backup_dirs(self) -> list[str]:
        """``backup_data_dirs_list`` 的短别名。"""

        return self.backup_data_dirs_list

    @property
    def model_configured(self) -> bool:
        return bool(self.model_base_url and self.model_name and not self.offline)

    def project_dir(self, project_id: str) -> Path:
        path = self.home / "projects" / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------------------------------------------------------- 启动校验
    def _pay_configured(self) -> bool:
        """微信支付是否声称已接入（未走 mock 且至少有一个支付参数）。"""

        return not self.wechat_pay_mock and bool(
            self.wechat_pay_mch_id
            or self.wechat_pay_serial_no
            or self.wechat_pay_private_key
            or self.wechat_pay_apiv3_key
        )

    def _pay_missing(self) -> list[str]:
        fields = {
            "BIDCRAFT_WECHAT_PAY_MCH_ID": self.wechat_pay_mch_id,
            "BIDCRAFT_WECHAT_PAY_SERIAL_NO": self.wechat_pay_serial_no,
            "BIDCRAFT_WECHAT_PAY_PRIVATE_KEY": self.wechat_pay_private_key,
            "BIDCRAFT_WECHAT_PAY_PLATFORM_CERT": self.wechat_pay_platform_cert,
            "BIDCRAFT_WECHAT_PAY_APIV3_KEY": self.wechat_pay_apiv3_key,
            "BIDCRAFT_WECHAT_PAY_NOTIFY_URL": self.wechat_pay_notify_url,
        }
        return [name for name, value in fields.items() if not value]

    def validate(self) -> tuple[list[str], list[str]]:
        """返回 ``(errors, warnings)``，不抛异常、不打印，便于测试与复用。

        覆盖三类启动期就能发现的问题：模型参数、支付参数、管理员口令。
        校验刻意保持"保守"——只校验齐全性，不校验可达性（不发起任何网络请求）。
        """

        errors: list[str] = []
        warnings: list[str] = []
        if self.env not in ENVIRONMENTS:
            warnings.append(f"BIDCRAFT_ENV={self.env!r} 无效，已按 {DEFAULT_ENV} 处理")
        if self.env == "prod":
            if not self.offline and not (self.model_base_url and self.model_name):
                errors.append("生产环境未配置模型（BIDCRAFT_MODEL_BASE_URL / BIDCRAFT_MODEL_NAME）")
            if not self.admin_password:
                errors.append("生产环境必须设置管理员口令 BIDCRAFT_ADMIN_PASSWORD")
            elif len(self.admin_password) < MIN_ADMIN_PASSWORD_LENGTH:
                errors.append(
                    f"生产环境管理员口令 BIDCRAFT_ADMIN_PASSWORD 至少 {MIN_ADMIN_PASSWORD_LENGTH} 位"
                )
            elif is_weak_password(self.admin_password):
                errors.append("生产环境管理员口令 BIDCRAFT_ADMIN_PASSWORD 不能使用占位/弱口令")
            if self.secret_key:
                try:
                    raw_key = base64.urlsafe_b64decode(self.secret_key.encode("ascii"))
                except (ValueError, UnicodeEncodeError):
                    raw_key = b""
                if len(raw_key) != 32:
                    errors.append("BIDCRAFT_SECRET_KEY 必须是 32 字节 Fernet 密钥的 base64 编码")
            if not self.is_postgres:
                warnings.append(
                    "SQLite 为单写者：多 worker/多实例部署必须使用 PostgreSQL（BIDCRAFT_DB_URL）"
                )
            elif not self.secret_key:
                warnings.append(
                    "PostgreSQL 多实例须共享同一 secret.key，或统一设置 BIDCRAFT_SECRET_KEY"
                )
            if self._pay_configured():
                missing = self._pay_missing()
                if missing:
                    errors.append("生产环境微信支付参数不全：" + "、".join(missing))
            if not self.secure_cookie:
                warnings.append("生产环境建议开启 BIDCRAFT_SECURE_COOKIE（需 HTTPS）")
        else:
            if not self.offline and not self.model_configured:
                warnings.append("模型未配置，章节生成将走降级路径")
            if self._pay_configured() and self._pay_missing():
                warnings.append("微信支付参数不全，支付功能不可用")
            if not self.admin_password:
                warnings.append("未设置 BIDCRAFT_ADMIN_PASSWORD，将使用初始化口令")
            elif is_weak_password(self.admin_password):
                warnings.append("BIDCRAFT_ADMIN_PASSWORD 看起来是占位口令，请换成强口令")
        return errors, warnings

    def safe_dict(self) -> dict[str, object]:
        """配置脱敏视图，供 /admin/health 展示；绝不返回明文密钥。"""

        return {
            "env": self.env,
            "home": str(self.home),
            "db_backend": self.db_backend,
            "db_url": self.db_url_display,
            "offline": self.offline,
            "model_base_url": self.model_base_url,
            "model_name": self.model_name,
            "model_api_key": redact_secret(self.model_api_key),
            "wechat_pay": self._pay_configured(),
            "wechat_pay_mch_id": self.wechat_pay_mch_id,
            "wechat_pay_apiv3_key": redact_secret(self.wechat_pay_apiv3_key),
            "wechat_pay_private_key": redact_secret(self.wechat_pay_private_key),
            "wechat_app_secret": redact_secret(self.wechat_app_secret),
        }


def validate_startup(config: Settings | None = None) -> None:
    """启动期校验：dev/test 记 warning；prod 关键项缺失直接阻断。

    只依赖 ``config.validate()`` 的纯结果，便于单独测试。被 ``create_app()`` 调用。
    """

    config = config or settings
    errors, warnings = config.validate()
    for message in warnings:
        logger.warning("配置检查：%s", message)
    if errors:
        raise RuntimeError("启动配置校验失败：" + "；".join(errors))


settings = Settings()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """最小结构化格式：单行 JSON，不依赖第三方日志库。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_logging_configured = False


def configure_logging() -> None:
    """配置根日志一次；后续 create_app() 只更新级别/格式，不重复挂 handler。"""

    global _logging_configured
    root = logging.getLogger()
    level = getattr(logging, settings.log_level, logging.INFO)
    if settings.log_format == "json":
        formatter: logging.Formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s [req=%(request_id)s] %(message)s"
        )
    if not _logging_configured:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.addFilter(_RequestIdFilter())
        handler._bidcraft = True  # type: ignore[attr-defined] - 标记自有 handler
        root.addHandler(handler)
        _logging_configured = True
    else:
        for existing in root.handlers:
            if getattr(existing, "_bidcraft", False):
                existing.setFormatter(formatter)
    root.setLevel(level)


def set_request_id(value: str) -> object:
    return request_id_var.set(value)


def reset_request_id(token: object) -> None:
    request_id_var.reset(token)  # type: ignore[arg-type]


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
