import { ApiError, asArray, jsonBody, request } from '../lib/http'
import type { User, UserInput } from '../lib/types'
import { normalizeInvoice } from './shared'

// ---------------------------------------------------------------- auth domain
export const login = (username: string, password: string) =>
  request<{ user: User }>('/auth/login', jsonBody({ username, password }))

export const logout = () =>
  request<{ logged_out: boolean }>('/auth/logout', { method: 'POST' })

export const getMe = () => request<{ user: User }>('/auth/me')

export const register = (
  username: string,
  password: string,
  displayName = '',
  acceptTerms = false,
) =>
  request<{ user: User }>(
    '/auth/register',
    jsonBody({
      username,
      password,
      display_name: displayName,
      accept_terms: acceptTerms,
    }),
  )

export interface InvoiceRequest {
  id: string
  user_id: string
  username: string
  title: string
  tax_no: string
  email: string
  amount_cents: number
  note: string
  status: 'requested' | 'issued' | 'rejected'
  status_label: string
  invoice_no: string
  reject_reason: string
  has_file: boolean
  issued_at: string | null
  created_at: string | null
  updated_at: string | null
}

export const getInvoices = async (): Promise<InvoiceRequest[]> => {
  const data = await request<unknown>('/billing/invoices')
  const items = Array.isArray(data)
    ? data
    : Array.isArray((data as { items?: unknown[] })?.items)
      ? (data as { items: unknown[] }).items
      : []
  return items.map(normalizeInvoice).filter((item): item is InvoiceRequest => item !== null)
}

export const createInvoice = async (input: {
  title: string
  amount_cents: number
  tax_no?: string
  email?: string
  note?: string
}): Promise<InvoiceRequest> => {
  const data = await request<unknown>('/billing/invoices', jsonBody(input))
  const normalized = normalizeInvoice(data)
  if (!normalized) throw new ApiError(500, '开票申请返回格式异常')
  return normalized
}

export const invoiceFileUrl = (invoiceId: string): string =>
  `/billing/invoices/${encodeURIComponent(invoiceId)}/file`

export function wechatLoginUrl(health: { wechat_mock?: boolean } | null, next = '/'): string {
  const path = health?.wechat_mock ? '/auth/wechat/mock' : '/auth/wechat/start'
  return `${path}?next=${encodeURIComponent(next)}`
}

export const changePassword = (currentPassword: string, newPassword: string) =>
  request<{ changed: boolean; relogin: boolean }>(
    '/auth/password',
    jsonBody({ current_password: currentPassword, new_password: newPassword }),
  )

export async function getUsers(): Promise<User[]> {
  const data = await request<User[] | { users?: User[] }>('/admin/users')
  return asArray<User>(data, 'users')
}

export const createUser = (payload: UserInput) =>
  request<User>('/admin/users', jsonBody(payload))

export const updateUser = (id: string, payload: Partial<UserInput>) =>
  request<User>(`/admin/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })

export const deleteUser = (id: string) =>
  request<{ deleted: boolean; id: string }>(`/admin/users/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
