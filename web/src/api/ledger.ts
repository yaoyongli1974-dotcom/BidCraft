import { request } from '../lib/http'
import type { Ledger } from '../lib/types'

// ---------------------------------------------------------------- ledger domain
export async function getLedger(ref: string): Promise<Ledger> {
  const data = await request<Partial<Ledger>>(`/projects/${encodeURIComponent(ref)}/ledger`)
  return {
    summary: data.summary,
    facts: Array.isArray(data.facts) ? data.facts : [],
    materials: Array.isArray(data.materials) ? data.materials : [],
    parameters: Array.isArray(data.parameters) ? data.parameters : [],
    scoring: Array.isArray(data.scoring) ? data.scoring : [],
    requirements: Array.isArray(data.requirements) ? data.requirements : [],
    risks: Array.isArray(data.risks) ? data.risks : [],
    template_sections: Array.isArray(data.template_sections) ? data.template_sections : [],
    project: data.project,
  }
}

export const updateFact = (ref: string, key: string, value: string, status: string) =>
  request<import('../lib/types').FactUpdateResult>(
    `/projects/${encodeURIComponent(ref)}/facts/${encodeURIComponent(key)}`,
    { method: 'PATCH', body: JSON.stringify({ value, status }) },
  )
