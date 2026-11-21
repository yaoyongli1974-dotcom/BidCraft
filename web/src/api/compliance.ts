import { ApiError, asArray, jsonBody, request } from '../lib/http'
import type { AdminRulePackage, BulkIssueStatusResult, CheckResult, Issue, IssueStatus, ModelConfig, ModelInput, RequirementResponse, RequirementResponseStatus, RiskGate, RiskOverview, RulePackageValidation, TestResult, TextQaSettings, TypoRule } from '../lib/types'
import { asBool, asNumber, asText, normalizeRequirement, normalizeRiskCategory, normalizeRiskTier, normalizeRulePackage, normalizeTextQa, normalizeValidation } from './shared'

// ---------------------------------------------------------------- compliance domain
export async function getRulePackages(includeHidden = false): Promise<AdminRulePackage[]> {
  const query = includeHidden ? '?include_hidden=true' : ''
  const data = await request<unknown>(`/admin/rule-packages${query}`)
  const items = asArray<unknown>(data, 'packages')
  const list = items.length ? items : asArray<unknown>(data, 'rule_packages')
  return list.map(normalizeRulePackage)
}

export async function getRulePackage(name: string): Promise<AdminRulePackage> {
  const data = await request<unknown>(`/admin/rule-packages/${encodeURIComponent(name)}`)
  return normalizeRulePackage(data)
}

export async function validateRulePackage(input: {
  name?: string
  document: string
}): Promise<RulePackageValidation> {
  const data = await request<unknown>(
    '/admin/rule-packages/validate',
    jsonBody({ ...(input.name ? { name: input.name } : {}), document: input.document }),
  )
  return normalizeValidation(data)
}

export async function createRulePackage(document: string): Promise<AdminRulePackage> {
  const data = await request<unknown>('/admin/rule-packages', jsonBody({ document }))
  return normalizeRulePackage(data)
}

export async function updateRulePackage(
  name: string,
  document: string,
): Promise<AdminRulePackage> {
  const data = await request<unknown>(
    `/admin/rule-packages/${encodeURIComponent(name)}`,
    { method: 'PUT', body: JSON.stringify({ document }) },
  )
  return normalizeRulePackage(data)
}

export const deleteRulePackage = (name: string) =>
  request<{ name?: string; hidden?: boolean }>(
    `/admin/rule-packages/${encodeURIComponent(name)}`,
    { method: 'DELETE' },
  )

export async function restoreRulePackage(
  name: string,
  resetOverride = false,
): Promise<AdminRulePackage> {
  const query = resetOverride ? '?reset_override=true' : ''
  const data = await request<unknown>(
    `/admin/rule-packages/${encodeURIComponent(name)}/restore${query}`,
    { method: 'POST' },
  )
  return normalizeRulePackage(data)
}

export async function getModels(): Promise<ModelConfig[]> {
  const data = await request<ModelConfig[] | { models?: ModelConfig[] }>('/admin/models')
  return asArray<ModelConfig>(data, 'models')
}

export const createModel = (payload: ModelInput) =>
  request<ModelConfig>('/admin/models', jsonBody(payload))

export const updateModel = (id: string, payload: Partial<ModelInput>) =>
  request<ModelConfig>(`/admin/models/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })

export const deleteModel = (id: string) =>
  request<{ deleted: boolean; id: string }>(`/admin/models/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

export const testModel = (id: string) =>
  request<TestResult>(`/admin/models/${encodeURIComponent(id)}/test`, { method: 'POST' })

export const runCheck = (ref: string) =>
  request<CheckResult>(`/projects/${encodeURIComponent(ref)}/check`, { method: 'POST' })

export async function getIssues(ref: string): Promise<Issue[]> {
  const data = await request<Issue[] | { issues?: Issue[] }>(
    `/projects/${encodeURIComponent(ref)}/issues`,
  )
  return asArray<Issue>(data, 'issues')
}

export async function getRiskOverview(ref: string): Promise<RiskOverview> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/risk-overview`)
  const raw = data && typeof data === 'object' && !Array.isArray(data)
    ? (data as Record<string, unknown>)
    : {}
  const gateRaw =
    raw.gate && typeof raw.gate === 'object' && !Array.isArray(raw.gate)
      ? (raw.gate as Record<string, unknown>)
      : {}
  const gate: RiskGate = {
    blocked: asBool(gateRaw.blocked),
    blocking_open: Math.max(0, Math.round(asNumber(gateRaw.blocking_open, 0))),
    reason: asText(gateRaw.reason),
  }
  return {
    project_id: asText(raw.project_id),
    verdict: asText(raw.verdict, 'unknown') || 'unknown',
    checked_at: typeof raw.checked_at === 'string' && raw.checked_at.trim() ? raw.checked_at : null,
    gate,
    tiers: asArray<unknown>(raw, 'tiers').map(normalizeRiskTier),
    by_category: asArray<unknown>(raw, 'by_category').map(normalizeRiskCategory),
    items: asArray<Issue>(raw, 'items'),
  }
}

/** 更新单条问题的处置状态。忽略（ignored）必须带原因，服务端会校验。 */

export async function updateIssueStatus(
  ref: string,
  issueId: string,
  status: IssueStatus,
  note?: string,
): Promise<Issue> {
  const payload: { status: IssueStatus; note?: string } = { status }
  const trimmed = (note || '').trim()
  if (trimmed) payload.note = trimmed
  const data = await request<Issue | { issue?: Issue }>(
    `/projects/${encodeURIComponent(ref)}/issues/${encodeURIComponent(issueId)}`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  )
  if (data && typeof data === 'object' && 'issue' in data && data.issue) return data.issue
  return data as Issue
}

export async function getRequirements(ref: string): Promise<RequirementResponse[]> {
  try {
    const data = await request<unknown>(`/projects/${encodeURIComponent(ref)}/requirements`)
    const list = Array.isArray(data)
      ? data
      : data && typeof data === 'object' && Array.isArray((data as { items?: unknown }).items)
        ? ((data as { items: unknown[] }).items)
        : []
    return list
      .map(normalizeRequirement)
      .filter((item): item is RequirementResponse => item !== null)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return []
    throw error
  }
}

/** 人工确认实质性条款的响应状态（na 必须带原因，服务端会再校验一次）。 */

export async function updateRequirementResponse(
  ref: string,
  requirementId: string,
  status: RequirementResponseStatus,
  note?: string,
): Promise<RequirementResponse> {
  const payload: { response_status: RequirementResponseStatus; note?: string } = {
    response_status: status,
  }
  const trimmed = (note || '').trim()
  if (trimmed) payload.note = trimmed
  const data = await request<RequirementResponse | { requirement?: RequirementResponse }>(
    `/projects/${encodeURIComponent(ref)}/requirements/${encodeURIComponent(requirementId)}`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  )
  const normalized =
    data && typeof data === 'object' && 'requirement' in data && data.requirement
      ? normalizeRequirement(data.requirement)
      : normalizeRequirement(data)
  if (!normalized) throw new Error('服务端返回的条款数据不完整')
  return normalized
}

/** 批量更新处置状态；返回被更新的问题列表。 */

export async function bulkIssueStatus(
  ref: string,
  ids: string[],
  status: IssueStatus,
  note?: string,
): Promise<BulkIssueStatusResult> {
  const payload: { ids: string[]; status: IssueStatus; note?: string } = { ids, status }
  const trimmed = (note || '').trim()
  if (trimmed) payload.note = trimmed
  const data = await request<Partial<BulkIssueStatusResult>>(
    `/projects/${encodeURIComponent(ref)}/issues/bulk-status`,
    jsonBody(payload),
  )
  return {
    updated: Math.max(0, Math.round(asNumber(data?.updated, ids.length))),
    items: Array.isArray(data?.items) ? data.items : [],
  }
}

export const TYPO_RULE_MAX = { wrong: 20, right: 20, note: 60 } as const

export const TYPO_RULE_LIMIT = 500

/**
 * 归一化自定义错别字规则：去掉空错词、按 wrong 去重（保留首次出现的顺序与内容）、
 * 按后端列宽截断 wrong/right/note，并收敛到 500 条上限。既用于解析服务端返回，
 * 也用于提交前的清洗，保证前后端校验口径一致。
 */

export function normalizeTypoRules(value: unknown): TypoRule[] {
  const items = asArray<unknown>(value, 'typos')
  const seen = new Set<string>()
  const rules: TypoRule[] = []
  for (const item of items) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const raw = item as Record<string, unknown>
    const wrong = asText(raw.wrong).trim().slice(0, TYPO_RULE_MAX.wrong)
    if (!wrong || seen.has(wrong)) continue
    seen.add(wrong)
    rules.push({
      wrong,
      right: asText(raw.right).trim().slice(0, TYPO_RULE_MAX.right),
      note: asText(raw.note).trim().slice(0, TYPO_RULE_MAX.note),
    })
    if (rules.length >= TYPO_RULE_LIMIT) break
  }
  return rules
}

/** 兼容平铺数组与 `{ typos, count, builtin_count }` 两种返回形状。 */

export async function getTextQa(): Promise<TextQaSettings> {
  const data = await request<unknown>('/admin/textqa')
  return normalizeTextQa(data)
}

/** 整体覆盖保存自定义规则；非法载荷后端回 400（中文 detail），经 ApiError 透出。 */

export async function saveTextQa(typos: TypoRule[]): Promise<TextQaSettings> {
  const data = await request<unknown>('/admin/textqa', {
    method: 'PUT',
    body: JSON.stringify({ typos: normalizeTypoRules(typos) }),
  })
  return normalizeTextQa(data)
}
