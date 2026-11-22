from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    package: Mapped[str] = mapped_column(String(80), default="weakcurrent-cabling")
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="intake")
    buyer: Mapped[str] = mapped_column(String(255), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    # R23 项目模板库：设为模板后全员可浏览并从模板创建新项目。
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    files: Mapped[list[SourceFile]] = relationship(back_populates="project")
    facts: Mapped[list[Fact]] = relationship(back_populates="project")


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="tender")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(120), default="")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_state: Mapped[str] = mapped_column(String(20), default="pending")
    parse_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="files")


class Fact(Base):
    """One row of the project fact ledger (C1 schema, flattened for querying)."""

    __tablename__ = "facts"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_fact_project_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    value: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(40), default="")
    # extracted | confirmed | manual | missing | conflict
    status: Mapped[str] = mapped_column(String(20), default="extracted")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    source_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_page: Mapped[int] = mapped_column(Integer, default=0)
    source_quote: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="facts")


class FactCitation(Base):
    """正文引用某条事实取值的记录（来源追溯的基础数据）。

    ``value`` 是扫描时正文里出现的取值。台账字段被改写后，``value`` 与当前
    ``Fact.value`` 不一致即视为 stale：正文还写着旧值，所在章节提示「需复核」。
    判定只认「正文里出现了台账里的这个值」，不做语义推断。
    """

    __tablename__ = "fact_citations"
    __table_args__ = (UniqueConstraint("node_id", "fact_key", name="uq_citation_node_fact"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("outline_nodes.id"), index=True)
    fact_key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    spec: Mapped[str] = mapped_column(String(255), default="")
    unit: Mapped[str] = mapped_column(String(40), default="")
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    source_quote: Mapped[str] = mapped_column(Text, default="")


class BidPackage(Base):
    """One 标段/包件 (bid package) of a project.

    The material ledger (``Material``) stays shared at project level; only BOQ
    snapshots are separated per package, so each package may carry its own
    uploaded 工程量清单 and response workbook.
    """

    __tablename__ = "bid_packages"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_bid_package_project_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    number: Mapped[str] = mapped_column(String(40), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BoqSnapshot(Base):
    """One imported 工程量清单 workbook per project/package (replaced on re-upload).

    Stores the parsed BOQ rows only; alignment against the material ledger is
    recomputed from the live ``materials`` rows on every request, because a
    tender reparse rebuilds those rows. ``package_id`` is NULL for projects
    that never enabled bid packages (legacy single-snapshot semantics).
    """

    __tablename__ = "boq_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    package_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_file_id: Mapped[str] = mapped_column(String(36), default="")
    filename: Mapped[str] = mapped_column(String(255), default="")
    sheet: Mapped[str] = mapped_column(String(255), default="")
    header_row: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    ignored: Mapped[list] = mapped_column(JSON, default=list)
    # User-entered unit prices keyed by ``sheet!row``; never generated by the app.
    prices: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BoqQuoteVersion(Base):
    """A named snapshot of one package's entered unit prices and quote summary.

    Versions are immutable history: restoring one copies its ``prices`` back
    onto the active :class:`BoqSnapshot` of the same package. ``package_id`` is
    NULL for projects that never enabled bid packages.
    """

    __tablename__ = "boq_quote_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    package_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="")
    prices: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # 不可变计算依据（单价已在 prices，这里存数量/税率/币种/汇率/系数/舍入位）：
    # save_version 时冻结，供审计与复算，避免"只存结果不存依据"。
    basis: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft"
    )
    submit_note: Mapped[str] = mapped_column(String(200), default="", server_default="")
    submitted_by: Mapped[str] = mapped_column(String(80), default="", server_default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str] = mapped_column(String(200), default="", server_default="")
    reviewed_by: Mapped[str] = mapped_column(String(80), default="", server_default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # R18 多级审批记录：[{stage, user_id, name, decision, note, at}]，逐级 append；
    # 空链（单级负责人审批）时保持 []。重新提交会清空以重启审批链。
    approvals: Mapped[list] = mapped_column(JSON, default=list)


class Qualification(Base):
    """投标资质/业绩台账（owner 私有）。

    证书本身不联网核验；用户在此登记结构化信息，并把官方查询截图/证书扫描件
    作为证据附件留档。``evidence`` 元素形如
    ``{filename, path, size, mime, uploaded_at}``，文件落在
    ``<home>/qualifications/<user_id>/``。
    """

    __tablename__ = "qualifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # license | safety | person | performance | credit | other
    kind: Mapped[str] = mapped_column(String(20), default="other")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    holder: Mapped[str] = mapped_column(String(120), default="")
    cert_no: Mapped[str] = mapped_column(String(80), default="")
    issuer: Mapped[str] = mapped_column(String(120), default="")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Personnel(Base):
    """投标人员台账（owner 私有）。

    与 ``Qualification`` 分离：人员需要职务、注册专业、社保缴纳月份与在建
    锁定状态，便于资格自查与素材元数据登记时引用。
    """

    __tablename__ = "personnel"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(80), default="")
    specialty: Mapped[str] = mapped_column(String(80), default="")
    cert_no: Mapped[str] = mapped_column(String(80), default="")
    social_months: Mapped[int] = mapped_column(Integer, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class QuotaItem(Base):
    """Enterprise quota entry (企业定额) maintained by a super administrator.

    The application never invents prices: a BOQ row only receives a unit price
    that an administrator entered here (or that the user typed on the row).
    """

    __tablename__ = "quota_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    spec: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TermItem(Base):
    """Enterprise terminology entry (企业术语库) maintained by a super admin.

    ``kind="forbidden"`` marks a 禁用词 that must not appear in the response
    body; ``kind="replace"`` records the preferred wording for ``source`` so
    the checker can flag bodies that still use the old term without the new
    one. Only enabled rows participate in checking.
    """

    __tablename__ = "term_items"
    __table_args__ = (UniqueConstraint("kind", "source", name="uq_term_kind_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # replace | forbidden
    kind: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(120), default="")
    note: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Parameter(Base):
    __tablename__ = "parameters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(String(255), default="")
    unit: Mapped[str] = mapped_column(String(40), default="")
    requirement: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    source_quote: Mapped[str] = mapped_column(Text, default="")


class ScoringItem(Base):
    __tablename__ = "scoring_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    criteria: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    section_hint: Mapped[str] = mapped_column(String(255), default="")
    source_quote: Mapped[str] = mapped_column(Text, default="")
    # R39 得分回填：本项目（或对标中标人）在该评分项的实际得分与备注。
    actual_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_bidder: Mapped[str] = mapped_column(String(80), default="")
    score_note: Mapped[str] = mapped_column(String(200), default="")


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="substantive")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    source_quote: Mapped[str] = mapped_column(Text, default="")
    # ★/▲ 实质性标记：保留原文符号，供风险总览按最高优先级呈现。
    marker: Mapped[str] = mapped_column(String(8), default="")
    # L1 条款提取证据：原文页码与条款编号（招标文件里的物理位置，供举证定位）。
    page: Mapped[int] = mapped_column(Integer, default=0)
    clause_no: Mapped[str] = mapped_column(String(40), default="")
    # 人工确认的响应状态：pending（未确认）/ responded（已响应）/ na（不适用）。
    response_status: Mapped[str] = mapped_column(String(16), default="pending")
    response_note: Mapped[str] = mapped_column(Text, default="")
    responded_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 人工确认时快照「原始自动判定结果」：人员/时间/原因见上三列，这里存判定本身，
    # 使「谁、何时、基于什么判定、为何确认」可被完整追溯。
    response_verdict: Mapped[str] = mapped_column(String(16), default="")
    response_verdict_label: Mapped[str] = mapped_column(String(32), default="")
    response_verdict_reason: Mapped[str] = mapped_column(Text, default="")
    # 自动匹配到的正文章节（section_id 对应 Section 行，node_id 对应大纲节点，供跳转）。
    section_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    auto_covered: Mapped[bool] = mapped_column(Boolean, default=False)


class Risk(Base):
    __tablename__ = "risks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    level: Mapped[str] = mapped_column(String(20), default="major")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, default="")


class OutlineNode(Base):
    __tablename__ = "outline_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    heading: Mapped[str] = mapped_column(String(255), nullable=False)
    intent: Mapped[str] = mapped_column(Text, default="")
    target_words: Mapped[int] = mapped_column(Integer, default=1200)
    needs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("outline_nodes.id"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    words: Mapped[int] = mapped_column(Integer, default=0)
    # Optimistic concurrency token, bumped on every manual chapter save.
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # 章节复核流转：draft → review → confirmed → locked。
    # 投标团队的人工检查点，不追求可配置流程；已确认/锁定后正文不可改，导出只认已确认。
    review_status: Mapped[str] = mapped_column(String(16), default="draft")
    review_note: Mapped[str] = mapped_column(Text, default="")
    review_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_requested_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    section_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: 锚定到的大纲节点（OutlineNode.id）；location 匹配不到时留空。
    node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    code: Mapped[str] = mapped_column(String(40), default="")
    level: Mapped[str] = mapped_column(String(16), default="major")
    category: Mapped[str] = mapped_column(String(80), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    finding: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    #: 最近一次核对时间（naive UTC 落库，序列化见 clock.iso_beijing）。
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 人工处置时间/处置人/备注；status=open 时三者清空。
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    file_format: Mapped[str] = mapped_column(String(12), default="docx")
    file_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    #: 排队优先级：数值越小越先执行（VIP 主账号/子账号=0，普通=1）。
    priority: Mapped[int] = mapped_column(Integer, default=1)
    # DB 驱动队列：Job 行本身就是排队凭据，Worker 用条件 UPDATE 原子认领。
    # attempts 在每次认领时 +1；租约过期/进程崩溃由 reaper 重新排队，超过 max_attempts 才失败。
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # fencing token：每次认领刷新。worker 的关键写操作必须校验当前令牌，
    # 租约过期/被回收后旧 worker 令牌失效，写操作与心跳都会被拒（至少一次执行）。
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class JobEvent(Base):
    """Append-only progress event for a background job.

    ``id`` doubles as the SSE event id. Events are persisted so a browser can
    reconnect with ``Last-Event-ID`` and replay only what it missed.
    """

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # super_admin | manager | viewer
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    #: VIP 到期时间（naive UTC）；None 或已过期表示非 VIP。
    vip_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 子账号指向主账号 id；主账号为 None。子账号消费计入主账号。
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    #: 注销冷静期：申请后到期前可撤销，到期由 purge 任务真正删除。
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deletion_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 同意条款的版本与时间（注册时留痕，便于合规举证）。
    terms_version: Mapped[str] = mapped_column(String(20), default="")
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wechat_openid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    wechat_unionid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    wechat_nickname: Mapped[str] = mapped_column(String(120), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginRateLimit(Base):
    """Persisted failed-login counter so throttling survives process restarts.

    ``hits`` counts failures inside one fixed window starting at
    ``window_start``; a successful login deletes the row and expired rows are
    purged on every login attempt.
    """

    __tablename__ = "login_rate_limits"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelConfig(Base):
    """Admin-managed model endpoint. API keys are encrypted at rest."""

    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    # chat | image | embedding
    kind: Mapped[str] = mapped_column(String(20), default="chat")
    # openai | ollama | custom
    provider: Mapped[str] = mapped_column(String(20), default="openai")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.4)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4000)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class KnowledgeBase(Base):
    """Local document index or third-party retrieval endpoint."""

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    # local | api
    kind: Mapped[str] = mapped_column(String(20), default="local")
    # local | generic | dify | ragflow
    provider: Mapped[str] = mapped_column(String(20), default="local")
    location: Mapped[str] = mapped_column(String(1000), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    dataset_id: Mapped[str] = mapped_column(String(255), default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UserDoc(Base):
    """A file uploaded by a regular user into their personal knowledge base."""

    __tablename__ = "user_docs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    kb_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_bases.id"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), default="")
    stored_path: Mapped[str] = mapped_column(String(1000), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(120), default="")
    #: ready | indexing | failed
    status: Mapped[str] = mapped_column(String(16), default="ready")
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    source: Mapped[str] = mapped_column(String(500), default="")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    tokens: Mapped[str] = mapped_column(Text, default="")
    # Semantic vector cache: vectors only count when ``embed_key`` matches the
    # currently resolved endpoint (``config_id:model_name``).
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embed_key: Mapped[str] = mapped_column(String(200), default="")
    embed_dim: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Plugin(Base):
    """外部 MCP server（stdio 子进程或 HTTP 端点），由超管配置后聚合进 MCP 服务。

    ``tool_allowlist`` 为空表示允许该插件的全部工具；非空时只有列出的工具会
    出现在聚合的 ``tools/list`` 中，也不可被调用。插件不会自动安装或常驻：
    每次工具调用都会启动/握手/结束。
    """

    __tablename__ = "plugins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    # stdio | http
    transport: Mapped[str] = mapped_column(String(10), default="stdio")
    command: Mapped[str] = mapped_column(String(500), default="")
    args: Mapped[list] = mapped_column(JSON, default=list)
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    url: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tool_allowlist: Mapped[list] = mapped_column(JSON, default=list)
    #: 轻量沙箱参数：stdio 子进程的地址空间上限（MB）与 CPU 时间上限（秒）。
    mem_limit_mb: Mapped[int] = mapped_column(Integer, default=512)
    cpu_seconds: Mapped[int] = mapped_column(Integer, default=10)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str] = mapped_column(Text, default="")
    fail_streak: Mapped[int] = mapped_column(Integer, default=0)
    auto_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WechatConfig(Base):
    """管理端微信开放平台配置（单行，id 固定 default）。"""

    __tablename__ = "wechat_config"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    app_id: Mapped[str] = mapped_column(String(120), default="")
    app_secret_enc: Mapped[str] = mapped_column(Text, default="")
    redirect_uri: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mock: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    # signup | recharge | redeem | consume | refund | admin
    kind: Mapped[str] = mapped_column(String(20), default="recharge")
    note: Mapped[str] = mapped_column(String(500), default="")
    ref_type: Mapped[str] = mapped_column(String(40), default="")
    ref_id: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RechargeOrder(Base):
    __tablename__ = "recharge_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    out_trade_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    # pending | paid | closed | expired
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # wechat | redeem | admin | mock
    provider: Mapped[str] = mapped_column(String(16), default="wechat")
    package_id: Mapped[str] = mapped_column(String(80), default="")
    code_url: Mapped[str] = mapped_column(Text, default="")
    transaction_id: Mapped[str] = mapped_column(String(80), default="")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RedeemCode(Base):
    __tablename__ = "redeem_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    # unused | used | disabled
    status: Mapped[str] = mapped_column(String(16), default="unused")
    batch: Mapped[str] = mapped_column(String(60), default="")
    created_by: Mapped[str] = mapped_column(String(36), default="")
    used_by: Mapped[str] = mapped_column(String(36), default="")
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProjectMember(Base):
    """Per-project membership grant; the owner is ``Project.owner_id``."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # editor | viewer
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    """Append-only record of successful project/administration actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    username: Mapped[str] = mapped_column(String(80), default="")
    display_name: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ImageAsset(Base):
    """Generated chapter illustration, stored inside the project directory."""

    __tablename__ = "image_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    label: Mapped[str] = mapped_column(String(255), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    # ready | failed
    status: Mapped[str] = mapped_column(String(16), default="ready")
    file_path: Mapped[str] = mapped_column(String(500), default="")
    filename: Mapped[str] = mapped_column(String(255), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class InvoiceRequest(Base):
    """开票申请：用户提交抬头与金额，管理员开具后上传发票文件。"""

    __tablename__ = "invoice_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_no: Mapped[str] = mapped_column(String(80), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(500), default="")
    # requested | issued | rejected
    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)
    invoice_no: Mapped[str] = mapped_column(String(80), default="")
    reject_reason: Mapped[str] = mapped_column(String(500), default="")
    file_path: Mapped[str] = mapped_column(String(500), default="")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
