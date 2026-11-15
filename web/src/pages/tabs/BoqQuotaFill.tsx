import { useEffect, useMemo, useRef, useState } from 'react'
import { Info, PackageSearch, RefreshCw, WandSparkles } from 'lucide-react'
import type { BoqRow, QuotaFillResult, QuotaProposal } from '../../lib/types'
import { messageOf, quotaFill, setBoqPrice } from '../../lib/api'
import { className, formatMoney, formatNumber } from '../../lib/format'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'
import { Skeleton } from '../../components/Feedback'

function proposalKey(proposal: QuotaProposal): string {
  return `${proposal.boq_row ?? 'x'}:${proposal.quota_id}`
}

function scoreTone(score: number): 'is-strong' | 'is-fair' | 'is-weak' {
  if (score >= 0.9) return 'is-strong'
  if (score >= 0.8) return 'is-fair'
  return 'is-weak'
}

/**
 * 「定额套用」预览弹窗：先 dry_run 取提案，勾选后写入单价。
 * 全部勾选时走 quota-fill 一次性应用（审计记为 boq.quota_fill）；
 * 只勾选部分行时逐行写单价，保证「勾了才写」，不会悄悄覆盖未勾选的行。
 */
export function QuotaFillModal({
  open,
  projectRef,
  packageId,
  packageName,
  rows,
  readOnly,
  onClose,
  onApplied,
}: {
  open: boolean
  projectRef: string
  packageId: string | null
  packageName?: string
  rows: BoqRow[]
  readOnly: boolean
  onClose: () => void
  onApplied: (applied: number) => void
}) {
  const [onlyMissing, setOnlyMissing] = useState(true)
  const [result, setResult] = useState<QuotaFillResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [applying, setApplying] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [nonce, setNonce] = useState(0)
  const requestId = useRef(0)

  // 关闭后回到默认的「仅填补未报价行」，下次打开就是一次干净的预览。
  useEffect(() => {
    if (open) return
    setOnlyMissing(true)
    setResult(null)
    setError(null)
    setSelected(new Set())
    setApplying(false)
    setProgress(null)
  }, [open])

  useEffect(() => {
    if (!open) return
    const id = requestId.current + 1
    requestId.current = id
    let active = true
    setLoading(true)
    setError(null)
    setResult(null)
    setSelected(new Set())
    quotaFill(projectRef, {
      package_id: packageId,
      only_missing: onlyMissing,
      dry_run: true,
    })
      .then((response) => {
        if (!active || requestId.current !== id) return
        setResult(response)
        setSelected(new Set(response.proposals.map(proposalKey)))
      })
      .catch((err: unknown) => {
        if (!active || requestId.current !== id) return
        setError(messageOf(err))
      })
      .finally(() => {
        if (active && requestId.current === id) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [open, projectRef, packageId, onlyMissing, nonce])

  const proposals = useMemo(() => result?.proposals ?? [], [result?.proposals])

  const qtyByRow = useMemo(() => {
    const map = new Map<number, number | null>()
    for (const row of rows) {
      if (row.boq_row === null) continue
      map.set(row.boq_row, row.qty)
    }
    return map
  }, [rows])

  const estimate = useMemo(() => {
    let total = 0
    let missingQty = 0
    for (const proposal of proposals) {
      if (!selected.has(proposalKey(proposal))) continue
      const qty =
        proposal.boq_row === null ? null : qtyByRow.get(proposal.boq_row) ?? null
      if (typeof qty === 'number' && Number.isFinite(qty)) total += qty * proposal.unit_price
      else missingQty += 1
    }
    return { total, missingQty }
  }, [proposals, selected, qtyByRow])

  const toggle = (proposal: QuotaProposal) => {
    if (applying) return
    const key = proposalKey(proposal)
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const allSelected = proposals.length > 0 && selected.size === proposals.length

  const applySelected = async () => {
    if (readOnly || applying || !result) return
    const chosen = result.proposals.filter((proposal) => selected.has(proposalKey(proposal)))
    if (!chosen.length) return
    setApplying(true)
    setError(null)
    setProgress(null)
    try {
      if (chosen.length === result.proposals.length) {
        const response = await quotaFill(projectRef, {
          package_id: packageId,
          only_missing: onlyMissing,
          dry_run: false,
        })
        onApplied(response.applied)
      } else {
        let done = 0
        setProgress({ done: 0, total: chosen.length })
        for (const proposal of chosen) {
          if (proposal.boq_row !== null) {
            await setBoqPrice(projectRef, {
              boq_row: proposal.boq_row,
              unit_price: proposal.unit_price,
              ...(packageId ? { package_id: packageId } : {}),
            })
          }
          done += 1
          setProgress({ done, total: chosen.length })
        }
        onApplied(done)
      }
      onClose()
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setApplying(false)
      setProgress(null)
    }
  }

  const reload = () => setNonce((value) => value + 1)

  return (
    <Modal
      open={open}
      onClose={applying ? () => undefined : onClose}
      title="按企业定额套用单价"
      eyebrow={packageName ? `包件 · ${packageName}` : '当前清单'}
      size="lg"
      footer={
        <>
          <span className="quota-fill-foot-note">
            {loading
              ? '正在生成预览…'
              : proposals.length
                ? `已选 ${selected.size} / ${proposals.length} 行，确认后写入清单单价`
                : '预览不会写入清单'}
          </span>
          <Button variant="ghost" onClick={onClose} disabled={applying}>
            取消
          </Button>
          <Button
            variant="primary"
            icon={<WandSparkles size={14} />}
            loading={applying}
            disabled={readOnly || loading || selected.size === 0}
            title={
              readOnly
                ? '只读成员不能套用定额'
                : selected.size === 0
                  ? '请至少勾选一条清单行'
                  : undefined
            }
            onClick={() => void applySelected()}
          >
            {progress
              ? `套用中 ${progress.done}/${progress.total}`
              : selected.size > 0
                ? `套用 ${selected.size} 条单价`
                : '套用单价'}
          </Button>
        </>
      }
    >
      <div className="quota-fill">
        <div className="quota-fill-note">
          <Info size={15} aria-hidden />
          <p>
            程序只会套用管理员在「定额库」里维护的单价，不会估算或凭空生成价格。
            推荐先预览再勾选，未达到相似度阈值的行不会出现在列表里。
          </p>
        </div>

        <div className="quota-fill-bar">
          <label className={className('quota-switch', readOnly && 'is-disabled')}>
            <input
              type="checkbox"
              checked={onlyMissing}
              disabled={readOnly || loading || applying}
              onChange={(event) => setOnlyMissing(event.target.checked)}
            />
            <span className="quota-switch-track" aria-hidden>
              <span />
            </span>
            <span className="quota-switch-text">
              仅填补未报价行
              <small>关闭后，已有单价的行也会被覆盖</small>
            </span>
          </label>

          <div className="quota-fill-bar-right">
            {proposals.length > 0 && (
              <button
                type="button"
                className="text-btn"
                disabled={applying}
                onClick={() =>
                  setSelected(
                    allSelected
                      ? new Set()
                      : new Set(proposals.map(proposalKey)),
                  )
                }
              >
                {allSelected ? '取消全选' : '全选'}
              </button>
            )}
            <button
              type="button"
              className="text-btn"
              onClick={reload}
              disabled={loading || applying}
            >
              <RefreshCw size={13} className={loading ? 'spin' : undefined} />
              重新预览
            </button>
          </div>
        </div>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}

        {loading ? (
          <div className="quota-fill-skeleton" aria-busy="true" aria-label="正在生成预览">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="quota-fill-skeleton-row">
                <Skeleton width={14} height={14} radius={3} />
                <Skeleton width={26} height={12} />
                <Skeleton width="34%" height={13} />
                <Skeleton width="26%" height={12} />
                <Skeleton width={54} height={12} />
              </div>
            ))}
          </div>
        ) : proposals.length === 0 ? (
          <div className="quota-fill-empty">
            <PackageSearch size={22} aria-hidden />
            <strong>没有可套用的清单行</strong>
            <p>
              {onlyMissing
                ? '当前包件的清单行都已录入单价，或没有与定额库相似度达到 0.75 的行。可以关闭「仅填补未报价行」再看看，或先在管理端补充定额。'
                : '没有与定额库相似度达到 0.75 的清单行。可以按清单写法补充定额名称，再回来套用。'}
            </p>
            <Button size="sm" variant="secondary" icon={<RefreshCw size={13} />} onClick={reload}>
              重新预览
            </Button>
          </div>
        ) : (
          <div className="quota-fill-table-wrap">
            <table className="data-table quota-fill-table">
              <thead>
                <tr>
                  <th className="quota-fill-check">
                    <span className="sr-only">选择</span>
                  </th>
                  <th className="quota-fill-row-no">行</th>
                  <th>清单名称</th>
                  <th>匹配定额</th>
                  <th className="num">相似度</th>
                  <th className="num">单价（元）</th>
                </tr>
              </thead>
              <tbody>
                {proposals.map((proposal) => {
                  const key = proposalKey(proposal)
                  const checked = selected.has(key)
                  const pct = Math.round(Math.max(0, Math.min(1, proposal.score)) * 100)
                  const qty =
                    proposal.boq_row === null
                      ? null
                      : qtyByRow.get(proposal.boq_row) ?? null
                  return (
                    <tr
                      key={key}
                      className={className('quota-fill-row', checked && 'is-checked')}
                    >
                      <td className="quota-fill-check">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={applying}
                          aria-label={`套用第 ${proposal.boq_row ?? '—'} 行的定额单价`}
                          onChange={() => toggle(proposal)}
                        />
                      </td>
                      <td className="quota-fill-row-no mono">
                        {proposal.boq_row ?? '—'}
                      </td>
                      <td>
                        <strong>{proposal.name || '—'}</strong>
                        {typeof qty === 'number' ? (
                          <span className="cell-sub">
                            数量 {formatNumber(qty)} · 合价 ¥
                            {formatMoney(qty * proposal.unit_price)}
                          </span>
                        ) : (
                          <span className="cell-sub">清单缺数量，无法计算合价</span>
                        )}
                      </td>
                      <td>
                        <span className="quota-fill-match">{proposal.quota_name || '—'}</span>
                      </td>
                      <td className="num">
                        <span className={className('quota-fill-score', scoreTone(proposal.score))}>
                          {pct}%
                        </span>
                      </td>
                      <td className="num">
                        <span className="mono">¥{formatMoney(proposal.unit_price)}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {proposals.length > 0 && !loading && (
          <div className="quota-fill-summary">
            <span>
              选中 <strong>{selected.size}</strong> / {proposals.length} 行
            </span>
            <span className="quota-fill-total">
              预计新增报价
              <strong className="mono">¥{formatMoney(estimate.total)}</strong>
            </span>
            {estimate.missingQty > 0 && (
              <span className="quota-fill-warn">
                {estimate.missingQty} 行缺数量，未计入预估
              </span>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}
