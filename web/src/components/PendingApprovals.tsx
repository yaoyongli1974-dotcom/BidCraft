import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { AlertCircle, ClipboardCheck, Inbox, Loader2, RefreshCw } from 'lucide-react'
import { getPendingApprovals } from '../api/jobs'
import { getPendingWorkflowApprovals } from '../api/orchestration'
import { isAbortError, messageOf } from '../lib/http'
import type { PendingApproval, PendingWorkflowApproval } from '../lib/types'
import { className, formatDate, formatMoney, formatRelative } from '../lib/format'
import { useAuth } from '../context/useAuth'

/** 面板打开时的轮询间隔（毫秒）。 */
const POLL_INTERVAL = 60_000

function modeLabel(mode: PendingApproval['mode']): string {
  if (mode === 'all') return '（会签）'
  if (mode === 'any') return '（或签）'
  return ''
}

/**
 * 顶栏「待我审批」入口：图标 + 数量 Badge（0 不显示数字），点击展开只读面板。
 * 面板分两节：报价/商务标待办跳清单对齐（BOQ）或商务标页签；工作流待审闸门跳
 * 项目的「Agent 工作流」页签。审批动作都在对应项目面板里完成，这里只读。
 */
export function PendingApprovals() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<PendingApproval[]>([])
  const [workflowItems, setWorkflowItems] = useState<PendingWorkflowApproval[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    try {
      const [data, workflowData] = await Promise.all([
        getPendingApprovals(signal),
        getPendingWorkflowApprovals(signal),
      ])
      setItems(data)
      setWorkflowItems(workflowData)
      setLoaded(true)
    } catch (err) {
      if (isAbortError(err)) return
      setError(messageOf(err))
    } finally {
      setLoading(false)
    }
  }, [])

  // 登录后先取一次，用于顶栏数量 Badge；卸载时取消在途请求。
  useEffect(() => {
    if (!user) return
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [user, load])

  // 打开面板时刷新，并每 60s 轮询；关闭或卸载时清理定时器与在途请求。
  useEffect(() => {
    if (!open || !user) return
    const controller = new AbortController()
    void load(controller.signal)
    const timer = window.setInterval(() => void load(controller.signal), POLL_INTERVAL)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [open, user, load])

  // 与 UserMenu 一致：路由变化时收起面板。
  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        rootRef.current?.querySelector<HTMLButtonElement>('.pending-trigger')?.focus()
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!user) return null

  const count = items.length + workflowItems.length
  const countText = count > 99 ? '99+' : String(count)

  const openItem = (item: PendingApproval) => {
    setOpen(false)
    // 商务标待办跳到商务标页签；报价版本仍沿用清单对齐（BOQ）页签。
    const tab = item.doc_id ? 'commercial' : 'boq'
    navigate(`/app/projects/${encodeURIComponent(item.project_id)}/${tab}`)
  }

  const openWorkflowItem = (item: PendingWorkflowApproval) => {
    setOpen(false)
    navigate(`/app/projects/${encodeURIComponent(item.project_id)}/agents`)
  }

  return (
    <div className="pending-menu" ref={rootRef}>
      <button
        type="button"
        className={className('pending-trigger', open && 'is-open')}
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={count > 0 ? `待我审批，${count} 项` : '待我审批，暂无待办'}
        title={count > 0 ? `待我审批 · ${count} 项` : '待我审批'}
      >
        <ClipboardCheck size={16} aria-hidden />
        <span className="pending-trigger-label">待我审批</span>
        {count > 0 && (
          <span className="pending-count mono" aria-hidden>
            {countText}
          </span>
        )}
      </button>

      {open && (
        <div className="pending-dropdown" role="menu" aria-label="待我审批">
          <div className="pending-dropdown-head">
            <ClipboardCheck size={14} aria-hidden />
            <strong>待我审批</strong>
            {loaded && <span className="pending-dropdown-count mono">{count} 项</span>}
            <button
              type="button"
              className="pending-refresh"
              onClick={() => void load()}
              disabled={loading}
              aria-label="刷新待我审批"
              title="刷新"
            >
              <RefreshCw size={12} className={loading ? 'spin' : undefined} aria-hidden />
            </button>
          </div>

          <div className="pending-dropdown-body">
            {error && (
              <div className="pending-error" role="alert">
                <AlertCircle size={13} aria-hidden />
                <span>{error}</span>
                <button type="button" onClick={() => void load()} disabled={loading}>
                  重试
                </button>
              </div>
            )}

            {loading && !loaded ? (
              <div className="pending-state" role="status">
                <Loader2 size={16} className="spin" aria-hidden />
                <span>正在加载待我审批…</span>
              </div>
            ) : count === 0 ? (
              !error && (
                <div className="pending-state">
                  <Inbox size={18} aria-hidden />
                  <span>暂无待我审批的报价</span>
                </div>
              )
            ) : (
              <>
                {items.length > 0 && (
                  <section className="pending-section" aria-label="报价与商务标">
                    <div className="pending-section-label">报价 / 商务标</div>
                    <ul className="pending-list">
                      {items.map((item) => (
                        <li key={`${item.project_id}:${item.doc_id || item.version_id}`}>
                          <button
                            type="button"
                            className="pending-item"
                            role="menuitem"
                            onClick={() => openItem(item)}
                            title={`${item.project_name} · ${item.version_name}`}
                          >
                            <span className="pending-item-title">
                              <strong>{item.project_name}</strong>
                              <span className="pending-item-version">· {item.version_name}</span>
                            </span>
                            <span className="pending-item-stage">
                              第 {item.stage}/{item.stage_count} 级{modeLabel(item.mode)}
                            </span>
                            <span className="pending-item-meta">
                              <span className="pending-item-submitter">
                                {item.submitted_by || '—'}
                                <span title={formatDate(item.submitted_at)}>
                                  {' '}
                                  · {formatRelative(item.submitted_at)}
                                </span>
                              </span>
                              {item.total_amount !== null && (
                                <span className="pending-item-amount mono">
                                  ¥{formatMoney(item.total_amount)}
                                </span>
                              )}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {workflowItems.length > 0 && (
                  <section className="pending-section" aria-label="工作流待审">
                    <div className="pending-section-label">工作流待审</div>
                    <ul className="pending-list">
                      {workflowItems.map((item) => (
                        <li key={`workflow:${item.id}:${item.run_id}`}>
                          <button
                            type="button"
                            className="pending-item"
                            role="menuitem"
                            onClick={() => openWorkflowItem(item)}
                            title={`${item.project_name} · ${item.goal || item.approval_type}`}
                          >
                            <span className="pending-item-title">
                              <strong>{item.project_name}</strong>
                              <span className="pending-item-version">
                                · {item.goal || item.workflow_type}
                              </span>
                            </span>
                            <span className="pending-item-stage">
                              {item.approval_type}
                              {item.status ? ` · ${item.status}` : ''}
                              {item.labels.length > 0 ? ` · ${item.labels.join(' · ')}` : ''}
                            </span>
                            <span className="pending-item-meta">
                              <span className="pending-item-submitter">
                                {item.submitted_by || '—'}
                                <span title={formatDate(item.submitted_at)}>
                                  {' '}
                                  · {formatRelative(item.submitted_at)}
                                </span>
                              </span>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </>
            )}
          </div>

          <div className="pending-dropdown-foot">
            <ClipboardCheck size={11} aria-hidden />
            报价跳清单对齐、商务标跳商务标页签，在对应面板中审批；Agent 工作流审批在项目「Agent 工作流」页签处理（提交人不能自审）
          </div>
        </div>
      )}
    </div>
  )
}