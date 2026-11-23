import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminBillingTab } from './AdminBillingTab'
import {
  generateRedeemCodes,
  getAdminBilling,
  issueAdminInvoice,
  listAdminInvoices,
  rejectAdminInvoice,
  saveAdminBilling,
} from '../../api/billing'
import type { AdminBillingData, InvoiceRequest } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

const toast = vi.hoisted(() => ({
  notify: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}))

vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../api/billing', () => ({
  getAdminBilling: vi.fn(),
  saveAdminBilling: vi.fn(),
  generateRedeemCodes: vi.fn(),
  listAdminInvoices: vi.fn(),
  issueAdminInvoice: vi.fn(),
  rejectAdminInvoice: vi.fn(),
}))

const billingData: AdminBillingData = {
  settings: {
    signup_credits: 5, chapter_cost: 1,
    packages: [{ id: 'starter', name: '入门包', credits: 10, amount_cents: 990 }],
    vip_packages: [], vip_discount: 0.5, sub_account_limit: 5,
  },
  stats: { users: 2, external_users: 1, revenue_cents: 990, outstanding_credits: 10 },
  orders: [],
  codes: [],
}

const invoice: InvoiceRequest = {
  id: 'inv-1', user_id: 'u1', username: 'alice', title: '某公司', tax_no: 'TAX-1',
  email: 'a@example.com', amount_cents: 9900, status: 'requested', status_label: '待开具',
  created_at: '2026-11-23T08:00:00Z',
}

describe('AdminBillingTab 开票处置', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    vi.mocked(getAdminBilling).mockResolvedValue(billingData)
    vi.mocked(saveAdminBilling).mockResolvedValue({ settings: billingData.settings, stats: billingData.stats })
    vi.mocked(generateRedeemCodes).mockResolvedValue({ codes: [] })
    vi.mocked(listAdminInvoices).mockResolvedValue([invoice])
    vi.mocked(issueAdminInvoice).mockResolvedValue({ ...invoice, status: 'issued' })
    vi.mocked(rejectAdminInvoice).mockResolvedValue({ ...invoice, status: 'rejected' })
  })

  function renderTab() {
    return render(<MemoryRouter><AdminBillingTab /></MemoryRouter>)
  }

  it('列出待开具申请并上传发票文件完成开具', async () => {
    renderTab()
    expect(await screen.findByText('开票申请')).toBeTruthy()
    expect(screen.getByText('某公司')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '开具' }))
    fireEvent.change(screen.getByLabelText('发票号'), { target: { value: 'FP-2026-001' } })
    const file = new File(['%PDF-1.4'], 'invoice.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByLabelText('发票文件'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: '确认开具' }))
    await waitFor(() =>
      expect(issueAdminInvoice).toHaveBeenCalledWith('inv-1', 'FP-2026-001', file),
    )
    expect(toast.success).toHaveBeenCalledWith('发票已开具')
  })

  it('缺少必填项时不调用开具接口', async () => {
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: '开具' }))
    fireEvent.click(screen.getByRole('button', { name: '确认开具' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请填写发票号')
    expect(issueAdminInvoice).not.toHaveBeenCalled()
  })

  it('驳回需要填写原因', async () => {
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: '驳回' }))
    fireEvent.change(screen.getByLabelText('驳回原因'), { target: { value: '信息有误' } })
    fireEvent.click(screen.getByRole('button', { name: '确认驳回' }))
    await waitFor(() => expect(rejectAdminInvoice).toHaveBeenCalledWith('inv-1', '信息有误'))
  })
})
