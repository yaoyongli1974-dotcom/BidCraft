import { request } from '../lib/http'
import type {
  QualityReport,
  QualityFinding,
  QualityRunInput,
} from '../lib/types'

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

export function normalizeQualityReport(value: unknown): QualityReport | null {
  const raw = asRecord(value)
  if (!raw.id) return null
  const summary = asRecord(raw.summary)
  return {
    id: String(raw.id),
    project_id: String(raw.project_id || ''),
    report_version: Number(raw.report_version || 0),
    overall_score: Number(raw.overall_score || 0),
    grade: String(raw.grade || 'C'),
    verdict: String(raw.verdict || 'warn') as QualityReport['verdict'],
    dimensions: Array.isArray(raw.dimensions) ? raw.dimensions : [],
    summary: {
      coverage: asRecord(summary.coverage),
      recommendations: Array.isArray(summary.recommendations)
        ? summary.recommendations.map(String)
        : [],
      note: String(summary.note || ''),
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
    evidence: asRecord(raw.evidence),
    recommendation: String(raw.recommendation || ''),
    status: String(raw.status || 'open'),
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

export async function getEvaluationReport(ref: string, reportId: string): Promise<QualityReport | null> {
  const data = await request<unknown>(`${projectBase(ref)}/evaluation/reports/${encodeURIComponent(reportId)}`)
  return normalizeQualityReport(data)
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
