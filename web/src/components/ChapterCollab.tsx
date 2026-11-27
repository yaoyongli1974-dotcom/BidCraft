import { useEffect, useMemo, useState } from 'react'
import { History, Lock, LockOpen, RefreshCw, RotateCcw, Unlock } from 'lucide-react'
import type { SectionRevision } from '../lib/types'
import {
  acquireChapterLock,
  getChapterDiff,
  getChapterLock,
  listChapterRevisions,
  releaseChapterLock,
  renewChapterLock,
  restoreChapterRevision,
} from '../api/collaboration'
import { messageOf } from '../lib/http'
import { className, formatDate } from '../lib/format'
import { useQuery } from '../lib/hooks'
import { useAuth } from '../context/useAuth'
import { useToast } from '../context/useToast'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../components/Feedback'
import { Modal } from '../components/Modal'
import { RevisionDiff } from './RevisionDiff'

const CHANGE_LABEL: Record<string, string> = {
  edit: '手动编辑',
  restore: '版本恢复',
  generate: 'AI 生成',
}

function remainingText(expiresAt: string | null | undefined, now: number): string {
  if (!expiresAt) return '有效期未知'
  const deadline = new Date(expiresAt).getTime()
  if (Number.isNaN(deadline)) return '有效期未知'
  const ms = deadline - now
  if (ms <= 0) return '已过期'
  const total = Math.ceil(ms / 1000)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    return `剩余 ${hours} 小时 ${minutes % 60} 分`
  }
  return `剩余 ${minutes} 分 ${seconds} 秒`
}

/**
 * 章节级协作：编辑锁状态条 + 修订历史/差异/恢复入口。
 *
 * 锁只做协作提示，真正的并发安全仍由服务端保存时的 revision 校验兜底。
 * 修订恢复需要填写原因；当前章节有未保存修改时禁止恢复，避免静默覆盖。
 */
export function ChapterCollab({
  projectRef,
  nodeId,
  heading,
  canWrite,
  dirty,
  onRestored,
}: {
  /** 项目 ref；不能命名为 `ref`，React 会把字符串 ref 拦截并报错。 */
  projectRef: string
  nodeId: string
  heading: string
  canWrite: boolean
  dirty?: boolean
  onRestored?: () => void
}) {
  const { user } = useAuth()
  const toast = useToast()
  const lockQuery = useQuery(`chapter-lock:${projectRef}:${nodeId}`, () =>
    getChapterLock(projectRef, nodeId),
  )
  const [busy, setBusy] = useState<'acquire' | 'renew' | 'release' | null>(null)
  const [lockError, setLockError] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [activeRevision, setActiveRevision] = useState<SectionRevision | null>(null)
  const [restoreTarget, setRestoreTarget] = useState<SectionRevision | null>(null)
  const [restoreNote, setRestoreNote] = useState('')
  const [restoring, setRestoring] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const lock = lockQuery.data || null
  const isMine = Boolean(user?.id && lock?.user_id && lock.user_id === user.id)
  const revisionsQuery = useQuery(
    `revisions:${projectRef}:${nodeId}`,
    () => listChapterRevisions(projectRef, nodeId),
    { enabled: historyOpen },
  )
  const diffFrom = activeRevision ? Math.max(0, activeRevision.revision - 1) : 0
  const diffTo = activeRevision?.revision ?? 0
  const diffQuery = useQuery(
    `revision-diff:${projectRef}:${nodeId}:${diffFrom}:${diffTo}`,
    () => getChapterDiff(projectRef, nodeId, diffFrom, diffTo),
    { enabled: historyOpen && Boolean(activeRevision) },
  )

  useEffect(() => {
    setHistoryOpen(false)
    setActiveRevision(null)
    setRestoreTarget(null)
    setRestoreNote('')
    setActionError(null)
    setLockError(null)
  }, [nodeId])

  // 锁过期倒计时：只有存在带有效期的锁时才启动 1s 定时器。
  useEffect(() => {
    if (!lock?.expires_at) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [lock?.expires_at])

  const revisions = useMemo(
    () => [...(revisionsQuery.data || [])].sort((left, right) => right.revision - left.revision),
    [revisionsQuery.data],
  )

  const ownerLabel = isMine
    ? '你'
    : lock?.display_name || lock?.username || (lock ? '其他成员' : '—')

  const acquire = async () => {
    if (!canWrite || lock || busy) return
    setBusy('acquire')
    setLockError(null)
    try {
      const next = await acquireChapterLock(projectRef, nodeId)
      lockQuery.setData(next)
      toast.success('已获取编辑锁', '其他成员会看到你正在编辑本章。')
    } catch (error) {
      const message = messageOf(error)
      setLockError(message)
      toast.error('获取编辑锁失败', message)
    } finally {
      setBusy(null)
    }
  }

  const renew = async () => {
    if (!canWrite || !isMine || !lock?.lock_token || busy) return
    setBusy('renew')
    setLockError(null)
    try {
      const next = await renewChapterLock(projectRef, nodeId, lock.lock_token)
      lockQuery.setData(next)
      toast.success('编辑锁已续期', remainingText(next.expires_at, Date.now()))
    } catch (error) {
      const message = messageOf(error)
      setLockError(message)
      toast.error('续期编辑锁失败', message)
    } finally {
      setBusy(null)
    }
  }

  const release = async () => {
    if (!canWrite || !isMine || busy) return
    setBusy('release')
    setLockError(null)
    try {
      await releaseChapterLock(projectRef, nodeId)
      lockQuery.setData(null)
      toast.success('已释放编辑锁', '其他成员现在可以编辑本章。')
    } catch (error) {
      const message = messageOf(error)
      setLockError(message)
      toast.error('释放编辑锁失败', message)
    } finally {
      setBusy(null)
    }
  }

  const selectRevision = (revision: SectionRevision) => {
    setActiveRevision(revision)
    setActionError(null)
  }

  const restore = async () => {
    if (!canWrite || dirty || !restoreTarget || !restoreNote.trim() || restoring) return
    setRestoring(true)
    setActionError(null)
    try {
      await restoreChapterRevision(projectRef, nodeId, {
        revision: restoreTarget.revision,
        note: restoreNote.trim(),
      })
      toast.success(
        '已恢复历史版本',
        `r${restoreTarget.revision} 的内容已写入本章，并生成新的修订记录。`,
      )
      revisionsQuery.refresh()
      setRestoreTarget(null)
      setRestoreNote('')
      setActiveRevision(null)
      setHistoryOpen(false)
      onRestored?.()
    } catch (error) {
      const message = messageOf(error)
      setActionError(message)
      toast.error('恢复历史版本失败', message)
    } finally {
      setRestoring(false)
    }
  }

  return (
    <div className={className('chapter-collab', lock && !isMine && 'is-locked')}>
      <div className="chapter-collab-bar">
        <div className="chapter-collab-state" role="status">
          {lock ? (
            <>
              <Lock size={14} aria-hidden />
              <span>
                编辑锁：<strong>{ownerLabel}</strong>
                {isMine ? '（你）' : ''} · {remainingText(lock.expires_at, now)}
              </span>
            </>
          ) : (
            <>
              <LockOpen size={14} aria-hidden />
              <span>未锁定，开始写作前可先获取编辑锁。</span>
            </>
          )}
        </div>
        <div className="chapter-collab-actions">
          {!lock && (
            <Button
              size="sm"
              variant="secondary"
              icon={<Lock size={13} />}
              loading={busy === 'acquire'}
              disabled={!canWrite || Boolean(busy)}
              title={canWrite ? '获取本章编辑锁' : '只读成员不能获取编辑锁'}
              onClick={() => void acquire()}
            >
              获取编辑锁
            </Button>
          )}
          {lock && isMine && (
            <>
              <Button
                size="sm"
                variant="ghost"
                icon={<RefreshCw size={13} />}
                loading={busy === 'renew'}
                disabled={!canWrite || !lock.lock_token || Boolean(busy)}
                title={lock.lock_token ? '延长锁的有效期' : '服务端未返回锁令牌，无法续期'}
                onClick={() => void renew()}
              >
                续期
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<Unlock size={13} />}
                loading={busy === 'release'}
                disabled={!canWrite || Boolean(busy)}
                onClick={() => void release()}
              >
                释放
              </Button>
            </>
          )}
          {lock && !isMine && (
            <span className="chapter-collab-blocked">其他成员正在编辑本章</span>
          )}
          <Button
            size="sm"
            variant="ghost"
            icon={<History size={13} />}
            onClick={() => setHistoryOpen(true)}
          >
            修订历史
          </Button>
        </div>
      </div>
      {(lockError || lockQuery.error) && (
        <p className="collab-inline-error" role="alert">
          {lockError || lockQuery.error}（编辑锁不可用，不影响本章保存与复核）
        </p>
      )}

      <Modal
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        title={`修订历史 · ${heading}`}
        eyebrow="版本与恢复"
        size="lg"
      >
        <div className="revision-layout">
          <div className="revision-list-col">
            <div className="revision-toolbar">
              <span className="eyebrow">修订记录</span>
              <Button
                size="sm"
                variant="ghost"
                icon={<RefreshCw size={13} />}
                loading={revisionsQuery.refreshing}
                onClick={revisionsQuery.refresh}
              >
                刷新
              </Button>
            </div>

            {revisionsQuery.error && !revisions.length ? (
              <ErrorState
                message={revisionsQuery.error}
                onRetry={revisionsQuery.refresh}
                compact
              />
            ) : revisionsQuery.loading && !revisions.length ? (
              <div className="revision-loading" aria-busy="true">
                {Array.from({ length: 3 }).map((_, index) => (
                  <Skeleton key={index} width="100%" height={56} radius={7} />
                ))}
              </div>
            ) : revisions.length === 0 ? (
              <EmptyState
                icon={History}
                compact
                title="还没有修订记录"
                description="保存或生成正文后，这里会按版本保留快照。"
              />
            ) : (
              <ol className="revision-list" aria-label="章节修订列表">
                {revisions.map((revision) => {
                  const active = activeRevision?.revision === revision.revision
                  return (
                    <li
                      key={revision.id || revision.revision}
                      className={className('revision-item', active && 'is-active')}
                    >
                      <button
                        type="button"
                        className="revision-item-main"
                        onClick={() => selectRevision(revision)}
                      >
                        <span className="revision-no mono">r{revision.revision}</span>
                        <span className="revision-copy">
                          <strong>{CHANGE_LABEL[revision.change_type] || revision.change_type}</strong>
                          <span>
                            {revision.changed_by || '未知成员'} ·{' '}
                            {revision.created_at ? formatDate(revision.created_at) : '—'}
                          </span>
                          {revision.change_note && (
                            <span className="revision-note">{revision.change_note}</span>
                          )}
                        </span>
                      </button>
                      <div className="revision-item-actions">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => selectRevision(revision)}
                        >
                          查看差异
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          icon={<RotateCcw size={13} />}
                          disabled={!canWrite || Boolean(dirty) || restoring}
                          title={
                            dirty
                              ? '当前章节有未保存修改，请先保存或放弃后再恢复'
                              : !canWrite
                                ? '只读成员不能恢复历史版本'
                                : '恢复此版本'
                          }
                          onClick={() => {
                            setRestoreTarget(revision)
                            setRestoreNote('')
                            setActionError(null)
                          }}
                        >
                          恢复
                        </Button>
                      </div>
                    </li>
                  )
                })}
              </ol>
            )}
          </div>

          <div className="revision-detail-col">
            <RevisionDiff
              diff={diffQuery.data || null}
              loading={diffQuery.loading}
              error={diffQuery.error}
              onRetry={diffQuery.refresh}
            />
            {!activeRevision && (
              <p className="revision-hint">
                选择左侧版本的「查看差异」，对比它与上一版本的正文变化。
              </p>
            )}

            {restoreTarget && (
              <div className="revision-restore">
                <div className="eyebrow">恢复版本</div>
                <p>
                  将 <strong className="mono">r{restoreTarget.revision}</strong>{' '}
                  的正文恢复为当前章节，并生成一条新的修订记录。
                </p>
                <label className="revision-restore-field">
                  <span>恢复原因（必填）</span>
                  <input
                    className="input input--sm"
                    value={restoreNote}
                    onChange={(event) => setRestoreNote(event.target.value)}
                    placeholder="例如：误删了本章的服务承诺条款"
                    aria-label="恢复原因"
                  />
                </label>
                {dirty && (
                  <p className="revision-warning" role="alert">
                    当前章节有未保存修改，请先保存或放弃后再恢复。
                  </p>
                )}
                {actionError && (
                  <p className="collab-inline-error" role="alert">
                    {actionError}
                  </p>
                )}
                <div className="revision-restore-actions">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRestoreTarget(null)
                      setActionError(null)
                    }}
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    loading={restoring}
                    disabled={!canWrite || Boolean(dirty) || !restoreNote.trim()}
                    onClick={() => void restore()}
                  >
                    确认恢复
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="revision-modal-foot">
          <Badge tone="neutral" subtle>
            共 {revisions.length} 个版本
          </Badge>
          <span>恢复历史版本不会删除既有记录，只会新增一条恢复修订。</span>
        </div>
      </Modal>
    </div>
  )
}
