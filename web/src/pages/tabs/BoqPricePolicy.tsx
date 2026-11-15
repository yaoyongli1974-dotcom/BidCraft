import { useEffect, useState } from 'react'
import {
  BadgePercent,
  ChevronRight,
  Save,
  TrendingUp,
} from 'lucide-react'
import type { BoqPayload, PricePolicy } from '../../lib/types'
import {
  adjustBoq,
  messageOf,
  normalizePricePolicy,
  roundTo,
  updatePricePolicy,
} from '../../lib/api'
import { className, formatMoney } from '../../lib/format'
import { Button } from '../../components/Button'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

function clampPolicy(policy: PricePolicy): PricePolicy {
  return normalizePricePolicy({
    enabled: policy.enabled,
    factor: policy.factor,
    round_digits: policy.round_digits,
  })
}

function samePolicy(left: PricePolicy, right: PricePolicy): boolean {
  return (
    left.enabled === right.enabled &&
    left.factor === right.factor &&
    left.round_digits === right.round_digits
  )
}

function policySummary(policy: PricePolicy, adjusted: number | null): string {
  const factor = formatMoney(policy.factor, 2)
  if (!policy.enabled) return `已保存系数 ×${factor}，当前按原价合计`
  return `×${factor} · ${policy.round_digits} 位小数${
    adjusted === null ? '' : ` · 调整后合计 ¥${formatMoney(adjusted, policy.round_digits)}`
  }`
}

/**
 * 调价策略面板：启用开关 + 系数 + 小数位，外加「按系数调价」的二次确认按钮。
 * 报价合计与调整后合计都是只读派生值，真正的价格只由后端写回清单。
 */
export function PricePolicyPanel({
  projectRef,
  packageId,
  packageName,
  policy,
  loading,
  error,
  canWrite,
  totalAmount,
  pricedCount,
  onRetry,
  onSaved,
  onAdjusted,
}: {
  projectRef: string
  packageId: string | null
  packageName?: string
  policy: PricePolicy | null
  loading: boolean
  error: string | null
  canWrite: boolean
  totalAmount: number
  pricedCount: number
  onRetry: () => void
  onSaved: (policy: PricePolicy) => void
  onAdjusted: (payload: BoqPayload) => void
}) {
  const toast = useToast()
  const confirm = useConfirm()
  const [draft, setDraft] = useState<PricePolicy | null>(policy)
  const [saving, setSaving] = useState(false)
  const [adjusting, setAdjusting] = useState(false)

  useEffect(() => {
    setDraft(policy)
  }, [policy])

  const dirty = Boolean(draft && policy && !samePolicy(draft, policy))
  const preview = draft ? roundTo(totalAmount * draft.factor, draft.round_digits) : null
  const summary = policy
    ? policySummary(
        policy,
        policy.enabled ? roundTo(totalAmount * policy.factor, policy.round_digits) : null,
      )
    : loading
      ? '正在读取调价策略…'
      : '统一调整清单报价'

  const patch = (next: Partial<PricePolicy>) => {
    setDraft((current) => (current ? { ...current, ...next } : current))
  }

  const save = async () => {
    if (!canWrite || !draft || saving || !dirty) return
    const payload = clampPolicy(draft)
    setSaving(true)
    try {
      const saved = await updatePricePolicy(projectRef, payload)
      onSaved(saved)
      toast.success(
        saved.enabled ? '调价策略已保存' : '调价策略已保存（未启用）',
        policySummary(saved, saved.enabled ? roundTo(totalAmount * saved.factor, saved.round_digits) : null),
      )
    } catch (err) {
      toast.error('保存调价策略失败', messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  const adjust = async () => {
    if (!canWrite || !policy || adjusting) return
    if (pricedCount === 0) {
      toast.warning('还没有可调价的单价', '请先为清单录入单价，或使用「定额套用」补齐。')
      return
    }
    const factor = formatMoney(policy.factor, 2)
    const scope = packageName ? `包件「${packageName}」` : '当前清单'
    const accepted = await confirm({
      title: `按系数 ×${factor} 调整单价？`,
      description: `将把${scope}中已录入单价的 ${pricedCount} 行乘以 ${factor}，按 ${policy.round_digits} 位小数四舍五入后写回清单，并改写已保存的单价。`,
      note: '调价会覆盖原有单价，且无法撤销；调整后的合计只受下一次调价或手工改价影响。',
      confirmLabel: '按系数调价',
      tone: 'danger',
    })
    if (!accepted) return
    setAdjusting(true)
    try {
      const payload = await adjustBoq(projectRef, packageId)
      onAdjusted(payload)
      toast.success(
        '已按系数调价',
        `${scope}共 ${pricedCount} 行单价已乘以 ${factor}，合计 ¥${formatMoney(
          payload.summary.total_amount,
        )}`,
      )
    } catch (err) {
      toast.error('按系数调价失败', messageOf(err))
    } finally {
      setAdjusting(false)
    }
  }

  return (
    <details className="price-policy">
      <summary>
        <span className="price-policy-title">
          <BadgePercent size={13} aria-hidden />
          <strong>调价策略</strong>
          {dirty && (
            <span className="price-policy-dirty price-policy-dirty--summary">未保存</span>
          )}
          {policy?.enabled && (
            <span className="price-policy-on">启用中</span>
          )}
          <span className="price-policy-summary">{summary}</span>
        </span>
        <span className="price-policy-caret" aria-hidden>
          <ChevronRight size={14} />
        </span>
      </summary>

      <div className="price-policy-body">
        {!draft ? (
          <div className="price-policy-message">
            {error ? (
              <>
                <span>
                  暂时读不到调价策略，可能是服务版本较旧：清单仍可正常报价，只是没有调整后合计。
                </span>
                <button type="button" className="text-btn" onClick={onRetry}>
                  重试
                </button>
              </>
            ) : (
              <span>正在读取调价策略…</span>
            )}
          </div>
        ) : (
          <>
            <div className="price-policy-grid">
              <div className="price-policy-field">
                <span>启用调价</span>
                <label className="price-policy-toggle">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    disabled={!canWrite || saving || adjusting}
                    onChange={(event) => patch({ enabled: event.target.checked })}
                  />
                  <span>在摘要与导出中显示调整后合计</span>
                </label>
                <small>关闭时合计按原价显示，系数仍会保存</small>
              </div>

              <label className="price-policy-field">
                <span>调价系数</span>
                <input
                  className="input input--sm input--number"
                  type="number"
                  min={0.1}
                  max={10}
                  step={0.01}
                  inputMode="decimal"
                  value={draft.factor}
                  disabled={!canWrite || saving || adjusting}
                  onChange={(event) => {
                    const next =
                      event.target.value === '' ? 0.1 : Number(event.target.value)
                    if (Number.isFinite(next)) patch({ factor: next })
                  }}
                  onBlur={() =>
                    setDraft((current) => (current ? clampPolicy(current) : current))
                  }
                />
                <small>0.1 – 10，例如 0.95 表示下浮 5%</small>
              </label>

              <label className="price-policy-field">
                <span>保留小数位</span>
                <select
                  className="select select--sm"
                  value={draft.round_digits}
                  disabled={!canWrite || saving || adjusting}
                  onChange={(event) => patch({ round_digits: Number(event.target.value) })}
                >
                  {[0, 1, 2, 3, 4].map((digits) => (
                    <option key={digits} value={digits}>
                      {digits} 位小数
                    </option>
                  ))}
                </select>
                <small>调整后合计与导出汇总按此四舍五入</small>
              </label>
            </div>

            <div className={className('price-policy-preview', !draft.enabled && 'is-off')}>
              <div>
                <span>当前合计</span>
                <strong className="mono">¥{formatMoney(totalAmount)}</strong>
              </div>
              <span className="price-policy-arrow" aria-hidden>
                ×{formatMoney(draft.factor, 2)}
              </span>
              <div className="is-adjusted">
                <span>调整后合计</span>
                <strong className="mono">
                  {draft.enabled && preview !== null
                    ? `¥${formatMoney(preview, draft.round_digits)}`
                    : '未启用'}
                </strong>
              </div>
              <span className="price-policy-hint">
                当前合计来自已录入单价，未保存的系数只在这里预览
              </span>
            </div>

            <div className="price-policy-foot">
              <span className="price-policy-note">
                「保存策略」只影响摘要与导出；「按系数调价」会把该包件已录入的单价直接乘以系数并覆盖写回，无法撤销。
              </span>
              <div className="price-policy-actions">
                {dirty && <span className="price-policy-dirty">未保存</span>}
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Save size={13} />}
                  loading={saving}
                  disabled={!canWrite || !dirty || saving || adjusting}
                  title={
                    !canWrite
                      ? '只读成员不能修改调价策略'
                      : !dirty
                        ? '改动后会启用保存'
                        : undefined
                  }
                  onClick={() => void save()}
                >
                  保存策略
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  icon={<TrendingUp size={13} />}
                  loading={adjusting}
                  disabled={!canWrite || saving || adjusting || pricedCount === 0}
                  title={
                    !canWrite
                      ? '只读成员不能调价'
                      : pricedCount === 0
                        ? '当前包件还没有录入单价'
                        : `把 ${pricedCount} 行已录入单价乘以 ${formatMoney(
                            policy?.factor ?? 1,
                            2,
                          )}`
                  }
                  onClick={() => void adjust()}
                >
                  按系数调价
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </details>
  )
}
