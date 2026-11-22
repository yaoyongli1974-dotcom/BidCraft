import { asArray, jsonBody, request } from '../lib/http'
import type {
  Enterprise,
  EnterpriseDocument,
  EnterpriseDocumentInput,
  EnterpriseMatch,
  EnterpriseMember,
  EnterpriseMemberInput,
  EnterprisePackage,
  EnterprisePackageItem,
} from '../lib/types'
import { asNumber, asText, isoOrNull } from './shared'

// ---------------------------------------------------------------- enterprise domain

function tagsOf(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === 'string' ? item.trim() : ''))
      .filter(Boolean)
  }
  const text = asText(value).trim()
  if (!text) return []
  return text
    .split(/[,，、\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function normalizeEnterprise(raw: unknown): Enterprise {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    id: asText(source.id),
    name: asText(source.name),
    owner_id: source.owner_id ? asText(source.owner_id) : undefined,
    role: source.role ? asText(source.role) : undefined,
    member_count: source.member_count !== undefined ? asNumber(source.member_count) : undefined,
    document_count:
      source.document_count !== undefined ? asNumber(source.document_count) : undefined,
    created_at: isoOrNull(source.created_at),
  }
}

export function normalizeEnterpriseMember(raw: unknown): EnterpriseMember {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    enterprise_id: source.enterprise_id ? asText(source.enterprise_id) : undefined,
    user_id: asText(source.user_id ?? source.id),
    username: asText(source.username),
    display_name: source.display_name ? asText(source.display_name) : undefined,
    role: source.role ? asText(source.role) : undefined,
    created_at: isoOrNull(source.created_at),
  }
}

export function normalizeEnterpriseDocument(raw: unknown): EnterpriseDocument {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    id: asText(source.id),
    enterprise_id: source.enterprise_id ? asText(source.enterprise_id) : undefined,
    source: asText(source.source),
    content: asText(source.content),
    kind: asText(source.kind) || 'other',
    tags: tagsOf(source.tags),
    valid_to: isoOrNull(source.valid_to),
    created_at: isoOrNull(source.created_at),
    created_by: asText(source.created_by),
  }
}

export function normalizeEnterpriseMatch(raw: unknown): EnterpriseMatch {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    id: source.id ? asText(source.id) : undefined,
    document_id: source.document_id ? asText(source.document_id) : undefined,
    source: asText(source.source),
    kind: source.kind ? asText(source.kind) : undefined,
    excerpt: asText(source.excerpt ?? source.content),
    score: source.score !== undefined && source.score !== null ? asNumber(source.score) : null,
    tags: tagsOf(source.tags),
    valid_to: isoOrNull(source.valid_to),
    reason: asText(source.reason),
  }
}

function normalizePackageItem(raw: unknown): EnterprisePackageItem {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    id: source.id ? asText(source.id) : undefined,
    label: asText(source.label),
    title: asText(source.title ?? source.name),
    source: asText(source.source),
    kind: asText(source.kind),
    status: asText(source.status),
    note: asText(source.note),
    count: source.count !== undefined ? asNumber(source.count) : undefined,
  }
}

export function normalizeEnterprisePackage(raw: unknown): EnterprisePackage {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return {
    id: source.id ? asText(source.id) : undefined,
    name: source.name ? asText(source.name) : undefined,
    status: source.status ? asText(source.status) : undefined,
    items: asArray<unknown>(source, 'items').map(normalizePackageItem),
    documents: asArray<unknown>(source, 'documents').map(normalizeEnterpriseDocument),
    generated_at: isoOrNull(source.generated_at),
  }
}

export async function getEnterprises(): Promise<Enterprise[]> {
  const data = await request<unknown>('/enterprise')
  return asArray<unknown>(data, 'enterprises').map(normalizeEnterprise)
}

export async function createEnterprise(name: string): Promise<Enterprise> {
  const data = await request<unknown>(
    '/enterprise',
    jsonBody({ name: name.trim() }),
  )
  return normalizeEnterprise(data)
}

export async function getEnterpriseMembers(enterpriseId: string): Promise<EnterpriseMember[]> {
  const data = await request<unknown>(
    `/enterprise/${encodeURIComponent(enterpriseId)}/members`,
  )
  return asArray<unknown>(data, 'members').map(normalizeEnterpriseMember)
}

export async function addEnterpriseMember(
  enterpriseId: string,
  input: EnterpriseMemberInput,
): Promise<EnterpriseMember> {
  const data = await request<unknown>(
    `/enterprise/${encodeURIComponent(enterpriseId)}/members`,
    jsonBody({ username: input.username.trim(), ...(input.role ? { role: input.role } : {}) }),
  )
  return normalizeEnterpriseMember(data)
}

export async function removeEnterpriseMember(
  enterpriseId: string,
  userId: string,
): Promise<{ deleted: boolean }> {
  const data = await request<{ deleted?: boolean }>(
    `/enterprise/${encodeURIComponent(enterpriseId)}/members/${encodeURIComponent(userId)}`,
    { method: 'DELETE' },
  )
  return { deleted: data?.deleted !== false }
}

export async function getEnterpriseDocuments(
  enterpriseId: string,
): Promise<EnterpriseDocument[]> {
  const data = await request<unknown>(
    `/enterprise/${encodeURIComponent(enterpriseId)}/documents`,
  )
  return asArray<unknown>(data, 'documents').map(normalizeEnterpriseDocument)
}

export async function addEnterpriseDocument(
  enterpriseId: string,
  input: EnterpriseDocumentInput,
): Promise<EnterpriseDocument> {
  const payload = {
    source: input.source.trim(),
    content: input.content.trim(),
    ...(input.kind ? { kind: input.kind } : {}),
    ...(input.tags && input.tags.length > 0 ? { tags: input.tags } : {}),
    ...(input.valid_to ? { valid_to: input.valid_to } : {}),
  }
  const data = await request<unknown>(
    `/enterprise/${encodeURIComponent(enterpriseId)}/documents`,
    jsonBody(payload),
  )
  return normalizeEnterpriseDocument(data)
}

export async function matchEnterpriseAssets(
  ref: string,
  input: { requirement: string; kind?: string },
): Promise<EnterpriseMatch[]> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/enterprise/match`,
    jsonBody({
      requirement: input.requirement.trim(),
      ...(input.kind ? { kind: input.kind } : {}),
    }),
  )
  return asArray<unknown>(data, 'matches').map(normalizeEnterpriseMatch)
}

export async function getEnterprisePackage(ref: string): Promise<EnterprisePackage> {
  const data = await request<unknown>(
    `/projects/${encodeURIComponent(ref)}/enterprise/package`,
  )
  return normalizeEnterprisePackage(data)
}
