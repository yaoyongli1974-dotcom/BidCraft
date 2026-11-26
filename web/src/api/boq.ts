import { ApiError, asArray, jsonBody, notifyUnauthorized, readError, request, uploadForm } from '../lib/http'
import type { BoqAction, BoqPayload, BoqQuoteVersion, BoqSummary, BoqVersionDeleteResult, BoqVersionDiff, PricePolicy, QuotaFillResult, QuotaInput, QuotaItem, QuotaMatch, QuotaProposal } from '../lib/types'
import { filenameFromDisposition } from './generation'
import { normalizeApprovalExplain } from './project'
import { EXCHANGE_RATE_MAX, EXCHANGE_RATE_MIN, asBool, asLooseBool, asNumber, asText, boqNumber, boqPackageQuery, clampExchangeRate, clampNumber, isoOrNull, normalizeBoqPayload, normalizeBoqVersionDiffChanged, normalizeBoqVersionDiffEntry, normalizeCurrency, normalizeQuotaItem, normalizeQuotaProposal, normalizeQuoteApprovals, normalizeQuoteVersionStatus, nullableNumber, postQuoteVersionAction, quotaItems } from './shared'

// ---------------------------------------------------------------- boq domain
export function normalizeBoqSummary(value: unknown): BoqSummary {
  const source = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return {
    boq_rows: boqNumber(source.boq_rows),
    matched: boqNumber(source.matched),
    qty_diff: boqNumber(source.qty_diff),
    unit_diff: boqNumber(source.unit_diff),
    spec_diff: boqNumber(source.spec_diff),
    missing_in_ledger: boqNumber(source.missing_in_ledger),
    missing_in_boq: boqNumber(source.missing_in_boq),
    ignored: boqNumber(source.ignored),
    total_diffs: boqNumber(source.total_diffs),
    match_rate: boqNumber(source.match_rate),
    priced: boqNumber(source.priced),
    unpriced: boqNumber(source.unpriced),
    quantity_missing: boqNumber(source.quantity_missing),
    total_amount: boqNumber(source.total_amount),
    // 旧后端没有调价策略，字段缺失即视为「无调整后合计」，界面照常显示原合计。
    adjusted_total:
      typeof source.adjusted_total === 'number' && Number.isFinite(source.adjusted_total)
        ? source.adjusted_total
        : null,
    // 税额与含税合计仅在税率为正时由后端返回；缺失时保持 null，避免误显 0。
    tax_amount: nullableNumber(source.tax_amount),
    total_with_tax: nullableNumber(source.total_with_tax),
    // 人民币含税合计由新后端按汇率折算；旧后端缺失时界面用本地兜底。
    cny_total_with_tax: nullableNumber(source.cny_total_with_tax),
  }
}

export async function getBoq(
  ref: string,
  packageId?: string | null,
): Promise<BoqPayload> {
  const data = await request<Partial<BoqPayload>>(
    `/projects/${encodeURIComponent(ref)}/boq${boqPackageQuery(packageId)}`,
  )
  return normalizeBoqPayload(data)
}

/**
 * 上传工程量清单工作簿。`sheet` 为可选工作表名；后端解析失败返回 400 中文错误。
 */

export function uploadBoq(
  ref: string,
  file: File,
  sheet?: string,
  onProgress?: (percent: number) => void,
  packageId?: string | null,
): Promise<BoqPayload> {
  const form = new FormData()
  form.append('file', file)
  const sheetName = (sheet || '').trim()
  if (sheetName) form.append('sheet', sheetName)
  const packageValue = (packageId || '').trim()
  if (packageValue) form.append('package_id', packageValue)
  return uploadForm<Partial<BoqPayload>>(
    `/projects/${encodeURIComponent(ref)}/boq`,
    form,
    { onProgress },
  ).then(normalizeBoqPayload)
}

export const applyBoq = (
  ref: string,
  input: { boq_row: number; action: BoqAction; package_id?: string | null },
): Promise<BoqPayload> =>
  request<BoqPayload>(`/projects/${encodeURIComponent(ref)}/boq/apply`, {
    method: 'POST',
    body: JSON.stringify(input),
  })

export const setBoqPrice = (
  ref: string,
  input: { boq_row: number; unit_price: number | null; package_id?: string | null },
): Promise<BoqPayload> =>
  request<BoqPayload>(`/projects/${encodeURIComponent(ref)}/boq/price`, {
    method: 'POST',
    body: JSON.stringify(input),
  })

export const deleteBoq = (ref: string, packageId?: string | null) =>
  request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(ref)}/boq${boqPackageQuery(packageId)}`,
    { method: 'DELETE' },
  )

export function normalizeBoqQuoteVersion(value: unknown): BoqQuoteVersion | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const source = value as Record<string, unknown>
  const id = asText(source.id).trim()
  if (!id) return null
  const rawPrices = source.prices
  let prices: Record<string, number> | undefined
  if (rawPrices && typeof rawPrices === 'object' && !Array.isArray(rawPrices)) {
    prices = {}
    for (const [key, raw] of Object.entries(rawPrices as Record<string, unknown>)) {
      if (typeof raw === 'number' && Number.isFinite(raw)) prices[key] = raw
    }
  }
  const packageId = asText(source.package_id).trim()
  return {
    id,
    name: asText(source.name),
    note: asText(source.note),
    package_id: packageId || null,
    summary: normalizeBoqSummary(source.summary),
    created_at: isoOrNull(source.created_at),
    price_count: boqNumber(source.price_count),
    status: normalizeQuoteVersionStatus(source.status),
    submit_note: asText(source.submit_note).slice(0, 200),
    submitted_by: asText(source.submitted_by).slice(0, 80),
    submitted_at: isoOrNull(source.submitted_at),
    review_note: asText(source.review_note).slice(0, 200),
    reviewed_by: asText(source.reviewed_by).slice(0, 80),
    reviewed_at: isoOrNull(source.reviewed_at),
    approvals: normalizeQuoteApprovals(source.approvals),
    // RC-09 后端逐阶段解释必须原样透传；BoqVersions 优先用它，仅在缺省时本地镜像兜底。
    approval_explain: normalizeApprovalExplain(source.approval_explain),
    approval_stage: Math.max(0, Math.round(asNumber(source.approval_stage, 0))),
    // R21 生效级总数：旧后端不返回时缺省 1，界面用本地 applicableStages 回退。
    approval_stage_count: Math.max(1, Math.round(asNumber(source.approval_stage_count, 1))),
    ...(prices ? { prices } : {}),
  }
}

/** 版本状态流转（提交/审批/撤销）统一返回最新版本 payload。 */

export async function submitBoqVersion(
  ref: string,
  versionId: string,
  note?: string,
): Promise<BoqQuoteVersion> {
  const trimmed = (note || '').trim().slice(0, 200)
  return postQuoteVersionAction(ref, versionId, 'submit', trimmed ? { note: trimmed } : undefined)
}

/**
 * 审批版本：定稿（approve）或驳回（reject）。空审批链时由项目负责人或超级管理员
 * 审批；链非空时仅当前级审批人或超级管理员可审，否则后端返回 403（detail 含「第 N 级」）。
 */

export async function reviewBoqVersion(
  ref: string,
  versionId: string,
  decision: 'approve' | 'reject',
  note?: string,
): Promise<BoqQuoteVersion> {
  const trimmed = (note || '').trim().slice(0, 200)
  return postQuoteVersionAction(ref, versionId, 'review', {
    decision,
    ...(trimmed ? { note: trimmed } : {}),
  })
}

/** 撤销定稿，把版本退回草稿。 */

export async function unapproveBoqVersion(
  ref: string,
  versionId: string,
): Promise<BoqQuoteVersion> {
  return postQuoteVersionAction(ref, versionId, 'unapprove')
}

/** 列出项目（或指定包件）的报价版本，最新的在前。 */

export async function listBoqVersions(
  ref: string,
  packageId?: string | null,
): Promise<BoqQuoteVersion[]> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions${boqPackageQuery(packageId)}`,
  )
  const list = Array.isArray(data) ? data : asArray<unknown>(data, 'versions')
  return list
    .map(normalizeBoqQuoteVersion)
    .filter((item): item is BoqQuoteVersion => item !== null)
}

/** 把当前清单单价与汇总保存为一个版本。 */

export async function createBoqVersion(
  ref: string,
  input: { name: string; note?: string; package_id?: string | null },
): Promise<BoqQuoteVersion> {
  const packageId = (input.package_id || '').trim()
  const note = (input.note || '').trim()
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions`,
    {
      method: 'POST',
      body: JSON.stringify({
        name: input.name.trim(),
        ...(note ? { note } : {}),
        ...(packageId ? { package_id: packageId } : {}),
      }),
    },
  )
  return (
    normalizeBoqQuoteVersion(data) ?? {
      id: '',
      name: input.name.trim(),
      note,
      package_id: packageId || null,
      summary: normalizeBoqSummary(null),
      created_at: null,
      price_count: 0,
      status: 'draft',
      submit_note: '',
      submitted_by: '',
      submitted_at: null,
      review_note: '',
      reviewed_by: '',
      reviewed_at: null,
      approvals: [],
      approval_explain: [],
      approval_stage: 0,
      approval_stage_count: 1,
    }
  )
}

/** 读取版本详情（含单价快照）。 */

export async function getBoqVersion(
  ref: string,
  versionId: string,
): Promise<BoqQuoteVersion> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}`,
  )
  const version = normalizeBoqQuoteVersion(data)
  if (!version) throw new ApiError(0, null, '报价版本返回了无法识别的数据。')
  return version
}

/** 把版本单价写回当前清单，返回最新的 BOQ payload。 */

export async function restoreBoqVersion(
  ref: string,
  versionId: string,
): Promise<BoqPayload> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}/restore`,
    { method: 'POST' },
  )
  return normalizeBoqPayload(data)
}

export async function deleteBoqVersion(
  ref: string,
  versionId: string,
): Promise<BoqVersionDeleteResult> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}`,
    { method: 'DELETE' },
  )
  const source = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return {
    deleted: asBool(source.deleted),
    id: asText(source.id) || versionId,
  }
}

export async function getBoqVersionDiff(
  ref: string,
  versionId: string,
  other?: string | null,
): Promise<BoqVersionDiff> {
  const query = other && other !== 'current' ? `?other=${encodeURIComponent(other)}` : ''
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/boq/versions/${encodeURIComponent(versionId)}/diff${query}`,
  )
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  return {
    version_id: asText(raw.version_id),
    other_id: asText(raw.other_id),
    added: asArray<unknown>(raw.added, 'added').map(normalizeBoqVersionDiffEntry),
    removed: asArray<unknown>(raw.removed, 'removed').map(normalizeBoqVersionDiffEntry),
    changed: asArray<unknown>(raw.changed, 'changed').map(normalizeBoqVersionDiffChanged),
    summary_a: normalizeBoqSummary(raw.summary_a),
    summary_b: normalizeBoqSummary(raw.summary_b),
    count_a: asNumber(raw.count_a, 0),
    count_b: asNumber(raw.count_b, 0),
  }
}

export async function exportBoq(
  ref: string,
  packageId?: string | null,
): Promise<{ blob: Blob; filename: string }> {
  let response: Response
  try {
    response = await fetch(
      `/projects/${encodeURIComponent(ref)}/boq/export${boqPackageQuery(packageId)}`,
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: '*/*' },
      },
    )
  } catch {
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(`/projects/${ref}/boq/export`)
    throw await readError(response)
  }
  const blob = await response.blob()
  const filename =
    filenameFromDisposition(response.headers.get('Content-Disposition')) || '清单响应表.xlsx'
  return { blob, filename }
}

export async function quotaFill(
  ref: string,
  input: {
    package_id?: string | null
    only_missing?: boolean
    dry_run?: boolean
  } = {},
): Promise<QuotaFillResult> {
  const packageId = (input.package_id || '').trim()
  const data = await request<{ proposals?: unknown; applied?: unknown }>(
    `/projects/${encodeURIComponent(ref)}/boq/quota-fill`,
    jsonBody({
      ...(packageId ? { package_id: packageId } : {}),
      only_missing: input.only_missing !== false,
      dry_run: input.dry_run !== false,
    }),
  )
  const proposals = Array.isArray(data?.proposals)
    ? (data.proposals.map(normalizeQuotaProposal).filter(Boolean) as QuotaProposal[])
    : []
  return {
    proposals,
    applied: typeof data?.applied === 'number' ? data.applied : 0,
  }
}

export const DEFAULT_PRICE_POLICY: PricePolicy = {
  enabled: false,
  factor: 1,
  round_digits: 2,
  tax_rate: 0,
  currency: 'CNY',
  exchange_rate: 1,
}

export function roundTo(value: number, digits: number): number {
  const places = Math.round(clampNumber(digits, 0, 4))
  const factor = 10 ** places
  return Math.round((value + Number.EPSILON) * factor) / factor
}

/**
 * 人民币含税合计：优先使用后端折算的 `cny_total_with_tax`；
 * 旧后端缺失该字段时，按含税合计 × 汇率在本地兜底（与后端同一四舍五入口径）。
 */

export function cnyTotalWithTax(
  cnyTotal: number | null | undefined,
  totalWithTax: number | null | undefined,
  exchangeRate: number | null | undefined,
): number | null {
  if (typeof cnyTotal === 'number' && Number.isFinite(cnyTotal)) return cnyTotal
  if (typeof totalWithTax !== 'number' || !Number.isFinite(totalWithTax)) return null
  const rate =
    typeof exchangeRate === 'number' && Number.isFinite(exchangeRate)
      ? clampNumber(exchangeRate, EXCHANGE_RATE_MIN, EXCHANGE_RATE_MAX)
      : 1
  return roundTo(totalWithTax * rate, 2)
}

/**
 * 归一化调价策略：缺省取默认值并按契约 clamp，
 * 保证界面上的数字始终落在后端接受的区间内。
 */

export function normalizePricePolicy(value: unknown): PricePolicy {
  const outer = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const nested = outer.price_policy
  const source =
    nested && typeof nested === 'object' && !Array.isArray(nested)
      ? (nested as Record<string, unknown>)
      : outer
  const factor = Math.round(clampNumber(asNumber(source.factor, 1), 0.1, 10) * 100) / 100
  const currency = normalizeCurrency(source.currency)
  return {
    enabled: asLooseBool(source.enabled, false),
    factor,
    round_digits: Math.round(clampNumber(asNumber(source.round_digits, 2), 0, 4)),
    // 税率按后端 0–0.5 的区间 clamp；非法值回退到 0（免税）。
    tax_rate: Math.round(clampNumber(asNumber(source.tax_rate, 0), 0, 0.5) * 10000) / 10000,
    currency,
    // 仅非人民币币种保留汇率输入；CNY 强制为 1，避免保存出无意义的折算。
    exchange_rate: clampExchangeRate(source.exchange_rate, currency),
  }
}

export async function getPricePolicy(ref: string): Promise<PricePolicy> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/price-policy`,
  )
  return normalizePricePolicy(data)
}

export async function updatePricePolicy(
  ref: string,
  policy: PricePolicy,
): Promise<PricePolicy> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/price-policy`,
    { method: 'PUT', body: JSON.stringify(normalizePricePolicy(policy)) },
  )
  return normalizePricePolicy(data)
}

/** 把该包件所有已录入单价乘以系数并写回；无已录入单价时后端返回 409。 */

export async function adjustBoq(
  ref: string,
  packageId?: string | null,
): Promise<BoqPayload> {
  const value = (packageId || '').trim()
  const data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/boq/adjust`, {
    method: 'POST',
    body: JSON.stringify(value ? { package_id: value } : {}),
  })
  return normalizeBoqPayload(data)
}

export async function getQuotas(search = '', limit = 100): Promise<QuotaItem[]> {
  const params = new URLSearchParams()
  const query = search.trim()
  if (query) params.set('search', query)
  params.set('limit', String(limit))
  const data = await request<unknown>(`/admin/quotas?${params.toString()}`)
  return quotaItems(data)
    .map(normalizeQuotaItem)
    .filter((item) => item.id && item.name)
}

export async function createQuota(input: QuotaInput): Promise<QuotaItem> {
  const data = await request<unknown>('/admin/quotas', jsonBody(input))
  return normalizeQuotaItem(data)
}

export async function updateQuota(
  id: string,
  patch: Partial<QuotaInput>,
): Promise<QuotaItem> {
  const data = await request<unknown>(`/admin/quotas/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
  return normalizeQuotaItem(data)
}

export const deleteQuota = (id: string) =>
  request<{ deleted?: boolean; id?: string }>(
    `/admin/quotas/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  )

/** 用清单名称/规格试匹配定额库，返回按相似度降序的前 5 条。 */

export async function matchQuotas(name: string, spec = ''): Promise<QuotaMatch[]> {
  const params = new URLSearchParams()
  params.set('name', name.trim())
  if (spec.trim()) params.set('spec', spec.trim())
  const data = await request<unknown>(`/admin/quotas/match?${params.toString()}`)
  const items = asArray<unknown>(data, 'matches')
  const list = items.length ? items : quotaItems(data)
  return list
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const source = item as Record<string, unknown>
      const match: QuotaMatch = {
        id: asText(source.id),
        name: asText(source.name),
        spec: asText(source.spec),
        unit: asText(source.unit),
        unit_price: asNumber(source.unit_price, 0),
        score: asNumber(source.score, 0),
      }
      return match.id ? match : null
    })
    .filter((item): item is QuotaMatch => item !== null)
}
