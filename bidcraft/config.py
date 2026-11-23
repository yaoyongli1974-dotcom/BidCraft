from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("bidcraft.config")

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
    health_public: bool = field(
        default_factory=lambda: os.getenv("BIDCRAFT_HEALTH_PUBLIC", "minimal")
        .strip().lower()
        in {"minimal", "public"}
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


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
