import { useEffect, useState } from 'react'
import { ChevronRight, History, RotateCcw, Save, Trash2 } from 'lucide-react'
import type { BoqPayload, BoqQuoteVersion } from '../../lib/types'
import {
  createBoqVersion,
  deleteBoqVersion,
  listBoqVersions,
  messageOf,
  restoreBoqVersion,
} from '../../lib/api'
import { className, formatDate, formatMoney } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Button } from '../../components/Button'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'

const NAME_MAX = 80
const NOTE_MAX = 200

/** 优先展示含税合计；旧版本快照只有原价合计。 */
function versionTotal(version: BoqQuoteVersion): { label: string; value: number } {
  if (
    typeof version.summary.total_with_tax === 'number' &&
    Number.isFinite(version.summary.total_with_tax)
  ) {
    return { label: '含税合计', value: version.summary.total_with_tax }
  }
  return { label: '合计', value: version.summary.total_amount }
}

/**
 * 「报价版本」折叠面板：保存当前单价快照、恢复或删除历史版本。
 * 版本跟随当前包件（package_id），只记录单价与汇总，不改动台账与对齐结果。
 */
export function QuoteVersionsPanel({
  projectRef,
  packageId,
  packageName,
  canWrite,
  onRestored,
}: {
  projectRef: string
  packageId: string | null
  packageName?: string
  canWrite: boolean
  onRestored: (payload: BoqPayload) => void
}) {
  const toast = useToast()
  const confirm = useConfirm()

  const versionsQuery = useQuery(
    `boq-versions:${projectRef}:${packageId ?? 'single'}`,
    () => listBoqVersions(projectRef, packageId),
  )
  const versions = versionsQuery.data ?? []

  const [name, setName] = useState('')
  const [nameTouched, setNameTouched] = useState(false)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  // 切换包件等于切换到另一组版本：清空尚未保存的名称与备注。
  useEffect(() => {
    setName('')
    setNameTouched(false)
    setNote('')
    setBusyId(null)
  }, [packageId, projectRef])

  const defaultName = `版本 ${versions.length + 1}`
  const effectiveName = nameTouched ? name : defaultName
  const busy = saving || busyId !== null

  const save = async () => {
    if (!canWrite || busy) return
    const trimmed = effectiveName.trim()
    if (!trimmed) {
      toast.warning('请填写版本名称', '例如「投标报价 V1」，便于之后恢复。')
      return
    }
    setSaving(true)
    try {
      const created = await createBoqVersion(projectRef, {
        name: trimmed,
        note,
        package_id: packageId,
      })
      versionsQuery.setData((current) => [created, ...(current ?? [])])
      setName('')
      setNameTouched(false)
      setNote('')
      const scope = packageName ? `包件「${packageName}」` : '当前清单'
      toast.success('报价版本已保存', `${scope}已记录「${created.name}」的单价快照。`)
    } catch (error) {
      toast.error('保存报价版本失败', messageOf(error))
    } finally {
      setSaving(false)
    }
  }

  const restore = async (version: BoqQuoteVersion) => {
    if (!canWrite || busy) return
    const accepted = await confirm({
      title: `恢复到「${version.name}」？`,
      description:
        '将把该版本保存的单价写回当前清单；当前尚未保存为版本的报价改动会被覆盖。',
      note: '台账材料与清单对齐结果不受影响，恢复后可以再保存一个新版本。',
      confirmLabel: '恢复版本',
      tone: 'warning',
    })
    if (!accepted) return
    setBusyId(version.id)
    try {
      const payload = await restoreBoqVersion(projectRef, version.id)
      onRestored(payload)
      toast.success('已恢复报价版本', `「${version.name}」的单价已写回当前清单。`)
    } catch (error) {
      toast.error('恢复报价版本失败', messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (version: BoqQuoteVersion) => {
    if (!canWrite || busy) return
    const accepted = await confirm({
      title: `删除版本「${version.name}」？`,
      description: '删除后该版本的单价快照将无法恢复，当前清单不受影响。',
      confirmLabel: '删除版本',
      tone: 'danger',
    })
    if (!accepted) return
    setBusyId(version.id)
    try {
      await deleteBoqVersion(projectRef, version.id)
      versionsQuery.setData((current) =>
        (current ?? []).filter((item) => item.id !== version.id),
      )
      toast.info('版本已删除', `「${version.name}」已从报价版本列表移除。`)
    } catch (error) {
      toast.error('删除版本失败', messageOf(error))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <details className="price-policy boq-versions">
      <summary>
        <span className="price-policy-title">
          <History size={13} aria-hidden />
          <strong>报价版本</strong>
          <span className="price-policy-summary">
            {versionsQuery.loading
              ? '正在读取版本…'
              : versions.length > 0
                ? `${versions.length} 个版本 · 可恢复历史报价`
                : '保存当前单价快照'}
          </span>
        </span>
        <span className="price-policy-caret" aria-hidden>
          <ChevronRight size={14} />
        </span>
      </summary>

      <div className="price-policy-body">
        <div className="boq-version-save">
          <label className="price-policy-field">
            <span>版本名称</span>
            <input
              className="input input--sm"
              value={effectiveName}
              maxLength={NAME_MAX}
              disabled={!canWrite || saving}
              onChange={(event) => {
                setName(event.target.value)
                setNameTouched(true)
              }}
              aria-label="版本名称"
            />
            <small>默认「版本 N」，最多 {NAME_MAX} 字</small>
          </label>
          <label className="price-policy-field">
            <span>备注</span>
            <input
              className="input input--sm"
              value={note}
              maxLength={NOTE_MAX}
              placeholder="可选，例如「含税报价」「下浮 5%」"
              disabled={!canWrite || saving}
              onChange={(event) => setNote(event.target.value)}
              aria-label="版本备注（可选）"
            />
            <small>可选，最多 {NOTE_MAX} 字</small>
          </label>
          <Button
            size="sm"
            variant="secondary"
            icon={<Save size={13} />}
            loading={saving}
            disabled={!canWrite || busy}
            title={
              !canWrite ? '只读成员不能保存版本' : '记录当前包件的单价与汇总快照'
            }
            onClick={() => void save()}
          >
            保存当前为版本
          </Button>
        </div>

        {versionsQuery.loading ? (
          <p className="price-policy-message">正在读取报价版本…</p>
        ) : versionsQuery.error && !versionsQuery.data ? (
          <div className="price-policy-message">
            <span>暂时读不到报价版本，可能是服务版本较旧：当前清单仍可正常报价。</span>
            <button type="button" className="text-btn" onClick={versionsQuery.refresh}>
              重试
            </button>
          </div>
        ) : versions.length === 0 ? (
          <p className="price-policy-message">
            还没有保存过版本。保存后会记录当前全部单价与汇总，可随时恢复。
          </p>
        ) : (
          <ul className="boq-version-list">
            {versions.map((version) => {
              const total = versionTotal(version)
              return (
                <li key={version.id} className="boq-version-item">
                  <div className="boq-version-main">
                    <strong>{version.name}</strong>
                    <span className="boq-version-meta">
                      <span>{formatDate(version.created_at)}</span>
                      <span>
                        {total.label}{' '}
                        <span className="mono">¥{formatMoney(total.value)}</span>
                      </span>
                      <span>{version.price_count} 条已报价</span>
                      {version.package_id === null && <span>项目级</span>}
                    </span>
                    {version.note && <p className="boq-version-note">{version.note}</p>}
                  </div>
                  <div
                    className={className(
                      'boq-version-actions',
                      busyId === version.id && 'is-busy',
                    )}
                  >
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={<RotateCcw size={12} />}
                      loading={busyId === version.id}
                      disabled={!canWrite || busy}
                      title={
                        !canWrite
                          ? '只读成员不能恢复版本'
                          : '把该版本保存的单价写回当前清单'
                      }
                      onClick={() => void restore(version)}
                    >
                      恢复
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 size={12} />}
                      disabled={!canWrite || busy}
                      title={!canWrite ? '只读成员不能删除版本' : '删除该版本快照'}
                      onClick={() => void remove(version)}
                    >
                      删除
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}

        <p className="price-policy-note">
          版本只记录当前包件的单价与汇总；恢复会覆盖当前单价，但不改动台账与清单对齐结果。最多保留 20 个版本。
        </p>
      </div>
    </details>
  )
}
