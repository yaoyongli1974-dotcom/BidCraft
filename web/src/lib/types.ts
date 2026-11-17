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
  wechat_nickname?: string
  avatar_url?: string
  last_login_at?: string | null
  created_at?: string | null
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

export interface BillingSummary {
  balance: number
  chapter_cost: number
  signup_credits: number
  billing_exempt: boolean
  packages: BillingPackage[]
}

export type CreditKind = 'signup' | 'recharge' | 'redeem' | 'consume' | 'refund' | 'admin'

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
export interface BoqQuoteVersion {
  id: string
  name: string
  note: string
  package_id: string | null
  summary: BoqSummary
  created_at: string | null
  /** 快照中已录入单价的条目数。 */
  price_count: number
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

/** Payload of a 409 response from `PUT /projects/{ref}/chapters/{node_id}`. */
export interface ChapterConflict {
  current_revision: number
  current_body: string
  current_words: number
  message?: string
}
