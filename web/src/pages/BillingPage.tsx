import { useEffect, useRef, useState } from 'react'
import {
  ArrowDownLeft,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Coins,
  CreditCard,
  Info,
  Loader2,
  QrCode,
  ReceiptText,
  RefreshCw,
  ShieldCheck,
  Ticket,
  Wallet,
  X,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import {
  billingOrderQrUrl,
  createBillingOrder,
  getBillingOrder,
  getBillingSummary,
  messageOf,
  mockPayOrder,
  redeemCreditCode,
} from '../lib/api'
import type { RechargeOrder } from '../lib/types'
import {
  creditKindMeta,
  formatCents,
  formatDate,
  formatNumber,
  formatRelative,
  orderStatusMeta,
  shortId,
  signedCredits,
} from '../lib/format'
import { useDocumentTitle, useQuery } from '../lib/hooks'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../components/Feedback'
import { TabHeading } from '../components/Workbench'

export function BillingPage() {
  useDocumentTitle('账户与充值')
  const { user, canWrite, updateUser, syncUser } = useAuth()
  const toast = useToast()
  const query = useQuery('billing', getBillingSummary)
  const data = query.data
  const [busyPackage, setBusyPackage] = useState<string | null>(null)
  const [activeOrder, setActiveOrder] = useState<RechargeOrder | null>(null)
  const [paying, setPaying] = useState(false)
  const [orderError, setOrderError] = useState<string | null>(null)
  const [redeemCode, setRedeemCode] = useState('')
  const [redeemBusy, setRedeemBusy] = useState(false)
  const [redeemError, setRedeemError] = useState<string | null>(null)
  const pollErrors = useRef(0)
  const settledOrder = useRef<string | null>(null)
  const readOnly = !canWrite

  useEffect(() => {
    if (data?.user && data.user.credits !== user?.credits) {
      updateUser(data.user)
    }
  }, [data?.user, user?.credits, updateUser])

  useEffect(() => {
    if (!activeOrder || activeOrder.status !== 'pending' || data?.wechat_pay_mock) return
    let cancelled = false
    pollErrors.current = 0
    const tick = async () => {
      try {
        const latest = await getBillingOrder(activeOrder.id)
        if (cancelled) return
        pollErrors.current = 0
        setActiveOrder(latest)
        if (latest.status === 'paid' && settledOrder.current !== latest.id) {
          settledOrder.current = latest.id
          query.refresh()
          try {
            await syncUser()
          } catch {
            // The refreshed billing summary still carries the new balance.
          }
          toast.success('充值已到账', `+${latest.credits} 点已加入账户余额。`)
        }
      } catch (error) {
        if (cancelled) return
        pollErrors.current += 1
        if (pollErrors.current >= 3) {
          setOrderError(`支付状态读取失败：${messageOf(error)}`)
        }
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), 2000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeOrder?.id, activeOrder?.status, data?.wechat_pay_mock])

  if (query.loading && !data) return <BillingSkeleton />

  if (query.error && !data) {
    return (
      <div className="billing-page">
        <div className="panel">
          <ErrorState message={query.error} onRetry={query.refresh} />
        </div>
      </div>
    )
  }

  if (!data) return <BillingSkeleton />

  const summary = data.summary
  const balance = summary.balance
  const packages = summary.packages || []
  const payAvailable = Boolean(data.wechat_pay || data.wechat_pay_mock)
  const purchaseDisabled = readOnly || !payAvailable || Boolean(busyPackage)
  const transactions = data.transactions || []
  const orders = data.orders || []

  const startOrder = async (packageId: string) => {
    if (purchaseDisabled) return
    setBusyPackage(packageId)
    setOrderError(null)
    settledOrder.current = null
    try {
      const order = await createBillingOrder(packageId)
      setActiveOrder(order)
      query.refresh()
      toast.info(
        data.wechat_pay_mock ? '订单已创建，等待模拟支付' : '订单已创建，请扫码支付',
        `${order.credits} 点 · ${formatCents(order.amount_cents)}`,
      )
    } catch (error) {
      const message = messageOf(error)
      setOrderError(message)
      toast.error('创建充值订单失败', message)
    } finally {
      setBusyPackage(null)
    }
  }

  const mockPay = async () => {
    if (!activeOrder || readOnly) return
    setPaying(true)
    setOrderError(null)
    try {
      const result = await mockPayOrder(activeOrder.id)
      setActiveOrder(result.order)
      updateUser(result.user)
      query.refresh()
      toast.success('模拟支付成功', `+${result.order.credits} 点已加入账户余额。`)
    } catch (error) {
      const message = messageOf(error)
      setOrderError(message)
      toast.error('模拟支付失败', message)
    } finally {
      setPaying(false)
    }
  }

  const redeem = async () => {
    if (readOnly || redeemBusy || !redeemCode.trim()) return
    setRedeemBusy(true)
    setRedeemError(null)
    const before = balance
    try {
      const result = await redeemCreditCode(redeemCode.trim())
      updateUser(result.user)
      setRedeemCode('')
      query.setData({
        ...data,
        user: result.user,
        summary: result.summary,
        transactions: result.transactions,
      })
      query.refresh()
      const gained = Math.max(0, result.summary.balance - before)
      toast.success(
        '兑换成功',
        `+${formatNumber(gained)} 点已到账，当前余额 ${formatNumber(result.summary.balance)} 点。`,
      )
    } catch (error) {
      const message = messageOf(error)
      setRedeemError(message)
      toast.error('兑换失败', message)
    } finally {
      setRedeemBusy(false)
    }
  }

  return (
    <div className="billing-page">
      <TabHeading
        eyebrow="账户与充值"
        title="点数与账单"
        description="查看、编辑、核对与导出免费；AI 生成按章扣点，生成失败会自动退回。"
        actions={
          <button
            type="button"
            className="text-btn"
            onClick={query.refresh}
            disabled={query.refreshing}
          >
            <RefreshCw size={13} className={query.refreshing ? 'spin' : undefined} />
            刷新余额
          </button>
        }
      />

      {readOnly && (
        <div className="billing-readonly" role="status">
          <ShieldCheck size={15} />
          <strong>只读成员</strong>
          <span>可以查看账户余额、交易与订单，但不能充值或兑换。</span>
        </div>
      )}

      <div className="billing-top">
        <section className="billing-balance-card">
          <div className="billing-balance-head">
            <span className="billing-eyebrow">可用点数</span>
            <Coins size={20} />
          </div>
          <div className="billing-balance-value">
            {formatNumber(balance)}
            <span>点</span>
          </div>
          <div className="billing-balance-meta">
            <span>
              <CreditCard size={13} /> 每章生成消耗 <strong>{summary.chapter_cost}</strong> 点
            </span>
            {summary.billing_exempt && (
              <Badge tone="success" subtle>
                内部账号不计费
              </Badge>
            )}
          </div>
          <div className="billing-balance-note">
            {summary.billing_exempt
              ? '你的账号属于内部成员，生成正文不扣点；充值主要用于外部账号或团队备用。'
              : `当前余额可支持约 ${
                  summary.chapter_cost > 0 ? Math.floor(balance / summary.chapter_cost) : '∞'
                } 章生成。`}
          </div>
        </section>

        <section className="billing-redeem-card">
          <div className="billing-card-head">
            <div>
              <div className="eyebrow">兑换码</div>
              <h2>兑换点数</h2>
            </div>
            <Ticket size={19} />
          </div>
          <p className="billing-card-lede">
            输入管理员生成的兑换码，点数会立即加入当前账号。
          </p>
          <form
            className="billing-redeem-form"
            onSubmit={(event) => {
              event.preventDefault()
              void redeem()
            }}
          >
            <input
              className="input mono"
              value={redeemCode}
              onChange={(event) => {
                setRedeemCode(event.target.value.toUpperCase())
                setRedeemError(null)
              }}
              placeholder="BC-XXXX-XXXX-XXXX"
              maxLength={40}
              disabled={readOnly || redeemBusy}
              aria-label="兑换码"
            />
            <Button
              type="submit"
              variant="primary"
              loading={redeemBusy}
              disabled={readOnly || !redeemCode.trim()}
            >
              兑换
            </Button>
          </form>
          {redeemError && (
            <div className="billing-inline-error" role="alert">
              {redeemError}
            </div>
          )}
          {summary.signup_credits > 0 && (
            <p className="billing-redeem-note">
              <Info size={12} /> 新注册账号会获得 {summary.signup_credits} 点体验额度。
            </p>
          )}
        </section>
      </div>

      <section className="billing-section">
        <div className="billing-section-head">
          <div>
            <div className="eyebrow">充值</div>
            <h2>选择充值套餐</h2>
          </div>
          <span className="billing-section-note">
            {data.wechat_pay_mock
              ? '当前为模拟支付，不会产生真实扣款'
              : payAvailable
                ? '微信支付 · 支付完成后自动到账'
                : '支付通道未配置，请联系管理员'}
          </span>
        </div>

        {packages.length === 0 ? (
          <div className="panel">
            <EmptyState icon={Wallet} compact title="管理员尚未配置充值套餐" />
          </div>
        ) : (
          <div className="billing-package-grid">
            {packages.map((pkg, index) => (
              <article
                key={pkg.id}
                className={`billing-package-card ${index === 1 ? 'is-featured' : ''}`}
              >
                {index === 1 && <span className="billing-package-flag">推荐</span>}
                <div className="billing-package-name">{pkg.name}</div>
                <div className="billing-package-credits">
                  {formatNumber(pkg.credits)}
                  <span>点</span>
                </div>
                <div className="billing-package-price">{formatCents(pkg.amount_cents)}</div>
                <div className="billing-package-per">
                  约 {formatCents(Math.round(pkg.amount_cents / Math.max(1, pkg.credits)))} / 点
                </div>
                <Button
                  variant={index === 1 ? 'primary' : 'secondary'}
                  block
                  icon={<CreditCard size={14} />}
                  loading={busyPackage === pkg.id}
                  disabled={purchaseDisabled}
                  title={
                    readOnly
                      ? '只读成员不能充值'
                      : !payAvailable
                        ? '支付通道未配置'
                        : undefined
                  }
                  onClick={() => void startOrder(pkg.id)}
                >
                  充值
                </Button>
              </article>
            ))}
          </div>
        )}
      </section>

      {activeOrder && (
        <section className="billing-order-panel" aria-live="polite">
          <div className="billing-order-head">
            <div>
              <div className="eyebrow">支付订单</div>
              <h2>
                {activeOrder.status === 'paid'
                  ? '充值已到账'
                  : activeOrder.status === 'closed'
                    ? '订单已关闭'
                    : data.wechat_pay_mock
                      ? '等待模拟支付'
                      : '等待微信扫码支付'}
              </h2>
            </div>
            <button
              type="button"
              className="icon-btn"
              onClick={() => setActiveOrder(null)}
              aria-label="关闭支付面板"
            >
              <X size={16} />
            </button>
          </div>
          <div className="billing-order-body">
            <div className="billing-order-facts">
              <div>
                <span>订单号</span>
                <strong className="mono">{activeOrder.out_trade_no}</strong>
              </div>
              <div>
                <span>充值点数</span>
                <strong>{formatNumber(activeOrder.credits)} 点</strong>
              </div>
              <div>
                <span>支付金额</span>
                <strong>{formatCents(activeOrder.amount_cents)}</strong>
              </div>
              <div>
                <span>状态</span>
                <Badge tone={orderStatusMeta(activeOrder.status).tone} subtle>
                  {orderStatusMeta(activeOrder.status).label}
                </Badge>
              </div>
            </div>

            {activeOrder.status === 'pending' &&
              (data.wechat_pay_mock ? (
                <div className="billing-mock-box">
                  <div>
                    <strong>模拟支付</strong>
                    <p>当前环境不会发起真实微信支付。点击后会立即增加点数。</p>
                  </div>
                  <Button
                    variant="primary"
                    icon={<CheckCircle2 size={15} />}
                    loading={paying}
                    disabled={readOnly}
                    onClick={() => void mockPay()}
                  >
                    模拟支付
                  </Button>
                </div>
              ) : (
                <div className="billing-qr-box">
                  <img
                    src={billingOrderQrUrl(activeOrder.id)}
                    alt="微信支付二维码"
                    className="billing-qr-image"
                  />
                  <div>
                    <strong>
                      <QrCode size={15} /> 使用微信扫码支付
                    </strong>
                    <p>支付完成后本页会自动刷新余额，请保持页面打开。</p>
                    <span className="billing-polling">
                      <Loader2 size={13} className="spin" /> 正在等待支付结果…
                    </span>
                  </div>
                </div>
              ))}

            {activeOrder.status === 'paid' && (
              <div className="billing-paid-box">
                <CheckCircle2 size={18} />
                <div>
                  <strong>支付成功，点数已到账</strong>
                  <p>当前余额 {formatNumber(balance)} 点。</p>
                </div>
              </div>
            )}
          </div>
          {orderError && (
            <div className="billing-inline-error" role="alert">
              {orderError}
            </div>
          )}
        </section>
      )}

      <div className="billing-columns">
        <section className="panel billing-transactions">
          <div className="panel-head">
            <div>
              <div className="eyebrow">点数流水</div>
              <h3>最近交易</h3>
            </div>
            <span className="subtle">{transactions.length} 条</span>
          </div>
          {transactions.length === 0 ? (
            <EmptyState
              icon={ReceiptText}
              compact
              title="还没有点数记录"
              description="注册赠送、充值、兑换和生成消耗都会显示在这里。"
            />
          ) : (
            <div className="billing-transaction-list">
              {transactions.map((item) => {
                const meta = creditKindMeta(item.kind)
                const positive = item.amount >= 0
                return (
                  <article key={item.id} className="billing-transaction">
                    <span
                      className={`billing-transaction-icon ${
                        positive ? 'is-positive' : 'is-negative'
                      }`}
                      aria-hidden
                    >
                      {positive ? <ArrowDownLeft size={15} /> : <ArrowUpRight size={15} />}
                    </span>
                    <div className="billing-transaction-main">
                      <div className="billing-transaction-title">
                        <Badge tone={meta.tone} subtle>
                          {meta.label}
                        </Badge>
                        <strong className={positive ? 'is-positive' : 'is-negative'}>
                          {signedCredits(item.amount)} 点
                        </strong>
                      </div>
                      <p>{item.note || '点数变动'}</p>
                      <span className="billing-transaction-meta">
                        <Clock3 size={11} /> {formatRelative(item.created_at)} · 余额{' '}
                        {formatNumber(item.balance_after)} 点
                      </span>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </section>

        <section className="panel billing-orders">
          <div className="panel-head">
            <div>
              <div className="eyebrow">充值订单</div>
              <h3>订单记录</h3>
            </div>
            <span className="subtle">{orders.length} 条</span>
          </div>
          {orders.length === 0 ? (
            <EmptyState
              icon={Wallet}
              compact
              title="还没有充值订单"
              description="选择套餐充值后，订单状态会显示在这里。"
            />
          ) : (
            <div className="billing-order-list">
              {orders.map((order) => {
                const meta = orderStatusMeta(order.status)
                return (
                  <article key={order.id} className="billing-order-row">
                    <div className="billing-order-row-head">
                      <span className="mono">#{shortId(order.out_trade_no, 18)}</span>
                      <Badge tone={meta.tone} subtle>
                        {meta.label}
                      </Badge>
                    </div>
                    <div className="billing-order-row-facts">
                      <span>{formatNumber(order.credits)} 点</span>
                      <span>{formatCents(order.amount_cents)}</span>
                      <span>{formatDate(order.created_at, false)}</span>
                    </div>
                    {order.status === 'pending' && !readOnly && payAvailable && (
                      <button
                        type="button"
                        className="text-link"
                        onClick={() => {
                          settledOrder.current = null
                          setActiveOrder(order)
                        }}
                      >
                        继续支付 <ArrowUpRight size={11} />
                      </button>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </section>
      </div>

      <div className="billing-footnote">
        <ShieldCheck size={15} />
        <span>
          查看、编辑、核对与导出不扣点；AI 生成按章扣点，生成失败或未写入会自动退回。
          内部账号不参与扣点。当前为本地部署，支付状态由你的服务端确认。
        </span>
      </div>
    </div>
  )
}

function BillingSkeleton() {
  return (
    <div className="billing-page" aria-busy="true">
      <div className="billing-skeleton-head">
        <Skeleton width={170} height={12} />
        <Skeleton width={240} height={34} />
        <Skeleton width="48%" height={13} />
      </div>
      <div className="billing-top">
        <Skeleton height={190} radius={10} />
        <Skeleton height={190} radius={10} />
      </div>
      <div className="billing-package-grid">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} height={230} radius={10} />
        ))}
      </div>
    </div>
  )
}
