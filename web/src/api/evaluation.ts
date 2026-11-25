import { request, requestBlob } from '../lib/http'
import type {
  EvaluationSummary,
  EvaluationSummaryItem,
  QualityFinding,
  QualityReport,
  QualityRunInput,
} from '../lib/types'

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 后端真实件指标未测量时返回 null，绝不展示为 0。 */
function asMetric(value: unknown): number | string | null {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string') return value
  return null
}

function asNullableText(value: unknown): string | null {
  if (value === null || value === undefined) return null
  const text = String(value).trim()
  return text || null
}

function asNullableNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return null
}

function asNotes(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

/** 明细容器一律归一化成空对象/空数组，脏值不得让详情面板抛错。 */
function asDetailRows(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value
        .filter((item) => item && typeof item === 'object' && !Array.isArray(item))
        .map((item) => ({ ...(item as Record<string, unknown>) }))
    : []
}

function asDetailCoverage(value: unknown): Record<string, Record<string, unknown>> {
  const raw = asRecord(value)
  const result: Record<string, Record<string, unknown>> = {}
  Object.entries(raw).forEach(([key, entry]) => {
    result[key] = asRecord(entry)
  })
  return result
}

function asDetailSamples(value: unknown): Record<string, Array<Record<string, unknown>>> {
  const raw = asRecord(value)
  const result: Record<string, Array<Record<string, unknown>>> = {}
  Object.entries(raw).forEach(([key, rows]) => {
    result[key] = asDetailRows(rows)
  })
  return result
}

export function normalizeQualityReport(value: unknown): QualityReport | null {
  const raw = asRecord(value)
  if (!raw.id) return null
  const summary = asRecord(raw.summary)
  const commercialRaw = asRecord(summary.commercial)
  const commercial = Object.keys(commercialRaw).length
    ? {
        ok: Boolean(commercialRaw.ok),
        ratio: Number(commercialRaw.ratio || 0),
        missing: Array.isArray(commercialRaw.missing)
          ? commercialRaw.missing.map((item) => {
              const row = asRecord(item)
              return {
                section: String(row.section || ''),
                field: String(row.field || ''),
                label: String(row.label || ''),
              }
            })
          : [],
        checked: Number(commercialRaw.checked || 0),
        doc_id: String(commercialRaw.doc_id || ''),
        doc_status: String(commercialRaw.doc_status || ''),
        source: String(commercialRaw.source || ''),
      }
    : undefined
  const metricsRaw = asRecord(summary.metrics)
  const metrics = summary.metrics
    ? {
        manual_edit_rate: asMetric(metricsRaw.manual_edit_rate),
        issue_density: asMetric(metricsRaw.issue_density),
        boq_match_rate: asMetric(metricsRaw.boq_match_rate),
        notes: asNotes(metricsRaw.notes),
        coverage: asDetailCoverage(metricsRaw.coverage),
        samples: asDetailSamples(metricsRaw.samples),
        mismatches: asDetailRows(metricsRaw.mismatches),
      }
    : undefined
  return {
    id: String(raw.id),
    project_id: String(raw.project_id || ''),
    report_version: Number(raw.report_version || 0),
    overall_score: Number(raw.overall_score || 0),
    // 无报告/旧后端缺字段时保留 null，界面显示「—」，不伪造 C / warn。
    grade: asNullableText(raw.grade),
    verdict: asNullableText(raw.verdict) as QualityReport['verdict'],
    dimensions: Array.isArray(raw.dimensions) ? raw.dimensions : [],
    summary: {
      coverage: asRecord(summary.coverage),
      recommendations: Array.isArray(summary.recommendations)
        ? summary.recommendations.map(String)
        : [],
      note: String(summary.note || ''),
      commercial,
      metrics,
      tender_quality: summary.tender_quality
        ? (() => {
            const tender = asRecord(summary.tender_quality)
            return {
              status: String(tender.status || '未测量'),
              reason: tender.reason ? String(tender.reason) : undefined,
              notes: asNotes(tender.notes),
              clause_recall: asMetric(tender.clause_recall),
              value_parse_rate: asMetric(tender.value_parse_rate),
              verdict_agreement: asMetric(tender.verdict_agreement),
              files: Array.isArray(tender.files)
                ? tender.files.map((item) => {
                    const row = asRecord(item)
                    return {
                      path: String(row.path || ''),
                      clause_recall: asMetric(row.clause_recall),
                      value_parse_rate: asMetric(row.value_parse_rate),
                      notes: asNotes(row.notes),
                      coverage: asDetailCoverage(row.coverage),
                      samples: asDetailSamples(row.samples),
                      mismatches: asDetailRows(row.mismatches),
                    }
                  })
                : [],
              coverage: asDetailCoverage(tender.coverage),
              samples: asDetailSamples(tender.samples),
              mismatches: asDetailRows(tender.mismatches),
            }
          })()
        : undefined,
    },
    risk_summary: asRecord(raw.risk_summary),
    created_by: String(raw.created_by || ''),
    created_at: raw.created_at ? String(raw.created_at) : null,
    findings: Array.isArray(raw.findings)
      ? raw.findings.map(normalizeQualityFinding).filter(Boolean) as QualityFinding[]
      : [],
  }
}

export function normalizeQualityFinding(value: unknown): QualityFinding {
  const raw = asRecord(value)
  const evidence = asRecord(raw.evidence)
  const stableKey = asNullableText(raw.stable_key ?? evidence.stable_key ?? evidence.fingerprint)
  const sourceRowId = asNullableText(
    raw.source_row_id ??
      evidence.source_row_id ??
      evidence.source_row ??
      evidence.row_id ??
      evidence.issue_id ??
      evidence.audit_id ??
      evidence.run_id ??
      evidence.task_id,
  )
  return {
    id: String(raw.id || ''),
    report_id: String(raw.report_id || ''),
    category: String(raw.category || ''),
    level: String(raw.level || 'major'),
    node_id: raw.node_id ? String(raw.node_id) : null,
    scoring_item_id: raw.scoring_item_id ? String(raw.scoring_item_id) : null,
    requirement_id: raw.requirement_id ? String(raw.requirement_id) : null,
    title: String(raw.title || ''),
    description: String(raw.description || ''),
    evidence,
    recommendation: String(raw.recommendation || ''),
    status: String(raw.status || 'open'),
    stable_key: stableKey,
    source_row_id: sourceRowId,
    resolution_note: String(
      raw.resolution_note ?? evidence.resolution_note ?? '',
    ),
    resolved_by: String(raw.resolved_by ?? evidence.resolved_by ?? ''),
    resolved_at:
      typeof raw.resolved_at === 'string' && raw.resolved_at.trim()
        ? raw.resolved_at
        : typeof evidence.resolved_at === 'string' && evidence.resolved_at.trim()
          ? evidence.resolved_at
          : null,
  }
}

export function normalizeEvaluationSummaryItem(value: unknown): EvaluationSummaryItem | null {
  const raw = asRecord(value)
  const ref = asNullableText(raw.ref ?? raw.project_ref ?? raw.project_id) || ''
  if (!ref) return null
  const reportId = asNullableText(raw.report_id)
  // 没有落库报告时一律 measured=false，分数/等级/结论不得沿用旧值。
  const measured =
    (raw.measured === undefined ? true : raw.measured === true) && reportId !== null
  return {
    project_id: asNullableText(raw.project_id) || ref,
    ref,
    name: asNullableText(raw.name ?? raw.project_name) || ref,
    measured,
    report_id: reportId,
    report_version:
      measured && asNullableNumber(raw.report_version) !== null
        ? Math.round(asNullableNumber(raw.report_version) as number)
        : null,
    overall_score: measured ? asNullableNumber(raw.overall_score ?? raw.score) : null,
    grade: measured ? asNullableText(raw.grade) : null,
    verdict: measured
      ? (asNullableText(raw.verdict) as EvaluationSummaryItem['verdict'])
      : null,
    created_at: asNullableText(raw.created_at),
  }
}

export function normalizeEvaluationSummary(value: unknown): EvaluationSummary {
  const raw = asRecord(value)
  const list = Array.isArray(raw.items)
    ? raw.items
    : Array.isArray(raw.projects)
      ? raw.projects
      : []
  return {
    items: list
      .map(normalizeEvaluationSummaryItem)
      .filter((item): item is EvaluationSummaryItem => item !== null),
  }
}

function projectBase(ref: string): string {
  return `/projects/${encodeURIComponent(ref)}`
}

export async function runEvaluation(ref: string, input: QualityRunInput = {}): Promise<QualityReport> {
  const data = await request<unknown>(`${projectBase(ref)}/evaluation/run`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
  const report = normalizeQualityReport(data)
  if (!report) throw new Error('质量报告响应无效')
  return report
}

/** 提交后台质量评估任务；用 watchJob(job_id) 跟踪进度与终态。 */
export async function runEvaluationAsync(
  ref: string,
  input: QualityRunInput = {},
): Promise<{ job_id: string }> {
  const data = await request<{ job_id?: unknown }>(
    `${projectBase(ref)}/evaluation/run-async`,
    { method: 'POST', body: JSON.stringify(input) },
  )
  const jobId = data && data.job_id ? String(data.job_id) : ''
  if (!jobId) throw new Error('后台评估任务响应无效')
  return { job_id: jobId }
}

export async function getLatestEvaluation(ref: string): Promise<QualityReport | null> {
  const data = await request<{ report?: unknown }>(`${projectBase(ref)}/evaluation`)
  return data && data.report ? normalizeQualityReport(data.report) : null
}

export async function listEvaluationReports(ref: string, limit = 20): Promise<QualityReport[]> {
  const data = await request<unknown[]>(`${projectBase(ref)}/evaluation/reports?limit=${limit}`)
  return Array.isArray(data)
    ? data.map(normalizeQualityReport).filter(Boolean) as QualityReport[]
    : []
}

/** 按 ID 读取指定报告详情：后台任务完成后必须用 job.result.report_id 对齐，不能读最新报告。 */
export async function getEvaluationReportDetail(ref: string, reportId: string): Promise<QualityReport> {
  const data = await request<unknown>(
    `${projectBase(ref)}/evaluation/reports/${encodeURIComponent(reportId)}`,
  )
  const report = normalizeQualityReport(data)
  if (!report) throw new Error('质量报告详情响应无效')
  return report
}

/** 下载指定报告的 Markdown 附件；文件名以 Content-Disposition 为准。 */
export async function exportEvaluationReport(
  ref: string,
  reportId: string,
): Promise<{ blob: Blob; filename: string }> {
  return requestBlob(
    `${projectBase(ref)}/evaluation/reports/${encodeURIComponent(reportId)}/export`,
    undefined,
    '质量报告.md',
  )
}

/** 跨项目评估概览；refs 省略时由后端返回当前用户可见的全部项目。 */
export async function getEvaluationSummary(ref: string, refs?: string[]): Promise<EvaluationSummary> {
  const params = new URLSearchParams()
  if (refs && refs.length > 0) params.set('refs', refs.join(','))
  const query = params.toString()
  const data = await request<unknown>(
    `${projectBase(ref)}/evaluation/summary${query ? `?${query}` : ''}`,
  )
  return normalizeEvaluationSummary(data)
}

export async function getEvaluationFindings(ref: string, reportId: string): Promise<QualityFinding[]> {
  const data = await request<unknown[]>(
    `${projectBase(ref)}/evaluation/reports/${encodeURIComponent(reportId)}/findings`,
  )
  return Array.isArray(data) ? data.map(normalizeQualityFinding) : []
}

export async function updateQualityFinding(
  ref: string,
  findingId: string,
  input: { status: string; note?: string },
): Promise<QualityFinding> {
  const data = await request<unknown>(
    `${projectBase(ref)}/evaluation/findings/${encodeURIComponent(findingId)}`,
    { method: 'PATCH', body: JSON.stringify(input) },
  )
  return normalizeQualityFinding(data)
}
