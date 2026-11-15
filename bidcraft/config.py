from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    """Runtime settings.

    Security posture: the process makes no outbound connection unless a model
    endpoint is explicitly configured. There is no telemetry, no analytics and
    no remote error reporting anywhere in this codebase.
    """

    home: Path = field(default_factory=lambda: Path(os.getenv("BIDCRAFT_HOME", ".bidcraft")))
    db_url: str = ""
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
    admin_username: str = field(default_factory=lambda: os.getenv("BIDCRAFT_ADMIN_USER", "admin"))
    admin_password: str = field(default_factory=lambda: os.getenv("BIDCRAFT_ADMIN_PASSWORD", ""))
    session_days: int = field(
        default_factory=lambda: int(os.getenv("BIDCRAFT_SESSION_DAYS", "7"))
    )
    secure_cookie: bool = field(default_factory=lambda: _bool("BIDCRAFT_SECURE_COOKIE"))
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
    ocr_enabled: bool = field(default_factory=lambda: _bool("BIDCRAFT_OCR", True))
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

    @property
    def model_configured(self) -> bool:
        return bool(self.model_base_url and self.model_name and not self.offline)

    def project_dir(self, project_id: str) -> Path:
        path = self.home / "projects" / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
