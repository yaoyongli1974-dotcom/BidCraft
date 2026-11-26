"""HTTP 请求体模型（Pydantic）。

从 app.py 外迁：纯数据定义，无副作用，被 app 门面与 routes 共同引用。URL/响应格式不变。
"""

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from ..authorization import MEMBER_ROLES


def _validate_member_role(role: str) -> str:
    """Canonical project-member role check (same set as ``authorization``).

    Raise ``HTTPException(400)`` rather than ``ValueError`` deliberately:
    the handlers already answer an invalid role with 400, and this validator
    only moves the check to the API boundary as defense in depth.  Pydantic
    only converts ValueError/AssertionError into a 422 ValidationError; other
    exceptions propagate, so the public status code stays unchanged.
    """

    if role not in MEMBER_ROLES:
        raise HTTPException(status_code=400, detail="role 只能是 editor 或 viewer")
    return role


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


class LessonPolicyUpdate(BaseModel):
    """v0.33.0 RC-08 建议经验治理策略（存 project.meta，无 schema 迁移）。

    部分字段PATCH：只更新显式给出的键，未给出的键沿用旧策略；取值白名单与
    裁剪在 ``evaluation.learning.normalize_lesson_policy`` 统一完成。
    """

    grades: dict[str, str] | None = None
    trust_levels: dict[str, str] | None = None
    min_trust: str | None = None
    ttl_days: int | None = None
    revoked_ids: list[str] | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    bid_section: str | None = None
    # R23 设为模板/取消模板（仅 owner/admin）。
    is_template: bool | None = None
    # R19 审批链：兼容 R18 扁平用户 id 列表与 v2 阶段列表
    # [{approvers: [用户id], mode?: "all"|"any"}]（≤5 级，仅 owner 可改）；
    # 空列表=单级负责人审批。双格式解析与校验在 handler 内完成。
    approval_chain: list[str] | list[dict[str, Any]] | None = None
    # v0.33.0 RC-08 经验治理策略（仅 owner/admin 可改，部分字段 PATCH）。
    lesson_policy: LessonPolicyUpdate | None = None


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

    @field_validator("role")
    @classmethod
    def _role_check(cls, value: str) -> str:
        return _validate_member_role(value)


class MemberUpdate(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _role_check(cls, value: str) -> str:
        return _validate_member_role(value)


class RulePackageDocument(BaseModel):
    document: str = Field(min_length=1, max_length=200 * 1024)


class RulePackageValidateRequest(BaseModel):
    name: str = ""
    document: str = Field(min_length=1, max_length=200 * 1024)


class FactUpdate(BaseModel):
    """人工修正事实；确认状态必须携带可核验的真实来源证据。

    ``key`` 由路由路径注入（CLI 显式传入），其余字段保持旧客户端兼容。
    """

    key: str = ""
    value: str
    unit: str | None = None
    status: Literal["extracted", "confirmed", "manual", "missing", "conflict"] = "confirmed"
    source_file_id: str | None = None
    source_quote: str = ""
    source_page: int = 0


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
    #: 人工修改说明：进入修订快照与审计 detail，供经验回流消费；缺省空串保持兼容。
    change_note: str = Field(default="", max_length=500)


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
    # 作用域隔离：owner 必须带 owner_id；http 私网访问需显式 allow_private_net。
    scope: str | None = None
    owner_id: str | None = None
    allow_private_net: bool = False


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
    scope: str | None = None
    owner_id: str | None = None
    allow_private_net: bool | None = None


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


# --- 对标易标第二批（v0.25.0）请求模型 ---

class EnterpriseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class EnterpriseMemberAdd(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    role: str = "member"


class KbDocumentIngest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    content: str = ""
    kind: str = "other"
    tags: list[str] = []
    valid_to: str | None = None


class EnterpriseMatchRequest(BaseModel):
    requirement: str = ""
    kind: str = ""


class SimilarityRequest(BaseModel):
    scope: str = "project"
    mode: str = "text"
    threshold: float = 0.85
    source_project_id: str | None = None


class SimilarityHitReview(BaseModel):
    status: str = "normal"
    note: str = ""


class CommercialDocCreate(BaseModel):
    name: str = "商务标"
    quote_version_id: str | None = None
    package_id: str | None = None


class CommercialDocUpdate(BaseModel):
    name: str | None = None
    sections: Any = None


class CommercialReview(BaseModel):
    action: str = "submit"
    note: str = ""
    #: 提交时是否强制校验商务标必填项完整性（默认关闭，保持既有行为）。
    require_complete: bool = False

# ---- 对标易标第三批：Agent 编排 + 协作 ----
class WorkflowCreate(BaseModel):
    workflow_type: str = "standard"
    goal: str = ""
    input: dict = {}
    budget: dict = {}


class WorkflowApprovalReview(BaseModel):
    decision: str = "approve"
    note: str = ""


class SectionRevisionRestore(BaseModel):
    revision: int
    note: str = ""


class ChapterAssignmentCreate(BaseModel):
    node_id: str = Field(min_length=1, max_length=36)
    assignee_id: str = Field(min_length=1, max_length=36)
    deadline: str | None = None
    note: str = ""


class ChapterAssignmentUpdate(BaseModel):
    status: str = "assigned"
    note: str = ""


class SectionLockRenew(BaseModel):
    lock_token: str = Field(min_length=1, max_length=36)


class QualityRunRequest(BaseModel):
    note: str = ""


class QualityFindingUpdate(BaseModel):
    # 与 IssueStatusUpdate 口径一致：只允许人工处置的三种终态，非法值在
    # 请求体解析阶段直接 422，不进入 Service 层。
    status: Literal["handled", "ignored", "open"] = "handled"
    note: str = ""


# --- v0.32 Phase 1：章节规划 ---
class ChapterPlanGenerateRequest(BaseModel):
    #: 用户对本章的补充写作指令（会进入规划 prompt，不会凭空生成引用 ID）。
    user_instruction: str = ""
    #: 标段/包件隔离；不传时沿用项目上下文。
    package_id: str = ""
    #: 为 True 时把当前有效规划标记为被取代并追加新版本。
    regenerate: bool = False
    note: str = ""


class ChapterPlanUpdate(BaseModel):
    """人工调整：只覆盖显式给出的字段，其余沿用当前版本。"""

    note: str = ""
    package_id: str = ""
    #: 与扁平字段等价的包装形式，便于统一前端契约；两者同时给出时以扁平字段为准。
    updates: dict[str, Any] | None = None
    objective: str | None = None
    user_instruction: str | None = None
    score_points: list[Any] | None = None
    requirements: list[Any] | None = None
    conditions: list[Any] | None = None
    challenges: list[Any] | None = None
    strategy: list[Any] | None = None
    measures: list[Any] | None = None
    knowledge_refs: list[Any] | None = None
    fact_refs: list[Any] | None = None
    planned_tables: list[Any] | None = None
    planned_diagrams: list[Any] | None = None
    forbidden_assumptions: list[Any] | None = None
    quality_criteria: list[Any] | None = None
    user_adjustments: list[Any] | None = None


class ChapterPlanConfirm(BaseModel):
    note: str = ""


# --- v0.32 Phase 2：知识资产引擎 -------------------------------------------
class KbAssetReview(BaseModel):
    """文档资产审核流转：draft → review → approved → published / rejected / archived。"""

    decision: Literal["review", "approve", "publish", "reject", "archive"]
    note: str = ""


class KbDuplicateGroupResolve(BaseModel):
    """查重候选组人工裁决：resolve（合并/采纳）或 dismiss（忽略）。"""

    decision: Literal["resolve", "dismiss"]
    note: str = ""
