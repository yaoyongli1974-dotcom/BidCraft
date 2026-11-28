import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BillingPage } from './BillingPage'
import type { BillingData, RechargeOrder, User } from '../lib/types'
import { clearQueryCache } from '../lib/queryCache'

const mocks = vi.hoisted(() => ({
  getBillingSummary: vi.fn(),
  createBillingOrder: vi.fn(),
  createVipOrder: vi.fn(),
  getBillingOrder: vi.fn(),
  mockPayOrder: vi.fn(),
  redeemCreditCode: vi.fn(),
  getSubAccounts: vi.fn(),
  createSubAccount: vi.fn(),
  updateSubAccount: vi.fn(),
  deleteSubAccount: vi.fn(),
  toast: {
    notify: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(),
    warning: vi.fn(), dismiss: vi.fn(),
  },
  auth: {
    user: null as User | null,
    canWrite: true,
    updateUser: vi.fn(),
    syncUser: vi.fn(async () => {}),
  },
}))

vi.mock('../api/billing', () => ({
  getBillingSummary: mocks.getBillingSummary,
  createBillingOrder: mocks.createBillingOrder,
  createVipOrder: mocks.createVipOrder,
  getBillingOrder: mocks.getBillingOrder,
  mockPayOrder: mocks.mockPayOrder,
  redeemCreditCode: mocks.redeemCreditCode,
  getSubAccounts: mocks.getSubAccounts,
  createSubAccount: mocks.createSubAccount,
  updateSubAccount: mocks.updateSubAccount,
  deleteSubAccount: mocks.deleteSubAccount,
  billingOrderQrUrl: (id: string) => `/billing/orders/${id}/qr`,
}))
vi.mock('../context/useToast', () => ({ useToast: () => mocks.toast }))
vi.mock('../context/useAuth', () => ({ useAuth: () => mocks.auth }))
vi.mock('../context/useConfirm', () => ({ useConfirm: () => vi.fn() }))

const user: User = {
  id: 'u1', username: 'alice', display_name: 'Alice', role: 'manager',
  role_label: '投标经理', active: true, credits: 100,
}

function order(status: RechargeOrder['status']): RechargeOrder {
  return {
    id: 'o1', out_trade_no: 'BC-2026-0001', amount_cents: 990, credits: 10,
    status, provider: 'wechat', package_id: 'starter', created_at: '2026-11-28T00:00:00Z',
  }
}

function billing(overrides: Partial<BillingData> = {}): BillingData {
  return {
    user,
    summary: {
      balance: 100, chapter_cost: 1, signup_credits: 5, billing_exempt: false,
      packages: [{ id: 'starter', name: '入门包', credits: 10, amount_cents: 990 }],
      vip_packages: [], is_vip: false, sub_account_limit: 5,
    },
    transactions: [],
    orders: [order('pending')],
    wechat_login: false,
    wechat_mock: false,
    wechat_pay: true,
    wechat_pay_mock: false,
    ...overrides,
  }
}

function renderPage() {
  return render(<MemoryRouter><BillingPage /></MemoryRouter>)
}

async function loadPage(data: BillingData = billing()) {
  mocks.getBillingSummary.mockResolvedValue(data)
  renderPage()
  expect(await screen.findByText('选择充值套餐')).toBeTruthy()
}

describe('BillingPage 终态与重复提交', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    mocks.auth.user = user
    mocks.auth.canWrite = true
    mocks.getSubAccounts.mockResolvedValue([])
    mocks.getBillingOrder.mockResolvedValue(order('pending'))
    mocks.createBillingOrder.mockResolvedValue(order('pending'))
    mocks.createVipOrder.mockResolvedValue(order('pending'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  // 订单状态契约来自后端 /billing/orders：paid/closed/expired/failed 都不是 pending。
  it.each([
    ['paid', '充值已到账'],
    ['closed', '订单已关闭'],
    ['expired', '已过期'],
    ['failed', '支付失败'],
  ])('订单进入 %s 后停止轮询并停留在终态', async (status, label) => {
    mocks.getBillingOrder.mockResolvedValue(order(status))
    await loadPage()
    vi.useFakeTimers()

    fireEvent.click(screen.getByRole('button', { name: /继续支付/ }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(mocks.getBillingOrder).toHaveBeenCalledTimes(1)
    expect(screen.getAllByText(label).length).toBeGreaterThan(0)

    // 与轮询间隔（2s）相比足够长：若终态未停轮询，这里必然再次调用接口。
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(mocks.getBillingOrder).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('正在等待支付结果…')).toBeNull()
  })

  it('连续点击充值只创建一个订单', async () => {
    await loadPage()
    const never = new Promise<RechargeOrder>(() => {})
    mocks.createBillingOrder.mockReturnValue(never)

    const button = screen.getByRole('button', { name: '充值' })
    // 同一事件循环内两次点击：React state（busyPackage）还没更新，
    // 只有同步的 ref 守卫能阻止第二次 createBillingOrder。
    act(() => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(mocks.createBillingOrder).toHaveBeenCalledTimes(1)
  })

  it('连续点击 VIP 开通只创建一个订单', async () => {
    vi.mocked(mocks.getBillingSummary).mockResolvedValue(billing({
      summary: {
        ...billing().summary,
        vip_packages: [{ id: 'vip30', name: '月度 VIP', days: 30, amount_cents: 9900 }],
      },
    }))
    renderPage()
    expect(await screen.findByText('选择充值套餐')).toBeTruthy()
    const never = new Promise<RechargeOrder>(() => {})
    mocks.createVipOrder.mockReturnValue(never)

    const button = screen.getByRole('button', { name: '开通' })
    act(() => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(mocks.createVipOrder).toHaveBeenCalledTimes(1)
  })

  it('模拟支付连续点击只提交一次', async () => {
    await loadPage(billing({ wechat_pay_mock: true }))
    const never = new Promise<{ order: RechargeOrder; user: User }>(() => {})
    mocks.mockPayOrder.mockReturnValue(never)

    fireEvent.click(screen.getByRole('button', { name: /继续支付/ }))
    const button = await screen.findByRole('button', { name: '模拟支付' })
    act(() => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(mocks.mockPayOrder).toHaveBeenCalledTimes(1)
  })
})
