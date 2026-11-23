import { jsonBody, request, uploadForm } from '../lib/http'
import type { AdminBillingData, AdminBillingSettings, BillingData, CreditAdjustResult, InvoiceRequest, MockPayResult, RechargeOrder, RedeemResult, SubAccount, SubAccountInput, User } from '../lib/types'
import { wechatLoginUrl } from './auth'

// ---------------------------------------------------------------- billing domain
export const getBillingSummary = () => request<BillingData>('/billing/summary')

export const createBillingOrder = (packageId: string) =>
  request<RechargeOrder>('/billing/orders', jsonBody({ package_id: packageId }))

export const getBillingOrder = (orderId: string) =>
  request<RechargeOrder>(`/billing/orders/${encodeURIComponent(orderId)}`)

export const mockPayOrder = (orderId: string) =>
  request<MockPayResult>(`/billing/orders/${encodeURIComponent(orderId)}/mock-pay`, {
    method: 'POST',
  })

export const redeemCreditCode = (code: string) =>
  request<RedeemResult>('/billing/redeem', jsonBody({ code }))

export const billingOrderQrUrl = (orderId: string) =>
  `/billing/orders/${encodeURIComponent(orderId)}/qr`

export const getAdminBilling = () => request<AdminBillingData>('/admin/billing')

export const saveAdminBilling = (payload: AdminBillingSettings) =>
  request<{ settings: AdminBillingSettings; stats: AdminBillingData['stats'] }>(
    '/admin/billing',
    { method: 'PUT', body: JSON.stringify(payload) },
  )

export const generateRedeemCodes = (payload: {
  count: number
  credits: number
  amount_cents: number
  days: number
}) => request<{ codes: string[] }>('/admin/redeem-codes', jsonBody(payload))

export const adjustUserCredits = (userId: string, amount: number, note = '') =>
  request<CreditAdjustResult>(`/admin/users/${encodeURIComponent(userId)}/credits`, {
    method: 'POST',
    body: JSON.stringify({ amount, note }),
  })

export const grantUserVip = (userId: string, days: number, note = '') =>
  request<{ user: User }>(`/admin/users/${encodeURIComponent(userId)}/vip`, {
    method: 'POST',
    body: JSON.stringify({ days, note }),
  })

export const createVipOrder = (packageId: string) =>
  request<RechargeOrder>('/billing/vip/orders', jsonBody({ package_id: packageId }))

// ---------------------------------------------------------------- admin invoices

/** 管理员查看开票申请；后端支持 status=requested/issued/rejected。 */
export const listAdminInvoices = (status = '') =>
  request<InvoiceRequest[]>(`/admin/invoices${status ? `?status=${encodeURIComponent(status)}` : ''}`)

/** 开具发票：上传 PDF/图片文件并填写发票号（同一路径，multipart）。 */
export const issueAdminInvoice = (
  invoiceId: string,
  invoiceNo: string,
  file: File,
) => {
  const form = new FormData()
  form.append('invoice_no', invoiceNo)
  form.append('file', file)
  return uploadForm<InvoiceRequest>(
    `/admin/invoices/${encodeURIComponent(invoiceId)}/issue`,
    form,
  )
}

/** 驳回开票申请，reason 会展示给用户。 */
export const rejectAdminInvoice = (invoiceId: string, reason: string) =>
  request<InvoiceRequest>(
    `/admin/invoices/${encodeURIComponent(invoiceId)}/reject`,
    jsonBody({ reason }),
  )

export const getSubAccounts = () => request<SubAccount[]>('/account/sub-accounts')

export const createSubAccount = (input: SubAccountInput) =>
  request<SubAccount>('/account/sub-accounts', jsonBody(input))

export const updateSubAccount = (
  subId: string,
  patch: { display_name?: string; password?: string; active?: boolean },
) =>
  request<SubAccount>(`/account/sub-accounts/${encodeURIComponent(subId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })

export const deleteSubAccount = (subId: string) =>
  request<{ deleted: boolean; id: string }>(`/account/sub-accounts/${encodeURIComponent(subId)}`, {
    method: 'DELETE',
  })

export function wechatFullPageLogin(health: { wechat_mock?: boolean } | null, next = '/') {
  window.location.assign(wechatLoginUrl(health, next))
}
