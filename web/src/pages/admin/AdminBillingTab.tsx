import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BadgePercent,
  Copy,
  Coins,
  CreditCard,
  Plus,
  RefreshCw,
  Save,
  Ticket,
  Trash2,
  Users,
  Wallet,
} from 'lucide-react'
import { useQuery } from '../../lib/hooks'
import {
  generateRedeemCodes,
  getAdminBilling,
  messageOf,
  saveAdminBilling,
} from '../../lib/api'
import type { BillingPackage, VipPackage } from '../../lib/types'
import {
  formatCents,
  formatDate,
  formatNumber,
  orderStatusMeta,
  redeemStatusMeta,
  shortId,
} from '../../lib/format'
import { useToast } from '../../context/useToast'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { ErrorState, Skeleton } from '../../components/Feedback'
import { AdminSectionLabel, AdminSkeletonRows } from './AdminShared'

interface PackageDraft {
  key: string
  id: string
  name: string
  credits: string
  price: string
}

let packageKey = 0

function toDraft(pkg: BillingPackage): PackageDraft {
  packageKey += 1
  return {
    key: `pkg-${packageKey}`,
    id: pkg.id,
    name: pkg.name,
    credits: String(pkg.credits),
    price: (pkg.amount_cents / 100).toFixed(2),
  }
}

interface VipPackageDraft {
  key: string
  id: string
  name: string
  days: string
  price: string
}

function toVipDraft(pkg: VipPackage): VipPackageDraft {
  packageKey += 1
  return {
    key: `vip-${packageKey}`,
    id: pkg.id,
    name: pkg.name,
    days: String(pkg.days),
    price: (pkg.amount_cents / 100).toFixed(2),
  }
}

export function AdminBillingTab() {
  const toast = useToast()
  const query = useQuery('admin:billing', getAdminBilling)
  const data = query.data
  const [signup, setSignup] = useState('5')
  const [chapterCost, setChapterCost] = useState('1')
  const [packages, setPackages] = useState<PackageDraft[]>([])
  const [vipPackages, setVipPackages] = useState<VipPackageDraft[]>([])
  const [vipDiscount, setVipDiscount] = useState('0.5')
  const [subLimit, setSubLimit] = useState('5')
  const [saving, setSaving] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [codeCount, setCodeCount] = useState('10')
  const [codeCredits, setCodeCredits] = useState('10')
  const [codeAmount, setCodeAmount] = useState('')
  const [codeDays, setCodeDays] = useState('365')
  const [generating, setGenerating] = useState(false)
  const [codeError, setCodeError] = useState<string | null>(null)
  const [generatedCodes, setGeneratedCodes] = useState<string[]>([])
  const settingsInitialized = useRef(false)

  useEffect(() => {
    if (!data || settingsInitialized.current) return
    settingsInitialized.current = true
    setSignup(String(data.settings.signup_credits))
    setChapterCost(String(data.settings.chapter_cost))
    setPackages(data.settings.packages.map(toDraft))
    setVipPackages((data.settings.vip_packages || []).map(toVipDraft))
    setVipDiscount(String(data.settings.vip_discount ?? 0.5))
    setSubLimit(String(data.settings.sub_account_limit ?? 5))
  }, [data])

  const stats = data?.stats
  const orders = useMemo(() => data?.orders || [], [data?.orders])
  const codes = data?.codes || []

  const totalOrderCents = useMemo(
    () => orders.filter((order) => order.status === 'paid').reduce((sum, order) => sum + order.amount_cents, 0),
    [orders],
  )

  const addPackage = () => {
    setPackages((current) => [
      ...current,
      { key: `pkg-new-${Date.now()}`, id: '', name: '', credits: '10', price: '9.90' },
    ])
  }

  const updatePackage = (key: string, patch: Partial<PackageDraft>) => {
    setPackages((current) =>
      current.map((pkg) => (pkg.key === key ? { ...pkg, ...patch } : pkg)),
    )
  }

  const removePackage = (key: string) => {
    setPackages((current) => current.filter((pkg) => pkg.key !== key))
  }

  const addVipPackage = () => {
    setVipPackages((current) => [
      ...current,
      { key: `vip-new-${Date.now()}`, id: '', name: '', days: '30', price: '30.00' },
    ])
  }

  const updateVipPackage = (key: string, patch: Partial<VipPackageDraft>) => {
    setVipPackages((current) =>
      current.map((pkg) => (pkg.key === key ? { ...pkg, ...patch } : pkg)),
    )
  }

  const removeVipPackage = (key: string) => {
    setVipPackages((current) => current.filter((pkg) => pkg.key !== key))
  }

  const saveSettings = async () => {
    setSettingsError(null)
    if (packages.length === 0) {
      setSettingsError('至少需要一个充值套餐。')
      return
    }
    const payload = {
      signup_credits: Number(signup),
      chapter_cost: Number(chapterCost),
      packages: packages.map((pkg) => ({
        id: pkg.id.trim(),
        name: pkg.name.trim(),
        credits: Math.round(Number(pkg.credits)),
        amount_cents: Math.round(Number(pkg.price) * 100),
      })),
      vip_packages: vipPackages.map((pkg) => ({
        id: pkg.id.trim(),
        name: pkg.name.trim(),
        days: Math.round(Number(pkg.days)),
        amount_cents: Math.round(Number(pkg.price) * 100),
      })),
      vip_discount: Number(vipDiscount),
      sub_account_limit: Math.round(Number(subLimit)),
    }
    if (
      !Number.isFinite(payload.signup_credits) ||
      !Number.isFinite(payload.chapter_cost) ||
      payload.packages.some(
        (pkg) =>
          !pkg.id ||
          !pkg.name ||
          !Number.isFinite(pkg.credits) ||
          pkg.credits <= 0 ||
          !Number.isFinite(pkg.amount_cents) ||
          pkg.amount_cents <= 0,
      )
    ) {
      setSettingsError('请检查套餐 ID、名称、点数和价格，点数与价格必须大于 0。')
      return
    }
    if (
      payload.vip_packages.some(
        (pkg) =>
          !pkg.id ||
          !pkg.name ||
          !Number.isFinite(pkg.days) ||
          pkg.days <= 0 ||
          !Number.isFinite(pkg.amount_cents) ||
          pkg.amount_cents <= 0,
      ) ||
      !Number.isFinite(payload.vip_discount) ||
      payload.vip_discount < 0.1 ||
      payload.vip_discount > 1 ||
      !Number.isFinite(payload.sub_account_limit) ||
      payload.sub_account_limit < 0 ||
      payload.sub_account_limit > 20
    ) {
      setSettingsError('请检查 VIP 套餐（天数与价格须大于 0）、折扣（0.1–1）与子账号上限（0–20）。')
      return
    }
    setSaving(true)
    try {
      await saveAdminBilling(payload)
      query.refresh()
      toast.success('计费设置已保存')
    } catch (error) {
      const message = messageOf(error)
      setSettingsError(message)
      toast.error('保存计费设置失败', message)
    } finally {
      setSaving(false)
    }
  }

  const generateCodes = async () => {
    setCodeError(null)
    const payload = {
      count: Math.round(Number(codeCount)),
      credits: Math.round(Number(codeCredits)),
      amount_cents: codeAmount.trim() ? Math.round(Number(codeAmount) * 100) : 0,
      days: Math.round(Number(codeDays)),
    }
    if (
      !Number.isFinite(payload.count) ||
      payload.count < 1 ||
      payload.count > 200 ||
      !Number.isFinite(payload.credits) ||
      payload.credits <= 0 ||
      !Number.isFinite(payload.days) ||
      payload.days < 1
    ) {
      setCodeError('生成数量需为 1–200，点数和有效天数必须大于 0。')
      return
    }
    setGenerating(true)
    try {
      const result = await generateRedeemCodes(payload)
      setGeneratedCodes(result.codes || [])
      query.refresh()
      toast.success('兑换码已生成', `共 ${result.codes?.length || 0} 个`)
    } catch (error) {
      const message = messageOf(error)
      setCodeError(message)
      toast.error('生成兑换码失败', message)
    } finally {
      setGenerating(false)
    }
  }

  const copyCodes = async () => {
    if (!generatedCodes.length) return
    try {
      await navigator.clipboard.writeText(generatedCodes.join('\n'))
      toast.success('已复制兑换码', `${generatedCodes.length} 个`)
    } catch {
      toast.error('复制失败', '浏览器未授权剪贴板访问，请手动选择文本复制。')
    }
  }

  if (query.loading && !data) {
    return (
      <div className="admin-billing">
        <div className="admin-billing-metrics">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} height={84} radius={8} />
          ))}
        </div>
        <AdminSkeletonRows rows={4} />
      </div>
    )
  }

  if (query.error && !data) {
    return (
      <div className="admin-panel-inner">
        <ErrorState message={query.error} onRetry={query.refresh} />
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="admin-billing">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="点数与支付"
          title="计费与充值"
          description="配置注册赠送、章节消耗与充值套餐，生成兑换码并查看外部用户与订单。"
          aside={
            <button type="button" className="text-btn" onClick={query.refresh} disabled={query.refreshing}>
              <RefreshCw size={13} className={query.refreshing ? 'spin' : undefined} />
              刷新
            </button>
          }
        />
      </div>

      <div className="admin-billing-metrics">
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon">
            <Users size={16} />
          </span>
          <div>
            <span>外部用户</span>
            <strong>{formatNumber(stats?.external_users || 0)}</strong>
            <small>共 {formatNumber(stats?.users || 0)} 个账号</small>
          </div>
        </div>
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon">
            <CreditCard size={16} />
          </span>
          <div>
            <span>累计营收</span>
            <strong>{formatCents(stats?.revenue_cents || 0)}</strong>
            <small>已支付订单合计</small>
          </div>
        </div>
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon">
            <Coins size={16} />
          </span>
          <div>
            <span>流通点数</span>
            <strong>{formatNumber(stats?.outstanding_credits || 0)}</strong>
            <small>全部用户余额合计</small>
          </div>
        </div>
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon">
            <Wallet size={16} />
          </span>
          <div>
            <span>已支付订单</span>
            <strong>{formatNumber(orders.filter((order) => order.status === 'paid').length)}</strong>
            <small>{formatCents(totalOrderCents)} 已到账</small>
          </div>
        </div>
      </div>

      <div className="admin-billing-grid">
        <section className="admin-billing-card">
          <div className="admin-billing-card-head">
            <div>
              <div className="eyebrow">计费规则</div>
              <h3>点数与套餐</h3>
            </div>
            <BadgePercent size={18} />
          </div>
          <div className="admin-form-grid admin-billing-settings">
            <div className="form-field">
              <label htmlFor="billing-signup">注册赠送点数</label>
              <input
                id="billing-signup"
                className="input input--number"
                type="number"
                min={0}
                value={signup}
                onChange={(event) => setSignup(event.target.value)}
              />
              <p className="field-hint">仅对外部自注册账号生效。</p>
            </div>
            <div className="form-field">
              <label htmlFor="billing-chapter">每章生成消耗</label>
              <input
                id="billing-chapter"
                className="input input--number"
                type="number"
                min={0}
                value={chapterCost}
                onChange={(event) => setChapterCost(event.target.value)}
              />
              <p className="field-hint">内部账号不计费；生成失败自动退回。</p>
            </div>
          </div>

          <div className="admin-billing-packages">
            <div className="admin-billing-packages-head">
              <strong>充值套餐</strong>
              <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={addPackage}>
                添加套餐
              </Button>
            </div>
            <div className="admin-package-table">
              <div className="admin-package-head">
                <span>ID</span>
                <span>名称</span>
                <span>点数</span>
                <span>价格（元）</span>
                <span />
              </div>
              {packages.map((pkg) => (
                <div className="admin-package-row" key={pkg.key}>
                  <input
                    className="input input--sm mono"
                    value={pkg.id}
                    onChange={(event) => updatePackage(pkg.key, { id: event.target.value })}
                    placeholder="starter"
                    aria-label="套餐 ID"
                  />
                  <input
                    className="input input--sm"
                    value={pkg.name}
                    onChange={(event) => updatePackage(pkg.key, { name: event.target.value })}
                    placeholder="入门包"
                    aria-label="套餐名称"
                  />
                  <input
                    className="input input--sm input--number"
                    type="number"
                    min={1}
                    value={pkg.credits}
                    onChange={(event) => updatePackage(pkg.key, { credits: event.target.value })}
                    aria-label="套餐点数"
                  />
                  <input
                    className="input input--sm input--number"
                    type="number"
                    min={0.01}
                    step={0.01}
                    value={pkg.price}
                    onChange={(event) => updatePackage(pkg.key, { price: event.target.value })}
                    aria-label="套餐价格"
                  />
                  <IconButton
                    label={`删除套餐 ${pkg.name || pkg.id}`}
                    tone="danger"
                    onClick={() => removePackage(pkg.key)}
                  >
                    <Trash2 size={13} />
                  </IconButton>
                </div>
              ))}
            </div>
          </div>

          <div className="admin-billing-packages">
            <div className="admin-billing-packages-head">
              <strong>VIP 会员套餐</strong>
              <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={addVipPackage}>
                添加 VIP 套餐
              </Button>
            </div>
            <div className="admin-package-table admin-vip-table">
              <div className="admin-package-head admin-vip-head">
                <span>ID</span>
                <span>名称</span>
                <span>天数</span>
                <span>价格（元）</span>
                <span />
              </div>
              {vipPackages.map((pkg) => (
                <div className="admin-package-row admin-vip-row" key={pkg.key}>
                  <input
                    className="input input--sm mono"
                    value={pkg.id}
                    onChange={(event) => updateVipPackage(pkg.key, { id: event.target.value })}
                    placeholder="vip-month"
                    aria-label="VIP 套餐 ID"
                  />
                  <input
                    className="input input--sm"
                    value={pkg.name}
                    onChange={(event) => updateVipPackage(pkg.key, { name: event.target.value })}
                    placeholder="月卡"
                    aria-label="VIP 套餐名称"
                  />
                  <input
                    className="input input--sm input--number"
                    type="number"
                    min={1}
                    value={pkg.days}
                    onChange={(event) => updateVipPackage(pkg.key, { days: event.target.value })}
                    aria-label="VIP 天数"
                  />
                  <input
                    className="input input--sm input--number"
                    type="number"
                    min={0.01}
                    step={0.01}
                    value={pkg.price}
                    onChange={(event) => updateVipPackage(pkg.key, { price: event.target.value })}
                    aria-label="VIP 价格"
                  />
                  <IconButton
                    label={`删除 VIP 套餐 ${pkg.name || pkg.id}`}
                    tone="danger"
                    onClick={() => removeVipPackage(pkg.key)}
                  >
                    <Trash2 size={13} />
                  </IconButton>
                </div>
              ))}
            </div>
          </div>

          <div className="admin-form-grid admin-billing-settings">
            <div className="form-field">
              <label htmlFor="billing-vip-discount">VIP 生成折扣</label>
              <input
                id="billing-vip-discount"
                className="input input--number"
                type="number"
                min={0.1}
                max={1}
                step={0.05}
                value={vipDiscount}
                onChange={(event) => setVipDiscount(event.target.value)}
              />
              <p className="field-hint">VIP 会员每章消耗按此折扣计（0.1–1，0.5 即半价）。</p>
            </div>
            <div className="form-field">
              <label htmlFor="billing-sub-limit">子账号上限</label>
              <input
                id="billing-sub-limit"
                className="input input--number"
                type="number"
                min={0}
                max={20}
                value={subLimit}
                onChange={(event) => setSubLimit(event.target.value)}
              />
              <p className="field-hint">每个 VIP 主账号可创建的子账号数（0–20，0 为禁用）。</p>
            </div>
          </div>

          {settingsError && (
            <div className="billing-inline-error" role="alert">
              {settingsError}
            </div>
          )}
          <div className="admin-billing-actions">
            <Button
              variant="primary"
              icon={<Save size={14} />}
              loading={saving}
              onClick={() => void saveSettings()}
            >
              保存计费设置
            </Button>
          </div>
        </section>

        <section className="admin-billing-card">
          <div className="admin-billing-card-head">
            <div>
              <div className="eyebrow">兑换码</div>
              <h3>生成兑换码</h3>
            </div>
            <Ticket size={18} />
          </div>
          <div className="admin-form-grid admin-redeem-settings">
            <div className="form-field">
              <label htmlFor="redeem-count">生成数量</label>
              <input
                id="redeem-count"
                className="input input--number"
                type="number"
                min={1}
                max={200}
                value={codeCount}
                onChange={(event) => setCodeCount(event.target.value)}
              />
            </div>
            <div className="form-field">
              <label htmlFor="redeem-credits">每码点数</label>
              <input
                id="redeem-credits"
                className="input input--number"
                type="number"
                min={1}
                value={codeCredits}
                onChange={(event) => setCodeCredits(event.target.value)}
              />
            </div>
            <div className="form-field">
              <label htmlFor="redeem-amount">面值（元，可选）</label>
              <input
                id="redeem-amount"
                className="input input--number"
                type="number"
                min={0}
                step={0.01}
                value={codeAmount}
                onChange={(event) => setCodeAmount(event.target.value)}
                placeholder="0.00"
              />
            </div>
            <div className="form-field">
              <label htmlFor="redeem-days">有效天数</label>
              <input
                id="redeem-days"
                className="input input--number"
                type="number"
                min={1}
                value={codeDays}
                onChange={(event) => setCodeDays(event.target.value)}
              />
            </div>
          </div>
          {codeError && (
            <div className="billing-inline-error" role="alert">
              {codeError}
            </div>
          )}
          <div className="admin-billing-actions">
            <Button
              variant="secondary"
              icon={<Ticket size={14} />}
              loading={generating}
              onClick={() => void generateCodes()}
            >
              生成兑换码
            </Button>
          </div>

          {generatedCodes.length > 0 && (
            <div className="admin-generated-codes">
              <div className="admin-generated-codes-head">
                <strong>本次生成 {generatedCodes.length} 个</strong>
                <button type="button" className="text-btn" onClick={() => void copyCodes()}>
                  <Copy size={13} /> 复制全部
                </button>
              </div>
              <pre>{generatedCodes.join('\n')}</pre>
              <p className="field-hint">兑换码只在本次显示中集中展示；页面下方列表会保留记录。</p>
            </div>
          )}
        </section>
      </div>

      <div className="admin-billing-tables">
        <section className="admin-billing-card">
          <div className="admin-billing-card-head">
            <div>
              <div className="eyebrow">订单</div>
              <h3>最近订单</h3>
            </div>
            <span className="subtle">{orders.length} 条</span>
          </div>
          {orders.length === 0 ? (
            <div className="admin-billing-empty">还没有充值订单。</div>
          ) : (
            <div className="admin-billing-table-wrap">
              <table className="admin-data-table">
                <thead>
                  <tr>
                    <th>订单号</th>
                    <th>点数</th>
                    <th>金额</th>
                    <th>状态</th>
                    <th>渠道</th>
                    <th>创建时间</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((order) => {
                    const meta = orderStatusMeta(order.status)
                    return (
                      <tr key={order.id}>
                        <td className="mono">{shortId(order.out_trade_no, 22)}</td>
                        <td>{formatNumber(order.credits)} 点</td>
                        <td>{formatCents(order.amount_cents)}</td>
                        <td>
                          <Badge tone={meta.tone} subtle>
                            {meta.label}
                          </Badge>
                        </td>
                        <td>{order.provider === 'mock' ? '模拟' : order.provider}</td>
                        <td>{formatDate(order.created_at, false)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="admin-billing-card">
          <div className="admin-billing-card-head">
            <div>
              <div className="eyebrow">兑换码</div>
              <h3>最近兑换码</h3>
            </div>
            <span className="subtle">{codes.length} 条</span>
          </div>
          {codes.length === 0 ? (
            <div className="admin-billing-empty">还没有生成兑换码。</div>
          ) : (
            <div className="admin-billing-table-wrap">
              <table className="admin-data-table">
                <thead>
                  <tr>
                    <th>兑换码</th>
                    <th>点数</th>
                    <th>面值</th>
                    <th>状态</th>
                    <th>使用账号</th>
                    <th>有效期至</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {codes.map((code) => {
                    const meta = redeemStatusMeta(code.status)
                    return (
                      <tr key={code.id}>
                        <td className="mono">{code.code}</td>
                        <td>{formatNumber(code.credits)} 点</td>
                        <td>{code.amount_cents ? formatCents(code.amount_cents) : '—'}</td>
                        <td>
                          <Badge tone={meta.tone} subtle>
                            {meta.label}
                          </Badge>
                        </td>
                        <td className="mono">{shortId(code.used_by, 8)}</td>
                        <td>{formatDate(code.expires_at, false)}</td>
                        <td>
                          <button
                            type="button"
                            className="icon-btn"
                            aria-label={`复制兑换码 ${code.code}`}
                            onClick={() => {
                              void navigator.clipboard
                                .writeText(code.code)
                                .then(() => toast.success('已复制兑换码', code.code))
                                .catch(() => toast.error('复制失败', '请手动选择兑换码复制。'))
                            }}
                          >
                            <Copy size={13} />
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <div className="admin-footnote">
        注册赠送与章节消耗仅对外部账号生效；内部成员不计费。兑换码使用后记录使用账号与时间。
        最近订单和兑换码最多展示 50 条。
      </div>
    </div>
  )
}
