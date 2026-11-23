// Domain types mirroring the BidCraft HTTP contract. Fields that the server
// may omit during incremental rollouts are optional and normalised in selectors.

export type FactStatus = 'extracted' | 'confirmed' | 'manual' | 'missing' | 'conflict'

export interface Health {
  version: string
  model_configured: boolean
  offline: boolean
  home?: string
  model_label?: string
  models?: number
  knowledge_bases?: number
  auth_required?: boolean
  web_ready?: boolean
  registration?: boolean
  wechat_login?: boolean
  wechat_mock?: boolean
  wechat_pay?: boolean
  wechat_pay_mock?: boolean
}

export type Role = 'super_admin' | 'manager' | 'viewer'

export interface User {
  id: string
  username: string
  display_name: string
  role: Role | string
  role_label: string
  active: boolean
  credits?: number
  is_external?: boolean
  vip_expires_at?: string | null
  is_vip?: boolean
  parent_id?: string
  is_sub?: boolean
  wechat_nickname?: string
  avatar_url?: string
  last_login_at?: string | null
  created_at?: string | null
}

/**
 * 普通用户上传的个人文档（R27）。文件落在服务端的个人目录，并自动建立
 * 个人知识库索引；只有上传者本人可以在生成时检索到。
 */
export interface UserDoc {
  id: string
  filename: string
  size: number
  mime: string
  /** ready（可检索）/ indexing（索引中）/ failed（索引失败）等后端状态。 */
  status: string
  error: string
  /** 关联的个人知识库 id。 */
  kb_id: string
  created_at: string | null
  updated_at: string | null
}

/** `POST /my/docs` 的 202 响应：文档元数据与后台索引任务 id。 */
export interface UserDocUploadResult {
  doc: UserDoc
  job_id: string
}

export type ModelKind = 'chat' | 'image' | 'embedding'
export type ModelProvider = 'openai' | 'ollama' | 'custom'

export interface ModelConfig {
  id: string
  name: string
  kind: ModelKind | string
  provider: ModelProvider | string
  base_url: string
  model: string
  temperature?: number
  max_tokens?: number
  options?: Record<string, unknown>
  enabled: boolean
  is_default: boolean
  has_api_key: boolean
  last_test_at?: string | null
  last_test_ok?: boolean | null
  last_test_message?: string
  created_at?: string | null
  updated_at?: string | null
}

export interface ModelInput {
  name: string
  kind: string
  provider: string
  base_url: string
  model: string
  api_key?: string
  temperature?: number
  max_tokens?: number
  options?: Record<string, unknown>
  enabled?: boolean
  is_default?: boolean
  clear_api_key?: boolean
}

export type KbKind = 'local' | 'api'
export type KbProvider = 'local' | 'generic' | 'dify' | 'ragflow'

export interface KnowledgeBase {
  id: string
  name: string
  kind: KbKind | string
  provider: KbProvider | string
  location: string
  dataset_id?: string
  options?: Record<string, unknown>
  enabled: boolean
  is_default: boolean
  has_api_key: boolean
  chunk_count: number
  indexed_at?: string | null
  last_test_at?: string | null
  last_test_ok?: boolean | null
  last_test_message?: string
  created_at?: string | null
  updated_at?: string | null
  /** Semantic vectors are switched on for this server. Older servers omit it. */
  vector_enabled?: boolean
  /** Embedding model used for the vector index; empty when none is configured. */
  vector_model?: string
  /** Chunks whose stored vector matches the current embedding model. */
  vector_embedded?: number
  /** Chunks that would need a vector (mirrors `chunk_count`). */
  vector_total?: number
  /** Vector width of the first embedded chunk, 0 before the first build. */
  vector_dim?: number
  /** Every chunk has a vector under the current model. */
  vector_ready?: boolean
  /** Some chunks still lack a vector under the current model. */
  vector_stale?: boolean
}

export interface KnowledgeBaseInput {
  name: string
  kind: string
  provider: string
  location: string
  api_key?: string
  dataset_id?: string
  options?: Record<string, unknown>
  enabled?: boolean
  is_default?: boolean
  clear_api_key?: boolean
}

export interface TestResult {
  ok: boolean
  message: string
  latency_ms: number
}

/**
 * Outcome of a vector build. `embedded` is how many chunks were written this
 * run; `error` carries a Chinese explanation when the build partially failed
 * (or could not run at all) and stays empty on full success.
 */
export interface KbVectorResult {
  embedded: number
  total: number
  model: string
  dim: number
  error: string
  kb: KnowledgeBase
}

export interface UserInput {
  username?: string
  display_name?: string
  password?: string
  role?: string
  active?: boolean
}


export interface PackageInfo {
  name: string
  title: string
  version: number | string
  extends?: string[] | string
  path?: string
  /** Project type key (`weakcurrent` / `fire` / ...). Older servers omit it. */
  project_type?: string
  /** Display label for the project type. Older servers omit it. */
  project_type_label?: string
  description?: string
  /** Shipped with the installation (not written by an administrator). */
  builtin?: boolean
  /** Built-in package with an administrator override in the user directory. */
  overridden?: boolean
  abstract?: boolean
}

/** Administrator view of a rule package. Mirrors `GET /admin/rule-packages`. */
export interface AdminRulePackage extends PackageInfo {
  project_type: string
  project_type_label: string
  description: string
  extends: string[]
  abstract: boolean
  builtin: boolean
  overridden: boolean
  hidden: boolean
  /** Number of projects created from this package. */
  usage: number
  /** Chinese reason when the YAML cannot be parsed; empty when healthy. */
  error: string
  document?: string
}

export interface RulePackageSummary {
  fields: number
  outline: number
  checks: number
}

export interface RulePackageValidation {
  ok: boolean
  problems: string[]
  summary: RulePackageSummary
}

export interface ProjectStats {
  sources: number
  facts_total: number
  facts_filled: number
  missing_required: number
  outline: number
  written: number
  issues_open: number
}

/** The caller's role inside one project. `admin` is a global super administrator. */
export type ProjectRole = 'owner' | 'editor' | 'viewer' | 'admin'

/** 报价审批链的一名审批人：用户 id 与保存时的姓名快照（改名不追更）。 */
export interface ApprovalChainEntry {
  id: string
  name: string
}

/** 一级审批的通过规则：all=会签（全部通过），any=或签（任一通过）。 */
export type ApprovalMode = 'all' | 'any'

/** 报价审批链的一级（v2）：1–5 名审批人 + 通过模式。 */
export interface ApprovalStage {
  approvers: ApprovalChainEntry[]
  mode: ApprovalMode
  /**
   * 可选金额门槛（元，≥0）：仅当版本 summary.total_amount ≥ 门槛时该级生效；
   * 缺省 / null = 恒生效。金额未知时带门槛级也视为生效（保守，宁多审不漏审）。
   */
  min_amount?: number | null
  /** 适用包件 id 白名单；空 / 缺省 = 不限包件。 */
  packages?: string[]
  /** 适用币种白名单；空 / 缺省 = 不限币种。 */
  currencies?: string[]
}

/** PATCH approval_chain 的 v2 入参：每级发用户 id 列表、模式与可选生效条件。 */
export interface ApprovalStageInput {
  approvers: string[]
  mode?: ApprovalMode
  /** 金额门槛（元，≥0）；省略 = 该级恒生效。 */
  min_amount?: number
  /** 适用包件 id 白名单；省略 / 空 = 不限包件。 */
  packages?: string[]
  /** 适用币种白名单；省略 / 空 = 不限币种。 */
  currencies?: string[]
}

/** 报价版本逐级审批的一条记录。 */
export interface QuoteApprovalRecord {
  /** 0 起的审批级序号。 */
  stage: number
  user_id: string
  name: string
  decision: 'approve' | 'reject'
  note: string
  at: string | null
}

/**
 * 跨项目「待我审批」的一条待办（`GET /approvals/pending` 平铺数组的一项）。
 * 只读视图：点击跳转到该项目的清单对齐（BOQ）页签，在版本面板里完成审批。
 */
export interface PendingApproval {
  project_id: string
  project_name: string
  version_id: string
  version_name: string
  /** 归属包件；单包件项目可能为 null。 */
  package_id: string | null
  /** 当前待审级（1 起）。 */
  stage: number
  /** 审批链总级数；空链时为 1。 */
  stage_count: number
  /** 当前级通过模式：all=会签，any=或签，空串=空链（负责人单级审批）。 */
  mode: 'all' | 'any' | ''
  submitted_by: string
  submitted_at: string | null
  /** 报价合计（元）；后端未给出时为 null，界面不显示金额。 */
  total_amount: number | null
}

export interface Project {
  id: string
  name: string
  package: string
  status: string
  buyer?: string
  number?: string
  tender_number?: string
  /** 标段 / 包件（≤60 字）；空串表示不区分。旧后端可能省略。 */
  bid_section?: string
  created_at?: string
  updated_at?: string
  stats?: Partial<ProjectStats>
  /** Present on `GET /projects` and `GET /projects/{ref}`; omitted by older servers. */
  my_role?: ProjectRole
  /** R23 项目模板标记：模板全员可见，可从模板创建新项目。旧后端可能省略。 */
  is_template?: boolean
  /** 有序报价审批链（≤5 级，每级 1–5 人会签/或签）；空数组或缺省 = 负责人单级审批。旧后端可能省略或回显扁平格式，读取时归一为 v2。 */
  approval_chain?: ApprovalStage[]
}

/** R22 项目复制入参：name 缺省由后端取「源名 副本」；两个开关缺省 true。 */
export interface ProjectDuplicateInput {
  name?: string
  includeBoq?: boolean
  includeImages?: boolean
}

/** R23 从模板创建入参：与复制同形状；name 缺省由后端取「模板名 副本」。 */
export type ProjectInstantiateInput = ProjectDuplicateInput

/** R23 模板列表轻量项（GET /projects/templates 平铺数组）。 */
export interface ProjectTemplate {
  id: string
  name: string
  package: string
  is_template: boolean
  updated_at: string | null
  /** 负责人 display_name || username；后端可空。 */
  owner_name: string | null
}

export interface LedgerSummary {
  fields_total?: number
  fields_filled?: number
  required?: number
  missing_required?: number
  materials?: number
  parameters?: number
  scoring?: number
  requirements?: number
  risks?: number
  [key: string]: number | undefined
}

export interface SourceRef {
  file?: string
  page?: number | string | null
  quote?: string
}

export interface Fact {
  key: string
  label: string
  value: string
  unit?: string
  status: FactStatus | string
  confidence?: number
  required: boolean
  source?: SourceRef
}

export interface Material {
  name: string
  spec?: string
  unit?: string
  qty?: number | string
  note?: string
  source_quote?: string
}

export interface Parameter {
  name: string
  value?: string
  unit?: string
  requirement?: string
  category?: string
  mandatory?: boolean
  source_quote?: string
}

export interface ScoringItem {
  name: string
  score?: number | string
  criteria?: string
  category?: string
  section_hint?: string
  source_quote?: string
}

/** 评分点覆盖矩阵（只读，来自 GET /projects/{ref}/scoring-coverage）。 */
export interface ScoringCoverageNode {
  id: string
  heading: string
  words: number
  has_body: boolean
  review_status?: string
  reasons: string[]
}

export interface ScoringCoverageRow {
  id?: string
  name: string
  score: number
  category?: string
  section_hint?: string
  status: 'covered' | 'weak' | 'uncovered'
  status_label: string
  nodes: ScoringCoverageNode[]
}

export interface ScoringCoverageReport {
  project_id?: string
  total_items: number
  total_score: number
  covered_score: number
  weak_score: number
  covered_ratio: number
  uncovered_count: number
  rows: ScoringCoverageRow[]
  issues: string[]
}

export interface Requirement {
  kind?: string
  text: string
  mandatory?: boolean
  source_quote?: string
}

export interface Risk {
  level?: string
  text: string
  source_quote?: string
}

export interface Ledger {
  summary?: LedgerSummary
  facts: Fact[]
  materials: Material[]
  parameters: Parameter[]
  scoring: ScoringItem[]
  requirements: Requirement[]
  risks: Risk[]
  template_sections?: string[]
  project?: Record<string, string>
}

export interface SourceFile {
  id: string
  filename: string
  role?: string
  size?: number
  page_count?: number | null
  parse_state?: string
  parse_note?: string
  created_at?: string
}

export interface OutlineChapter {
  id: string
  heading: string
  intent?: string
  target_words?: number
  needs?: string[]
  status?: string
  words?: number
  has_body?: boolean
  /** 本章正文命中的台账字段数（来源追溯）。 */
  citations?: number
  /** 命中字段中，台账取值已被改写、正文仍是旧值的数量。 */
  citations_stale?: number
  /** 章节复核状态：draft / review / confirmed / locked。 */
  review_status?: string
}

export interface ChapterDetail {
  id: string
  heading: string
  intent?: string
  target_words?: number
  body?: string
  words?: number
  status?: string
  /** Monotonic optimistic-lock counter; older servers omit it. */
  revision?: number
  citations?: number
  citations_stale?: number
  review_status?: string
  review_note?: string
  review_requested_at?: string | null
  review_requested_by?: string
  confirmed_at?: string | null
  confirmed_by?: string
  locked_at?: string | null
  locked_by?: string
}

/** 正文里出现的一处台账取值。 */
export interface FactCitation {
  node_id: string
  heading: string
  position: number
  fact_key: string
  label: string
  /** 扫描时正文里的取值。 */
  value: string
  /** 台账当前取值；与 value 不同即说明正文写着旧值。 */
  current_value: string
  occurrences: number
  stale: boolean
  status: string
  source_quote: string
  source_file_id?: string | null
  source_page: number
  checked_at?: string | null
}

export interface ChapterCitations {
  node_id: string
  heading: string
  citations: FactCitation[]
  stale: number
}

export interface CitationChapterSummary {
  node_id: string
  heading: string
  position: number
  citations: number
  stale: number
}

export interface ProjectCitations {
  project_id: string
  summary: {
    citations: number
    facts_cited: number
    stale: number
    sections_citing: number
    sections_needing_review: number
  }
  citations: FactCitation[]
  chapters: CitationChapterSummary[]
}

export interface FactUpdateResult {
  key: string
  label?: string
  value: string
  status: string
  required?: boolean
  /** 该字段被改写后，正文仍写着旧值的章节。 */
  stale_sections?: Array<{ node_id: string; heading: string; stale_value: string }>
}

export interface Issue {
  id?: string
  code: string
  level: string
  category?: string
  message?: string
  location?: string
  finding?: string
  recommendation?: string
  evidence?: unknown
  passed?: boolean
  status?: string
  /** 锚定信息：问题对应的大纲章节节点 id（用于「跳转修改」）；空表示未定位。 */
  node_id?: string | null
  /** 段落/小节锚（服务端细分定位用）；与 node_id 同源时可空。 */
  section_id?: string | null
  /** 所在章节标题；无 node_id 时仅作展示，不可跳转。 */
  heading?: string
  /** 在章节内的顺序提示（可空）。 */
  position?: number | null
  /** 该问题被核对出来的时间（可空）。 */
  checked_at?: string | null
  /** 处置完成时间（可空）。 */
  resolved_at?: string | null
  /** 处置人（可空）。 */
  resolved_by?: string | null
  /** 处置/忽略原因。 */
  resolution_note?: string
}

/**
 * 问题处置状态。新契约取值 open / handled / ignored；
 * 旧后端的 resolved / closed 仍要兼容（显示层归为「已处理」）。
 */
export type IssueStatus = 'open' | 'handled' | 'ignored'

/** 交付前风险总览的四档聚合级别（minor 与 info 合并进「提示」档）。 */
export type RiskTierLevel = 'blocking' | 'critical' | 'major' | 'minor'

/** `GET /projects/{ref}/risk-overview` 中一档的聚合计数。 */
export interface RiskTierSummary {
  level: RiskTierLevel
  label: string
  total: number
  open: number
  handled: number
  ignored: number
}

/** 按类别的风险聚合。 */
export interface RiskCategorySummary {
  category: string
  total: number
  open: number
  blocking_open: number
}

/** 导出门禁状态：有未处置的废标/阻断风险时 blocked。 */
export interface RiskGate {
  blocked: boolean
  blocking_open: number
  reason: string
}

/** `GET /projects/{ref}/risk-overview` 的响应（已按严重度 + open 优先排序）。 */
export interface RiskOverview {
  project_id: string
  verdict: 'blocked' | 'warn' | 'pass' | string
  checked_at: string | null
  gate: RiskGate
  tiers: RiskTierSummary[]
  by_category: RiskCategorySummary[]
  items: Issue[]
}

/** 实质性条款的人工响应状态：待确认 / 已响应 / 不适用。 */
export type RequirementResponseStatus = 'pending' | 'responded' | 'na'
/** 五层判定四态：满足 / 不满足 / 部分满足 / 人工复核。 */
export type RequirementVerdict = 'satisfied' | 'unsatisfied' | 'partial' | 'review'

/**
 * `GET /projects/{ref}/requirements` 的一条实质性条款。
 * marker 为 ★ / ▲（空串表示只是 mandatory，没有标记符号）。
 */
export interface RequirementResponse {
  id: string
  kind: string
  marker: string
  /** 自动归类的条款类型（资质/业绩/参数/承诺/通用），用于结构化判定。 */
  requirement_type?: string
  requirement_type_label?: string
  text: string
  mandatory: boolean
  source_quote: string
  response_status: RequirementResponseStatus
  response_note: string
  responded_by: string
  responded_at: string | null
  /** 人工确认时快照的原始自动判定结果。 */
  response_verdict?: string
  response_verdict_label?: string
  response_verdict_reason?: string
  section_id: string | null
  node_id: string | null
  section_heading: string
  auto_covered: boolean
  /** L1 提取证据：页码与条款编号。 */
  page?: number
  clause_no?: string
  /** 五层判定结论。旧后端缺字段时前端按 undefined 处理，不覆盖原状态展示。 */
  verdict?: RequirementVerdict
  verdict_label?: string
  verdict_reason?: string
  verdict_level?: string
  verdict_layers?: Record<string, unknown>
}

/** `POST /projects/{ref}/issues/bulk-status` 的响应。 */
export interface BulkIssueStatusResult {
  updated: number
  items: Issue[]
}

export interface CheckResult {
  verdict: 'pass' | 'warn' | 'blocked' | string
  issues: Issue[]
  summary?: {
    total?: number
    [key: string]: unknown
  }
}

export interface GenerationReport {
  written?: number
  fallback?: number
  failed?: number
  succeeded?: number
  skipped?: number
  total?: number
  failed_labels?: string[]
  suspicious_numbers?: string[] | number
  usage?: unknown
}

export interface Job {
  id: string
  status: 'queued' | 'running' | 'done' | 'failed' | string
  progress?: number
  message?: string
  payload?: {
    heading?: string
    done?: number
    total?: number
    succeeded?: number
    failed?: number
    skipped?: number
    current?: string
  }
  result?: GenerationReport
  error?: string
}

export interface UploadResult {
  project?: Project
  ledger_summary?: LedgerSummary
  notes?: string[]
  missing_required?: string[]
}

export interface ParseResult {
  project?: Project
  ledger_summary?: LedgerSummary
  notes?: string[]
  missing_required?: string[]
}

export interface ProjectDetail extends Project {
  ledger_summary?: LedgerSummary
  missing_required?: string[]
  outline?: number | OutlineChapter[]
  sources?: SourceFile[]
  stats?: Partial<ProjectStats>
  checks_total?: number
}

/** ---------------------------------------------------------------- 批量操作 */

export type BatchFormat = 'docx' | 'md' | 'xlsx'

/** 单个项目在批量任务里的结果。`ok` 为假时 `error` 说明原因。 */
export interface BatchItemResult {
  project_id: string
  name: string
  ok: boolean
  /** 仅批量核对：问题总数。 */
  issues?: number
  /** 仅批量核对：阻断级问题数。 */
  blocking?: number
  /** 仅批量导出：zip 内的文件名。 */
  arcname?: string
  error?: string
}

export interface BatchExportFile {
  project_id: string
  name: string
  arcname: string
}

export interface BatchExportFailure {
  project_id: string
  name: string
  error: string
}

/** 终态任务汇总。`failed` 统一成数量，失败明细在 `failedItems`。 */
export interface BatchJobResult {
  total: number
  ok: number
  failed: number
  results: BatchItemResult[]
  files: BatchExportFile[]
  failedItems: BatchExportFailure[]
  zip_path: string
  error: string
}

/**
 * 批量任务轮询视图。后端 Job 的 payload/result 在不同版本里字段略有差异，
 * 统一由 `normalizeBatchJob` 归一化后再给界面使用。
 */
export interface BatchJob {
  id: string
  kind: 'batch_check' | 'batch_export' | string
  status: 'queued' | 'running' | 'done' | 'failed' | string
  progress: number
  message: string
  error: string
  done: number
  total: number
  okCount: number
  failedCount: number
  /** 正在处理的项目名（旧后端可能为空）。 */
  current: string
  result: BatchJobResult
}

export interface OutlineResponse {
  chapters: OutlineChapter[]
  mode?: string
}

export interface ExportFormat {
  id: 'docx' | 'pdf' | 'xlsx' | 'md'
  title: string
  extension: string
  description: string
}

export interface ExportRecord {
  filename: string
  format: string
  at: string
}


export interface BillingPackage {
  id: string
  name: string
  credits: number
  amount_cents: number
}

export interface VipPackage {
  id: string
  name: string
  days: number
  amount_cents: number
}

export interface BillingSummary {
  balance: number
  chapter_cost: number
  signup_credits: number
  billing_exempt: boolean
  packages: BillingPackage[]
  is_vip?: boolean
  vip_expires_at?: string | null
  vip_packages?: VipPackage[]
  vip_discount?: number
  sub_account_limit?: number
  is_sub?: boolean
  parent_username?: string
}

export type CreditKind = 'signup' | 'recharge' | 'redeem' | 'consume' | 'refund' | 'admin' | 'vip'

export interface CreditTransaction {
  id: string
  amount: number
  balance_after: number
  kind: CreditKind | string
  note?: string
  ref_type?: string
  ref_id?: string
  created_at?: string | null
}

export type OrderStatus = 'pending' | 'paid' | 'closed' | 'expired' | string

export interface RechargeOrder {
  id: string
  out_trade_no: string
  amount_cents: number
  credits: number
  status: OrderStatus
  provider: string
  package_id: string
  code_url?: string
  transaction_id?: string
  paid_at?: string | null
  created_at?: string | null
}

export interface BillingData {
  user: User
  summary: BillingSummary
  transactions: CreditTransaction[]
  orders: RechargeOrder[]
  wechat_login: boolean
  wechat_mock: boolean
  wechat_pay: boolean
  wechat_pay_mock: boolean
}

export interface MockPayResult {
  order: RechargeOrder
  user: User
  summary: BillingSummary
}

export interface RedeemResult {
  user: User
  summary: BillingSummary
  transactions: CreditTransaction[]
}

export interface AdminBillingSettings {
  signup_credits: number
  chapter_cost: number
  packages: BillingPackage[]
  vip_packages?: VipPackage[]
  vip_discount?: number
  sub_account_limit?: number
}

export interface SubAccount {
  id: string
  username: string
  display_name: string
  active: boolean
  created_at?: string | null
  last_login_at?: string | null
}

export interface SubAccountInput {
  username: string
  password: string
  display_name?: string
}

export interface AdminBillingStats {
  users: number
  external_users: number
  revenue_cents: number
  outstanding_credits: number
}

export interface RedeemCode {
  id: string
  code: string
  credits: number
  amount_cents: number
  status: string
  batch: string
  used_by?: string | null
  used_at?: string | null
  expires_at?: string | null
  created_at?: string | null
}

export interface AdminBillingData {
  settings: AdminBillingSettings
  stats: AdminBillingStats
  orders: RechargeOrder[]
  codes: RedeemCode[]
}

export interface CreditAdjustResult {
  user: User
  transactions: CreditTransaction[]
}


export interface ImageAsset {
  id: string
  project_id: string
  node_id?: string | null
  label: string
  prompt?: string
  status: 'ready' | 'failed' | string
  url: string
  filename?: string
  size_bytes?: number
  provider?: string
  model?: string
  options?: Record<string, unknown>
  created_at?: string | null
  last_error?: string
}

export interface ImageCreateInput {
  label: string
  node_id?: string
  heading?: string
  prompt?: string
  style?: string
  size?: string
}

export interface ImagePlanItem {
  label: string
  token: string
  count: number
  status: 'ready' | 'failed' | 'missing' | string
  asset_id?: string | null
  url?: string
  last_error?: string
}

export interface ImagePlanChapter {
  id: string
  heading: string
  position?: number
  placeholders: ImagePlanItem[]
  total: number
  ready: number
  failed: number
  missing: number
  words?: number
  budget?: number
  over_budget?: boolean
}

/** 整篇配图策略：导出排版与批量生成共用的默认值。 */
export interface ImagePolicy {
  /** 全文配图上限；0 表示不限。 */
  max_total: number
  style: string
  size: string
  captions: boolean
  caption_prefix: string
  width_cm: number
}

/** apply 结果中的全文预算统计，后端未返回时整体缺省。 */
export interface ImagePlanDocument {
  used: number
  max_total: number
  remaining: number | null
  placeholders: number
  ready: number
  failed: number
  missing: number
  over_total: boolean
}

export interface ImagePlanApplyResult {
  applied: number
  skipped_missing: number
  skipped_failed: number
  skipped_over_budget: number
  /** 因全文上限而未插入的占位数；旧后端不返回时按 0 处理。 */
  skipped_over_total: number
  applied_labels: string[]
  chapters: string[]
}

export interface ImagePlan {
  chapters: ImagePlanChapter[]
  summary: {
    placeholders: number
    ready: number
    failed: number
    missing: number
  }
  /** 旧后端不返回策略与全文统计，两个字段都可缺省。 */
  policy?: ImagePolicy
  document?: ImagePlanDocument
}

// ---------------------------------------------------------------- 包件（标段）

/** 项目下的投标包件；同项目名称唯一。清单快照归属某个包件，台账材料仍由项目共享。 */
export interface BidPackage {
  id: string
  name: string
  number: string
  position: number
  /** 该包件是否已有清单快照。 */
  has_boq: boolean
  created_at?: string | null
}

export interface BidPackageInput {
  name: string
  number?: string
}

// ---------------------------------------------------------------- boq 清单对齐

export type BoqStatus =
  | 'matched'
  | 'qty_diff'
  | 'unit_diff'
  | 'spec_diff'
  | 'missing_in_ledger'
  | 'missing_in_boq'

export type BoqAction = 'update' | 'add' | 'ignore' | 'restore'

export interface BoqSnapshot {
  id: string
  filename: string
  sheet: string
  header_row: number
  item_count: number
  warnings?: string[]
  created_at?: string | null
}

export interface BoqDiffPair {
  boq?: string | number | null
  ledger?: string | number | null
  delta?: number | null
}

export interface BoqDiff {
  qty?: BoqDiffPair | null
  unit?: BoqDiffPair | null
  spec?: BoqDiffPair | null
}

export interface BoqLedgerMaterial {
  material_id: string
  name?: string
  spec?: string
  unit?: string
  qty?: number | null
  note?: string
}

export interface BoqPrice {
  unit_price?: number | null
  amount?: number | null
}

export interface BoqRow {
  key: string
  boq_row: number | null
  seq: string
  name: string
  spec: string
  unit: string
  qty: number | null
  note: string
  status: BoqStatus | string
  ignored: boolean
  match_score?: number | null
  ledger?: BoqLedgerMaterial | null
  diff?: BoqDiff | null
  price?: BoqPrice | null
}

export interface BoqSummary {
  boq_rows: number
  matched: number
  qty_diff: number
  unit_diff: number
  spec_diff: number
  missing_in_ledger: number
  missing_in_boq: number
  ignored: number
  total_diffs: number
  match_rate: number
  priced: number
  unpriced: number
  quantity_missing: number
  total_amount: number
  /**
   * 启用调价策略后按系数与小数位四舍五入的合计；策略未启用（或旧后端不返回）时为 null。
   */
  adjusted_total: number | null
  /** 按税率计算的税额；税率为 0 或旧后端不返回时为 null。 */
  tax_amount: number | null
  /** 含税合计（total + tax_amount）；total 缺失时为 null。 */
  total_with_tax: number | null
  /** 按调价策略汇率折算的人民币含税合计（round(total_with_tax × exchange_rate, 2)）；缺失时为 null。 */
  cny_total_with_tax: number | null
}

export interface BoqLedgerSummary {
  materials?: number
  [key: string]: unknown
}

export interface BoqPayload {
  snapshot: BoqSnapshot | null
  rows: BoqRow[]
  summary: BoqSummary
  ledger_summary?: BoqLedgerSummary
}

/** 报价版本：某一时点该包件清单的单价与汇总快照。 */
/** 报价版本的审批状态：草稿 → 待审 → 已定稿 / 已驳回，可退回草稿。 */
export type QuoteVersionStatus = 'draft' | 'submitted' | 'approved' | 'rejected'

export interface BoqQuoteVersion {
  id: string
  name: string
  note: string
  package_id: string | null
  summary: BoqSummary
  created_at: string | null
  /** 快照中已录入单价的条目数。 */
  price_count: number
  /** 审批状态；旧后端缺失时按草稿处理。 */
  status: QuoteVersionStatus
  /** 提交审批时填写的说明。 */
  submit_note: string
  submitted_by: string
  submitted_at: string | null
  /** 审批意见（定稿或驳回时填写）。 */
  review_note: string
  reviewed_by: string
  reviewed_at: string | null
  /** 逐级审批记录（审批链非空时产生）；旧后端缺失时按空列表处理。 */
  approvals: QuoteApprovalRecord[]
  /** 前导已完成的生效级数（0 起）；非待审或空链时为 0。不生效级被跳过、不计数。 */
  approval_stage: number
  /** 生效级总数（按金额门槛过滤后）；旧后端缺失时归一为 1，界面回退本地计算。 */
  approval_stage_count: number
  /** 仅详情接口返回：按清单行 key 保存的单价。 */
  prices?: Record<string, number> | null
}

/** 删除版本的响应。 */
export interface BoqVersionDeleteResult {
  deleted: boolean
  id: string
}

/** 版本对比中的单条价格项。 */
export interface BoqVersionDiffEntry {
  key: string
  name: string
  unit_price: number | null
  amount: number | null
}

/** 版本对比中双方都存在但单价不同的项。 */
export interface BoqVersionDiffChanged {
  key: string
  name: string
  from: number | null
  to: number | null
  from_amount: number | null
  to_amount: number | null
}

/** `GET /projects/{ref}/boq/versions/{id}/diff` 的响应。 */
export interface BoqVersionDiff {
  version_id: string
  /** `"current"` 或另一版本 id。 */
  other_id: string
  added: BoqVersionDiffEntry[]
  removed: BoqVersionDiffEntry[]
  changed: BoqVersionDiffChanged[]
  summary_a: BoqSummary
  summary_b: BoqSummary
  count_a: number
  count_b: number
}

// ---------------------------------------------------------------- 企业定额库

/** 管理员维护的定额条目；清单套价只使用这里的单价。 */
export interface QuotaItem {
  id: string
  name: string
  spec: string
  unit: string
  /** 定额单价（元）；后端保证为有限非负数。 */
  unit_price: number
  category: string
  note: string
  created_at?: string | null
  updated_at?: string | null
}

/** 新建 / 编辑定额时提交的字段；未填写的文本字段按空串保存。 */
export interface QuotaInput {
  name: string
  spec?: string
  unit?: string
  unit_price: number
  category?: string
  note?: string
}

/** `GET /admin/quotas/match` 的候选：score 为 0–1 的相似度。 */
export interface QuotaMatch {
  id: string
  name: string
  spec: string
  unit: string
  unit_price: number
  score: number
}

/** dry_run 提案：把某条清单行匹配到一条定额。 */
export interface QuotaProposal {
  boq_row: number | null
  name: string
  quota_id: string
  quota_name: string
  unit_price: number
  score: number
}

/** quota-fill 的结果；dry_run 时 applied 恒为 0。 */
export interface QuotaFillResult {
  proposals: QuotaProposal[]
  applied: number
}

// ---------------------------------------------------------------- 企业术语库

/** `replace` 建议替换的目标写法；`forbidden` 是禁止出现在正文里的表述。 */
export type TermKind = 'replace' | 'forbidden'

/** 管理员维护的术语条目；只有启用的条目参与正文检查。 */
export interface TermItem {
  id: string
  kind: TermKind | string
  source: string
  target: string
  note: string
  enabled: boolean
  created_at?: string | null
  updated_at?: string | null
}

/** 新建 / 编辑术语时提交的字段；`target` 仅对 replace 有意义。 */
export interface TermInput {
  kind: TermKind | string
  source: string
  target?: string
  note?: string
  enabled?: boolean
}

// ---------------------------------------------------------------- 文本规则（错别字词库）

/**
 * 一条企业自定义错别字规则。`wrong` 是正文里要揪出来的错词，`right` 留空表示
 * 只提示、不给替换建议；`note` 用于说明适用范围或依据。
 */
export interface TypoRule {
  wrong: string
  right: string
  note: string
}

/** `GET/PUT /admin/textqa` 的载荷：自定义规则列表 + 内置词库规模。 */
export interface TextQaSettings {
  typos: TypoRule[]
  /** 自定义规则条数（后端按 wrong 去重后的数量）。 */
  count: number
  /** 内置错别字词库条数；只读，不可在控制台删除。 */
  builtin_count: number
}

// ---------------------------------------------------------------- 调价策略

/** 报价币种，与后端的枚举保持一致。 */
export type PriceCurrency = 'CNY' | 'USD' | 'EUR' | 'HKD'

/** 项目级调价策略；factor 0.1–10，round_digits 0–4，tax_rate 0–0.5。 */
export interface PricePolicy {
  enabled: boolean
  factor: number
  round_digits: number
  /** 税率（小数形式，例如 0.09 表示 9%）；0–0.5。 */
  tax_rate: number
  currency: PriceCurrency
  /** 汇率：1 单位币种 = X CNY，clamp 0.0001–10000；币种为 CNY 时恒为 1。 */
  exchange_rate: number
}

// ---------------------------------------------------------------- 资质与自查

/** 资质台账的固定类型；未知值在归一化时回退为 `other`。 */
export type QualificationKind =
  | 'license'
  | 'safety'
  | 'person'
  | 'performance'
  | 'credit'
  | 'other'

export type QualificationStatus = 'valid' | 'expiring' | 'expired' | 'none'

/** 存在资质目录里的官方查询截图或证照扫描件。 */
export interface QualificationEvidence {
  filename: string
  size: number
  mime: string
  uploaded_at: string | null
}

/** 当前用户的一条资质记录（`GET /my/qualifications` 平铺数组的一项）。 */
export interface Qualification {
  id: string
  kind: QualificationKind
  name: string
  holder: string
  cert_no: string
  issuer: string
  valid_from: string | null
  valid_to: string | null
  note: string
  evidence: QualificationEvidence[]
  status: QualificationStatus
  /** 距到期日的天数；无到期日时为 null。 */
  days_left: number | null
  created_at: string | null
  updated_at: string | null
}

/** 新建 / 编辑资质时提交的字段；日期使用 YYYY-MM-DD，留空表示不填。 */
export interface QualificationInput {
  kind: QualificationKind | string
  name: string
  holder?: string
  cert_no?: string
  issuer?: string
  valid_from?: string | null
  valid_to?: string | null
  note?: string
}

export type PreflightStatus = 'ok' | 'expiring' | 'expired' | 'missing'

/** 按开标日生成的一条自查项；`kinds` 为可满足该项的资质类型。 */
export interface PreflightItem {
  key: string
  label: string
  kinds: QualificationKind[]
  hint: string
  status: PreflightStatus
  qualification_id: string | null
  name: string
  valid_to: string | null
  evidence_count: number
}

/** 自查清单的汇总；`warn` 统计临期项。 */
export interface PreflightSummary {
  ok: number
  warn: number
  missing: number
  total: number
}

export interface PreflightReport {
  deadline: string
  items: PreflightItem[]
  summary: PreflightSummary
}

/** R34 人员台账：本人维护的注册人员与在建锁定状态。 */
export interface Personnel {
  id: string
  name: string
  title: string
  specialty: string
  cert_no: string
  /** 社保连续缴纳月数，0–600。 */
  social_months: number
  /** 在建项目锁定：锁定期间不参与新的投标配置。 */
  locked: boolean
  note: string
  created_at: string | null
  updated_at: string | null
}

/** 新建 / 编辑人员的入参；`name` 必填，文本字段留空由后端截断为 0–80 字。 */
export interface PersonnelInput {
  name: string
  title?: string
  specialty?: string
  cert_no?: string
  social_months?: number
  locked?: boolean
  note?: string
}

// ---------------------------------------------------------------- 知识库素材元数据（R34）

/** 素材类型策略中的一项；`required` 为空表示无需额外字段。 */
export interface KbDocPolicyEntry {
  label: string
  required: string[]
  hint?: string
}

/** 以素材类型为键的策略表；`other` 始终存在。 */
export type KbDocPolicy = Record<string, KbDocPolicyEntry>

/** 一条已登记的素材元数据。未知类型回退为 `other`。 */
export interface KbDocMetaItem {
  source: string
  kind: string
  fields: Record<string, string>
  ok: boolean
  reason: string
}

/** 知识库内出现过的一个片段来源；`chunks` 为片段数。 */
export interface KbDocSource {
  source: string
  chunks: number
}

/** `GET /admin/kbs/{id}/docs` 的归一化响应。 */
export interface KbDocMetaResponse {
  policy: KbDocPolicy
  items: KbDocMetaItem[]
  sources: KbDocSource[]
}

/** `PUT /admin/kbs/{id}/docs` 的请求体。 */
export interface KbDocMetaInput {
  source: string
  kind: string
  fields: Record<string, string>
}

// ---------------------------------------------------------------- 协作与审计

/** `owner` is synthesised by the server from `project.owner_id` and always listed first. */
export type MemberRole = 'owner' | 'editor' | 'viewer'

export interface Member {
  user_id: string
  username: string
  display_name: string
  role: MemberRole | string
  created_at?: string | null
}

export interface AuditEntry {
  id: number
  action: string
  username?: string
  display_name?: string
  detail?: Record<string, unknown> | null
  created_at?: string | null
  /** Only present on the administrator feed. */
  project_id?: string | null
  project_name?: string | null
}

export interface AuditPage {
  items: AuditEntry[]
  next_before: number | null
}

// ---------------------------------------------------------------- 插件市场（MCP 客户端）

/** stdio 走本机子进程，http 走远端 JSON-RPC 端点；旧后端可能返回其他值。 */
export type PluginTransport = 'stdio' | 'http'

/** 管理员登记的一个 MCP 服务；`last_test_*` 与 ModelConfig 同模式。 */
export interface Plugin {
  id: string
  name: string
  transport: PluginTransport | string
  /** stdio 的可执行文件；http 插件为空串。 */
  command: string
  args: string[]
  env: Record<string, string>
  /** http 插件的端点地址；stdio 插件为空串。 */
  url: string
  enabled: boolean
  /** 空数组表示不限制，聚合时暴露该插件的全部工具。 */
  tool_allowlist: string[]
  /** stdio 子进程内存上限（MB），范围 64–4096，缺省 512。 */
  mem_limit_mb: number
  /** stdio 子进程 CPU 时间上限（秒），范围 1–120，缺省 10。 */
  cpu_seconds: number
  /** global = 所有调用者可见；owner = 仅归属用户与超管可见。 */
  scope?: 'global' | 'owner' | string
  /** scope=owner 时的归属用户 id。 */
  owner_id?: string | null
  /** HTTP 插件是否允许访问本机/私网地址（默认 false）。 */
  allow_private_net?: boolean
  last_test_at?: string | null
  last_test_ok?: boolean | null
  last_test_message?: string
  /** 连续失败次数；达到阈值后端会自动停用插件。 */
  fail_streak?: number
  /** 因连续失败被自动停用（与手动停用区分）。 */
  auto_disabled?: boolean
  /** 后端若在列表里带上工具清单（非契约必选字段），用于展示工具数。 */
  tools?: string[]
  created_at?: string | null
  updated_at?: string | null
}

/** 新建 / 编辑插件时提交的字段；未使用的传输方式字段留空。 */
export interface PluginInput {
  name: string
  transport: PluginTransport | string
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  enabled?: boolean
  tool_allowlist?: string[]
  /** 内存上限（MB），64–4096；省略时后端使用默认值 512。 */
  mem_limit_mb?: number
  /** CPU 时间上限（秒），1–120；省略时后端使用默认值 10。 */
  cpu_seconds?: number
  /** 个人插件需同时提交 owner_id；global 时忽略 owner_id。 */
  scope?: 'global' | 'owner' | string
  owner_id?: string | null
  /** 仅 HTTP 插件：可信内网端点需显式开启，默认 false。 */
  allow_private_net?: boolean
}

/** `POST /admin/plugins/{id}/test` 的结果；失败时 ok=false 但仍返回说明。 */
export interface PluginTestResult {
  ok: boolean
  message: string
  /** 服务端 tools/list 返回的工具名（未按白名单过滤）。 */
  tools: string[]
  latency_ms?: number
}

/** `GET /admin/plugins/catalog` 的内置预设；只填参数，不安装任何依赖。 */
export interface PluginCatalogEntry {
  name: string
  description: string
  transport: PluginTransport | string
  command: string
  args: string[]
  url: string
}

/** Administrator view of WeChat Open Platform settings (`/admin/wechat`). */
export interface WechatConfig {
  app_id: string
  has_secret: boolean
  redirect_uri: string
  enabled: boolean
  mock: boolean
  available: boolean
  /** Where the effective settings come from: stored config or environment. */
  source: 'db' | 'env' | string
}

export interface WechatConfigInput {
  app_id?: string
  app_secret?: string
  clear_secret?: boolean
  redirect_uri?: string
  enabled?: boolean
  mock?: boolean
}

/** Payload of a 409 response from `PUT /projects/{ref}/chapters/{node_id}`. */
export interface ChapterConflict {
  current_revision: number
  current_body: string
  current_words: number
  message?: string
}

declare global {
  interface Window {
    /** Set by the desktop client gateway (`/__client/token.js`). */
    __BIDCRAFT_CLIENT_TOKEN?: string
    __BIDCRAFT_SERVER?: string
  }
}

export interface ProfileExtraFact {
  key: string
  value: string
}

export interface WritingProfile {
  company_name: string
  legal_rep: string
  project_name: string
  owner_name: string
  bid_amount: string
  duration_days: string
  warranty_years: string
  quality_standard: string
  tone: '稳健' | '简洁' | '技术' | '亲和'
  forbidden_phrases: string[]
  extra_facts: ProfileExtraFact[]
}

export interface ProjectProfileResponse {
  profile: WritingProfile
  anchors: Record<string, string>
  facts_block: string
}

export interface ScoreEntry {
  id: string
  name: string
  score: number | null
  category: string
  criteria: string
  section_hint: string
  actual_score: number | null
  actual_bidder: string
  score_note: string
  rate: number | null
  loss: number | null
}

export interface ScoreDimension {
  category: string
  count: number
  max: number
  actual: number
  rate: number | null
}

export interface ScoreAnalysis {
  project_id: string
  total_items: number
  filled: number
  total_max: number
  total_actual: number
  rate: number | null
  dimensions: ScoreDimension[]
  losses: ScoreEntry[]
  items: ScoreEntry[]
}

// ---------------------------------------------------------------- 企业知识库 / 企业共享（第二批）

/** 企业（团队）账户；企业内共享资料与成员。 */
export interface Enterprise {
  id: string
  name: string
  owner_id?: string
  /** 当前用户在企业中的角色（后端可省略）。 */
  role?: string
  member_count?: number
  document_count?: number
  created_at?: string | null
}

/** 企业成员；`user_id` 用于移除成员。 */
export interface EnterpriseMember {
  enterprise_id?: string
  user_id: string
  username: string
  display_name?: string
  role?: string
  created_at?: string | null
}

export interface EnterpriseMemberInput {
  username: string
  role?: string
}

/** 企业资料入库入参；`tags` 为标签列表，`valid_to` 为 YYYY-MM-DD。 */
export interface EnterpriseDocumentInput {
  source: string
  content: string
  kind?: string
  tags?: string[]
  valid_to?: string | null
}

/** 企业共享资料（业绩、证书、人员等）。 */
export interface EnterpriseDocument {
  id: string
  enterprise_id?: string
  source: string
  content: string
  kind: string
  tags: string[]
  valid_to: string | null
  created_at?: string | null
  created_by?: string
}

/** 资产匹配结果：项目需求命中的企业资料。 */
export interface EnterpriseMatch {
  id?: string
  document_id?: string
  source: string
  kind?: string
  excerpt?: string
  score?: number | null
  tags?: string[]
  valid_to?: string | null
  reason?: string
}

/** 材料包条目；后端可返回条目（items）或资料（documents）。 */
export interface EnterprisePackageItem {
  id?: string
  label?: string
  title?: string
  source?: string
  kind?: string
  status?: string
  note?: string
  count?: number
}

/** 项目企业材料包。 */
export interface EnterprisePackage {
  id?: string
  name?: string
  status?: string
  items?: EnterprisePackageItem[]
  documents?: EnterpriseDocument[]
  generated_at?: string | null
}

// ---------------------------------------------------------------- 商务标（第二批）

export type CommercialStatus = 'draft' | 'submitted' | 'approved' | 'rejected'

/** 商务标章节预览行；字段随后端模板而定。 */
export interface CommercialSection {
  key?: string
  title: string
  content?: string
  status?: string
  note?: string
}

/** 商务标校验问题；提交/定稿前提示。 */
export interface CommercialValidationIssue {
  code?: string
  message: string
  severity?: string
  section?: string
}

export interface CommercialValidation {
  ok?: boolean
  issues: CommercialValidationIssue[]
}

/** 商务标文档：绑定一个已定稿报价版本，含章节与校验结果。 */
export interface CommercialDoc {
  id: string
  name: string
  quote_version_id: string
  package_id?: string | null
  status: CommercialStatus
  sections: CommercialSection[]
  validation: CommercialValidation
  submit_note?: string
  submitted_by?: string
  submitted_at?: string | null
  review_note?: string
  reviewed_by?: string
  reviewed_at?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface CommercialDocInput {
  name: string
  quote_version_id: string
  package_id?: string | null
}

export type CommercialExportFormat = 'xlsx' | 'docx'

// ---------------------------------------------------------------- 多维查重（第二批）

export type SimilarityScope = 'all' | 'internal' | 'external' | string
export type SimilarityMode = 'paragraph' | 'sentence' | 'section' | string
export type SimilarityHitStatus = 'pending' | 'confirmed' | 'ignored' | string

/** 查重报告统计；字段随版本演进，未知键按 unknown 保留。 */
export interface SimilarityStats {
  total?: number
  checked?: number
  hits?: number
  reviewed?: number
  pending?: number
  skipped?: number
  by_kind?: Record<string, number>
  [key: string]: unknown
}

/** 查重报告。`status` 为 queued/running/done/failed 等服务端状态。 */
export interface SimilarityReport {
  id: string
  scope: string
  mode: string
  threshold: number
  status: string
  stats: SimilarityStats
  /** 跳过的条目数或说明；后端形状可能不同。 */
  skipped?: number | string[]
  source_project_id?: string | null
  created_at: string | null
  finished_at?: string | null
}

/** 查重命中：同一段内容在两个来源中的位置与相似度。 */
export interface SimilarityHit {
  id: string
  kind: string
  source_a: string
  source_b: string
  loc_a?: string
  loc_b?: string
  excerpt_a: string
  excerpt_b: string
  similarity: number
  review_status: SimilarityHitStatus
  review_note?: string
}

// ---------------------------------------------------------------- Agent 编排（第三批）

/** Agent 注册表条目；注册表可扩展，早期后端可能只返回 agent_type / name。 */
export interface AgentInfo {
  agent_type: string
  name: string
  description?: string
  role?: string
  capabilities?: string[]
  tools?: string[]
  inputs?: string[]
  outputs?: string[]
  enabled?: boolean
  [key: string]: unknown
}

export type WorkflowStatus =
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'done'
  | 'failed'
  | 'cancelled'

export type WorkflowTaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export type WorkflowApprovalStatus = 'pending' | 'approved' | 'rejected'

/** 工作流统计；具体键随执行器实现扩展。 */
export interface WorkflowStats {
  tasks?: number
  total?: number
  done?: number
  succeeded?: number
  failed?: number
  skipped?: number
  tokens?: number
  prompt_tokens?: number
  completion_tokens?: number
  cost?: number
  cost_usd?: number
  duration_ms?: number
  [key: string]: unknown
}

/** 工作流预算；token 上限与实际用量都可能在预算对象里。 */
export interface WorkflowBudget {
  tokens?: number
  cost?: number
  max_tokens?: number
  max_cost?: number
  used_tokens?: number
  used_cost?: number
  [key: string]: unknown
}

export interface WorkflowRun {
  id: string
  project_id?: string
  workflow_type: string
  status: WorkflowStatus | string
  goal: string
  result?: Record<string, unknown> | null
  stats?: WorkflowStats | null
  budget?: WorkflowBudget | null
  created_at?: string | null
  completed_at?: string | null
  [key: string]: unknown
}

export interface WorkflowTask {
  id: string
  seq: number
  task_key: string
  agent_type: string
  status: WorkflowTaskStatus | string
  error?: string
  attempts?: number
  started_at?: string | null
  completed_at?: string | null
  [key: string]: unknown
}

export interface WorkflowApproval {
  id: string
  approval_type: string
  status: WorkflowApprovalStatus | string
  review_note?: string
  run_id?: string
  task_id?: string | null
  submitted_by?: string
  reviewed_by?: string
  submitted_at?: string | null
  reviewed_at?: string | null
  [key: string]: unknown
}

export interface WorkflowCreateInput {
  workflow_type: string
  goal: string
  input?: Record<string, unknown>
  budget?: Record<string, unknown>
}

export interface WorkflowApprovalReviewInput {
  decision: 'approve' | 'reject' | string
  note?: string
}

// ---------------------------------------------------------------- 协作版本（第三批）

export type SectionChangeType = 'edit' | 'restore' | 'generate'

export type ChapterAssignmentStatus = 'assigned' | 'in_progress' | 'done'

export interface SectionRevision {
  id: string
  node_id: string
  revision: number
  body_hash?: string
  changed_by?: string
  change_type: SectionChangeType | string
  change_note?: string
  created_at?: string | null
  [key: string]: unknown
}

export interface SectionRevisionDetail extends SectionRevision {
  body: string
}

/** 双栏差异中的一行；left 为空表示新增，right 为空表示删除。 */
export interface RevisionDiffRow {
  kind: 'equal' | 'add' | 'remove' | 'change'
  left?: string
  right?: string
}

export interface SectionDiff {
  from?: number | null
  to?: number | null
  from_body?: string
  to_body?: string
  rows: RevisionDiffRow[]
  added: number
  removed: number
  /** 服务端返回无法结构化的 diff 文本时保留原文，避免丢掉信息。 */
  text?: string
}

export interface SectionLock {
  node_id: string
  user_id: string
  lock_token: string
  expires_at?: string | null
  display_name?: string
  username?: string
  [key: string]: unknown
}

export interface ChapterAssignment {
  id: string
  node_id: string
  assignee_id: string
  status: ChapterAssignmentStatus | string
  deadline?: string | null
  note?: string
  assigned_by?: string
  created_at?: string | null
  updated_at?: string | null
  [key: string]: unknown
}

export interface ChapterAssignmentInput {
  node_id: string
  assignee_id: string
  deadline?: string | null
  note?: string
}

export interface ChapterAssignmentUpdateInput {
  status: string
  note?: string
}

export interface RestoreRevisionInput {
  revision: number
  note: string
}

/** 质量评估维度得分（对标易标第四批）。 */
export interface QualityDimension {
  key: string
  label: string
  score: number
  weight: number
  weighted: number
}

/** 质量问题项（可人工处置）。 */
export interface QualityFinding {
  id: string
  report_id: string
  category: string
  level: string
  node_id: string | null
  scoring_item_id: string | null
  requirement_id: string | null
  title: string
  description: string
  evidence: Record<string, unknown>
  recommendation: string
  status: string
}

/** 质量报告快照（不可变，每次运行版本号 +1）。 */
export interface QualityReport {
  id: string
  project_id: string
  report_version: number
  overall_score: number
  grade: string
  verdict: 'pass' | 'warn' | 'blocked' | string
  dimensions: QualityDimension[]
  summary: {
    coverage?: Record<string, unknown>
    recommendations?: string[]
    note?: string
    commercial?: {
      ok: boolean
      ratio: number
      missing: Array<{ section: string; field: string; label: string }>
      checked: number
      doc_id: string
      doc_status: string
      source: string
    }
  }
  risk_summary: Record<string, unknown>
  created_by: string
  created_at: string | null
  findings: QualityFinding[]
}

export interface QualityRunInput {
  note?: string
}
