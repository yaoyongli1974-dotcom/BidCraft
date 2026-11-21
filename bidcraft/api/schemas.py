"""HTTP 请求体模型（Pydantic）。

从 app.py 外迁：纯数据定义，无副作用，被 app 门面与 routes 共同引用。URL/响应格式不变。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    #: 必须显式勾选同意《服务条款》《隐私政策》；缺省一律拒绝，不替旧客户端默认同意。
    accept_terms: bool = False
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


class TextQaUpdate(BaseModel):
    typos: Any = None


class WechatConfigUpdate(BaseModel):
    app_id: str | None = None
    app_secret: str | None = None
    clear_secret: bool = False
    redirect_uri: str | None = None
    enabled: bool | None = None
    mock: bool | None = None


class AccountDelete(BaseModel):
    password: str
    confirm: str


class BillingProfilePayload(BaseModel):
    title: str = ""
    tax_no: str = ""
    email: str = ""


class ExportTemplateUpdate(BaseModel):
    id: str
