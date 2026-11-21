import { canManageProject } from '../../lib/permissions'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Download,
  EyeOff,
  FileSpreadsheet,
  LibraryBig,
  LockKeyhole,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import { applyBoq, cnyTotalWithTax, deleteBoq, exportBoq, getBoq, getPricePolicy, roundTo, setBoqPrice, uploadBoq } from '../../api/boq'
import { downloadBlob } from '../../api/generation'
import { ApiError, messageOf } from '../../lib/http'
import type { BidPackage, BoqAction, BoqPayload, BoqRow, BoqStatus, PricePolicy } from '../../lib/types'
import { className, formatDate, formatMoney, formatNumber } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { ErrorState, Skeleton } from '../../components/Feedback'
import { TabHeading } from '../../components/Workbench'
import { useAuth } from '../../context/useAuth'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'
import {
  BoqPackageBar,
  EnablePackageModal,
  ManagePackagesModal,
} from './BoqPackages'
import { loadBoqPackageState } from './boqPackageState'
import { PricePolicyPanel } from './BoqPricePolicy'
import { QuoteVersionsPanel } from './BoqVersions'
import { QuotaFillModal } from './BoqQuotaFill'

type BoqFilter =
  | 'all'
  | 'diff'
  | 'unpriced'
  | 'missing_in_ledger'
  | 'missing_in_boq'
  | 'ignored'

const FILTER_META: Array<{ id: BoqFilter; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'diff', label: '差异' },
  { id: 'unpriced', label: '未报价' },
  { id: 'missing_in_ledger', label: '清单有台账无' },
  { id: 'missing_in_boq', label: '台账有清单无' },
  { id: 'ignored', label: '已忽略' },
]

const STATUS_META: Record<string, { label: string; tone: 'success' | 'warn' | 'danger' | 'info' | 'neutral' }> = {
  matched: { label: '一致', tone: 'success' },
  qty_diff: { label: '数量差异', tone: 'warn' },
  unit_diff: { label: '单位差异', tone: 'warn' },
  spec_diff: { label: '规格差异', tone: 'info' },
  missing_in_ledger: { label: '清单有·台账无', tone: 'danger' },
  missing_in_boq: { label: '台账有·清单无', tone: 'neutral' },
}

const ACCEPT = '.xlsx,.xlsm'

const DIFF_STATUSES: BoqStatus[] = ['qty_diff', 'unit_diff', 'spec_diff']

function isDiffStatus(status: string): boolean {
  return (DIFF_STATUSES as string[]).includes(status)
}

function signedNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const rounded = Math.abs(value) < 0.0005 ? 0 : value
  const text = formatNumber(Math.abs(rounded))
  if (rounded === 0) return '0'
  return `${rounded > 0 ? '+' : '−'}${text}`
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

function diffNotes(row: BoqRow): string[] {
  const notes: string[] = []
  const diff = row.diff
  if (diff?.qty) {
    notes.push(
      `数量偏差 ${signedNumber(diff.qty.delta ?? null)}（清单 ${formatNumber(diff.qty.boq as number)} / 台账 ${formatNumber(diff.qty.ledger as number)}）`,
    )
  }
  if (diff?.unit) {
    notes.push(`单位不一致：清单「${diff.unit.boq || '—'}」/ 台账「${diff.unit.ledger || '—'}」`)
  }
  if (diff?.spec) {
    notes.push(`规格不一致：清单「${diff.spec.boq || '—'}」/ 台账「${diff.spec.ledger || '—'}」`)
  }
  if (row.status === 'missing_in_ledger') notes.push('台账中未找到对应材料')
  if (row.status === 'missing_in_boq') notes.push('台账材料未出现在清单中')
  return notes
}

export function BoqTab() {
  const { ref, canWrite, project, reloadProject } = useWorkspace()
  const { user, isSuperAdmin } = useAuth()
  const canReview = canManageProject(project)
  const toast = useToast()
  const confirm = useConfirm()

  const packagesQuery = useQuery(`boq-packages:${ref}`, () => loadBoqPackageState(ref))
  const packageState = packagesQuery.data
  const packagesSupported = packageState?.supported === true
  const packages = useMemo(
    () => (packagesSupported ? packageState?.packages ?? [] : []),
    [packagesSupported, packageState],
  )
  const packagesReady = packageState !== undefined

  const [selectedPackageId, setSelectedPackageId] = useState<string | null>(null)
  const packageId = useMemo(() => {
    if (!packagesSupported) return null
    if (selectedPackageId && packages.some((item) => item.id === selectedPackageId)) {
      return selectedPackageId
    }
    // 首次进入优先展示已有清单的包件，避免默认落在空包件上。
    const withSnapshot = packages.find((item) => item.has_boq)
    return withSnapshot?.id ?? packages[0]?.id ?? null
  }, [packagesSupported, packages, selectedPackageId])
  const currentPackage = packages.find((item) => item.id === packageId) ?? null

  const boqQuery = useQuery(
    `boq:${ref}:${packageId ?? 'single'}`,
    () => getBoq(ref, packageId),
    { enabled: packagesReady },
  )
  const data = boqQuery.data

  const policyQuery = useQuery(`price-policy:${ref}`, () => getPricePolicy(ref))

  const packageIdRef = useRef(packageId)
  useEffect(() => {
    packageIdRef.current = packageId
  }, [packageId])

  const [filter, setFilter] = useState<BoqFilter>('all')
  const [filterTouched, setFilterTouched] = useState(false)
  const [search, setSearch] = useState('')
  const [sheet, setSheet] = useState('')
  const [uploading, setUploading] = useState<{ name: string; percent: number } | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [pricingKey, setPricingKey] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [stale, setStale] = useState<string | null>(null)
  const [enableOpen, setEnableOpen] = useState(false)
  const [manageOpen, setManageOpen] = useState(false)
  const [quotaOpen, setQuotaOpen] = useState(false)

  // 切换包件等于切换一份独立清单：清空上份清单的视图状态，
  // 让默认筛选按新数据重新计算。
  useEffect(() => {
    setSearch('')
    setSheet('')
    setShowUpload(false)
    setStale(null)
    setFilterTouched(false)
    setQuotaOpen(false)
  }, [packageId, ref])

  // 首次加载或刷新后，若用户未主动筛选，存在数量/单位/规格差异时默认落在「差异」视图；
  // 仅有缺项时留在「全部」，避免默认视图为空（缺项有独立筛选项）。
  useEffect(() => {
    if (!data || filterTouched) return
    const summary = data.summary
    setFilter(summary.total_diffs > 0 ? 'diff' : 'all')
  }, [data, filterTouched])

  // 数据被重新拉取后清除过期提示。
  useEffect(() => {
    setStale(null)
  }, [data])

  const snapshot = data?.snapshot ?? null
  const summary = data?.summary
  const allRows = useMemo(() => data?.rows ?? [], [data])

  // 摘要里的调整后合计以后端为准；旧后端不返回时按当前策略在本地算一份预览。
  const policy = policyQuery.data ?? null
  const adjustedTotal = useMemo(() => {
    if (!summary) return null
    if (typeof summary.adjusted_total === 'number') return summary.adjusted_total
    if (!policy || !policy.enabled) return null
    return roundTo(summary.total_amount * policy.factor, policy.round_digits)
  }, [summary, policy])

  const rows = useMemo(() => {
    const query = search.trim().toLowerCase()
    return allRows.filter((row) => {
      if (filter === 'diff' && !isDiffStatus(row.status)) return false
      if (
        filter === 'unpriced' &&
        (row.boq_row === null || row.ignored || typeof row.price?.unit_price === 'number')
      )
        return false
      if (filter === 'missing_in_ledger' && row.status !== 'missing_in_ledger') return false
      if (filter === 'missing_in_boq' && row.status !== 'missing_in_boq') return false
      if (filter === 'ignored' && !row.ignored) return false
      if (query && !`${row.name} ${row.spec}`.toLowerCase().includes(query)) return false
      return true
    })
  }, [allRows, filter, search])

  const filterCounts = useMemo(() => {
    const s = summary
    return {
      all: allRows.length,
      diff: s ? s.qty_diff + s.unit_diff + s.spec_diff : 0,
      unpriced: s?.unpriced ?? 0,
      missing_in_ledger: s?.missing_in_ledger ?? 0,
      missing_in_boq: s?.missing_in_boq ?? 0,
      ignored: s?.ignored ?? 0,
    }
  }, [allRows, summary])

  const markPackageSnapshot = (id: string, hasBoq: boolean) => {
    packagesQuery.setData((current) =>
      current && current.supported
        ? {
            ...current,
            packages: current.packages.map((item) =>
              item.id === id ? { ...item, has_boq: hasBoq } : item,
            ),
          }
        : current,
    )
  }

  const uploadFile = async (file: File) => {
    if (!canWrite || uploading) return
    const extension = file.name.split('.').pop()?.toLowerCase()
    if (extension !== 'xlsx' && extension !== 'xlsm') {
      toast.warning('文件格式不支持', '清单对齐仅支持 .xlsx 或 .xlsm 工作簿。')
      return
    }
    const targetPackageId = packageId
    setUploading({ name: file.name, percent: 0 })
    try {
      const payload = await uploadBoq(
        ref,
        file,
        sheet,
        (percent) => setUploading({ name: file.name, percent }),
        targetPackageId,
      )
      if (packageIdRef.current === targetPackageId) {
        boqQuery.setData(payload)
        setSheet('')
        setShowUpload(false)
        setStale(null)
      }
      if (targetPackageId) markPackageSnapshot(targetPackageId, true)
      if (packagesSupported) packagesQuery.refresh()
      if (payload.snapshot?.warnings?.length) {
        toast.warning('清单已导入，但有解析提示', '部分单元格未能解析，请查看页顶提示。')
      } else {
        const scope = currentPackage ? `（${currentPackage.name}）` : ''
        toast.success(
          '清单已导入',
          `共解析 ${payload.summary.boq_rows} 条清单条目${scope}，已与共享台账逐行对齐。`,
        )
      }
    } catch (error) {
      toast.error(`「${file.name}」导入失败`, messageOf(error))
    } finally {
      setUploading(null)
    }
  }

  const startReupload = async () => {
    if (!canWrite || uploading) return
    const accepted = await confirm({
      title: '重新上传工程量清单？',
      description: '新文件会替换当前清单快照与全部对齐结果，台账材料保持不变。',
      confirmLabel: '重新上传',
      tone: 'warning',
    })
    if (!accepted) return
    setShowUpload(true)
  }

  const removeSnapshot = async () => {
    if (!canWrite) return
    const scope = currentPackage ? `包件「${currentPackage.name}」中` : ''
    const accepted = await confirm({
      title: '删除清单快照？',
      description: `将删除${scope}已上传的工程量清单、对齐与报价记录。台账材料由所有包件共享，不受影响，此操作不可撤销。`,
      confirmLabel: '删除清单',
      tone: 'danger',
    })
    if (!accepted) return
    const targetPackageId = packageId
    try {
      await deleteBoq(ref, targetPackageId)
      if (packageIdRef.current === targetPackageId) {
        setStale(null)
        boqQuery.refresh()
      }
      if (targetPackageId) markPackageSnapshot(targetPackageId, false)
      if (packagesSupported) packagesQuery.refresh()
      toast.success('已删除清单', '可随时重新上传该包件的工程量清单。')
    } catch (error) {
      toast.error('删除失败', messageOf(error))
    }
  }

  const runExport = async () => {
    if (!canWrite || exporting) return
    setExporting(true)
    try {
      const { blob, filename } = await exportBoq(ref, packageId)
      downloadBlob(blob, filename)
      toast.success('导出完成', `已开始下载 ${filename}`)
    } catch (error) {
      toast.error('导出响应表失败', messageOf(error))
    } finally {
      setExporting(false)
    }
  }

  const runAction = async (row: BoqRow, action: BoqAction) => {
    if (!canWrite || pendingKey || row.boq_row === null) return
    if (action === 'update') {
      const accepted = await confirm({
        title: '更新台账材料？',
        description: `将把台账中「${row.ledger?.name || row.name}」的数量、单位与规格更新为清单取值，名称保持不变。`,
        confirmLabel: '更新台账',
      })
      if (!accepted) return
    }
    if (action === 'add') {
      const accepted = await confirm({
        title: '加入台账？',
        description: `将按清单第 ${row.boq_row} 行新建台账材料「${row.name}」，来源会标注清单文件与行号。`,
        confirmLabel: '加入台账',
      })
      if (!accepted) return
    }
    setPendingKey(row.key)
    const targetPackageId = packageId
    try {
      const payload = await applyBoq(ref, {
        boq_row: row.boq_row,
        action,
        ...(targetPackageId ? { package_id: targetPackageId } : {}),
      })
      if (packageIdRef.current !== targetPackageId) return
      boqQuery.setData(payload)
      setStale(null)
      if (action === 'update') toast.success('台账已更新', `「${row.name}」已按清单取值更新。`)
      else if (action === 'add') toast.success('已加入台账', `「${row.name}」已按清单行新建。`)
      else if (action === 'ignore') toast.info('已忽略该条目', '此清单行不再参与对齐，可随时恢复。')
      else toast.info('已恢复对齐', '该清单行重新参与对齐。')
    } catch (error) {
      const isApi = error instanceof ApiError
      if (isApi && (error.status === 404 || error.status === 409)) {
        boqQuery.refresh()
      }
      if (isApi && error.status === 404) {
        setStale('清单快照可能已被更新，正在刷新最新对齐结果。请稍后重试该操作。')
      }
      toast.error(
        action === 'update'
          ? '更新台账失败'
          : action === 'add'
            ? '加入台账失败'
            : action === 'ignore'
              ? '忽略失败'
              : '恢复失败',
        messageOf(error),
      )
    } finally {
      setPendingKey(null)
    }
  }

  const runPrice = async (row: BoqRow, unitPrice: number | null) => {
    if (!canWrite || pricingKey || row.boq_row === null) return
    setPricingKey(row.key)
    const targetPackageId = packageId
    try {
      const payload = await setBoqPrice(ref, {
        boq_row: row.boq_row,
        unit_price: unitPrice,
        ...(targetPackageId ? { package_id: targetPackageId } : {}),
      })
      if (packageIdRef.current !== targetPackageId) return
      boqQuery.setData(payload)
      setStale(null)
    } catch (error) {
      const isApi = error instanceof ApiError
      if (isApi && error.status === 404) {
        boqQuery.refresh()
        setStale('清单快照可能已被更新，正在刷新最新报价结果。')
      }
      toast.error('保存单价失败', messageOf(error))
    } finally {
      setPricingKey(null)
    }
  }

  const clearFilters = () => {
    setSearch('')
    setFilter('all')
    setFilterTouched(true)
  }

  const handleQuotaApplied = (applied: number) => {
    boqQuery.refresh()
    setStale(null)
    if (applied > 0) {
      toast.success('企业定额已套用', `${applied} 条清单行写入了定额库单价。`)
    } else {
      toast.info('没有写入新的单价', '所选行可能已有报价，或清单缺少可写入的行。')
    }
  }

  const handlePolicySaved = (next: PricePolicy) => {
    policyQuery.setData(next)
  }

  const handleAdjusted = (payload: BoqPayload) => {
    boqQuery.setData(payload)
    setStale(null)
  }

  const handleVersionRestored = (payload: BoqPayload) => {
    boqQuery.setData(payload)
    setStale(null)
  }

  const retry = () => boqQuery.refresh()

  const matchPercent = summary ? Math.round((summary.match_rate || 0) * 100) : 0

  const addPackageToList = (pkg: BidPackage) => {
    packagesQuery.setData((current) =>
      current && current.supported
        ? { ...current, packages: [...current.packages, pkg] }
        : current,
    )
  }

  const handleFirstPackageCreated = (pkg: BidPackage) => {
    // 首包会自动收养旧快照；先按当前快照标记，避免选择器闪回「未上传」。
    addPackageToList({ ...pkg, has_boq: pkg.has_boq || Boolean(snapshot) })
    setSelectedPackageId(pkg.id)
    setEnableOpen(false)
    packagesQuery.refresh()
  }

  const handlePackageCreated = (pkg: BidPackage) => {
    // 新增包件不打断当前正在看的清单，用户可在管理弹窗里切换过去。
    // 列表刷新由管理弹窗的 onChanged 统一触发。
    addPackageToList(pkg)
  }

  const handlePackageDeleted = (id: string) => {
    const remaining = packages.filter((item) => item.id !== id)
    if (id === packageId) {
      const next = remaining.find((item) => item.has_boq) ?? remaining[0] ?? null
      setSelectedPackageId(next?.id ?? null)
    }
    packagesQuery.setData((current) =>
      current && current.supported
        ? { ...current, packages: current.packages.filter((item) => item.id !== id) }
        : current,
    )
  }

  const packageBusy = Boolean(uploading) || pendingKey !== null || pricingKey !== null

  return (
    <div className="tab-content boq-tab">
      <TabHeading
        eyebrow="清单对齐"
        title="工程量清单 · 双向对齐"
        description="上传招标方工程量清单，与台账材料逐行对齐。差异可以更新台账、补录材料或忽略；单价可用企业定额补齐、按系数调价，最后导出清单响应表。"
        actions={
          snapshot && !showUpload ? (
            <>
              <Button
                variant="secondary"
                size="sm"
                icon={<LibraryBig size={14} />}
                disabled={!canWrite}
                title={
                  !canWrite
                    ? '只读成员不能套用定额'
                    : '按企业定额库匹配清单行，预览后再勾选套用'
                }
                onClick={() => setQuotaOpen(true)}
              >
                定额套用
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon={<Download size={14} />}
                loading={exporting}
                disabled={!canWrite}
                title={!canWrite ? '只读成员不能导出文件' : '导出清单响应表工作簿'}
                onClick={() => void runExport()}
              >
                导出响应表
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon={<RefreshCw size={14} />}
                onClick={() => void startReupload()}
                disabled={!canWrite || Boolean(uploading)}
                title={!canWrite ? '只读成员不能上传文件' : '上传新清单会替换当前快照'}
              >
                重新上传
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              icon={<RefreshCw size={14} className={boqQuery.refreshing ? 'spin' : undefined} />}
              onClick={retry}
            >
              刷新
            </Button>
          )
        }
      />

      {packagesSupported && (
        <BoqPackageBar
          packages={packages}
          current={currentPackage}
          itemCount={snapshot?.item_count ?? null}
          canWrite={canWrite}
          busy={packageBusy}
          onSelect={setSelectedPackageId}
          onEnable={() => setEnableOpen(true)}
          onManage={() => setManageOpen(true)}
        />
      )}

      {stale && (
        <div className="notice notice--info" role="status">
          <RefreshCw size={17} className="spin" />
          <div>
            <strong>对齐结果需要刷新</strong>
            <p>{stale}</p>
          </div>
        </div>
      )}

      {boqQuery.error && !data ? (
        <section className="panel">
          <ErrorState message={boqQuery.error} onRetry={retry} />
        </section>
      ) : boqQuery.loading ? (
        <BoqSkeleton />
      ) : !snapshot ? (
        <UploadPanel
          uploading={uploading}
          sheet={sheet}
          onSheetChange={setSheet}
          onFile={(file) => void uploadFile(file)}
          canWrite={canWrite}
          packageName={currentPackage?.name}
        />
      ) : (
        <>
          {snapshot.warnings && snapshot.warnings.length > 0 && (
            <section className="parse-notes" aria-label="解析提示">
              <div className="parse-notes-head">
                <AlertCircle size={16} />
                <strong>解析提示</strong>
              </div>
              <ul>
                {snapshot.warnings.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            </section>
          )}

          {showUpload ? (
            <UploadPanel
              uploading={uploading}
              sheet={sheet}
              onSheetChange={setSheet}
              onFile={(file) => void uploadFile(file)}
              canWrite={canWrite}
              packageName={currentPackage?.name}
              replacing
              onCancel={() => setShowUpload(false)}
            />
          ) : (
            <>
              <SnapshotHeader
                filename={snapshot.filename}
                sheet={snapshot.sheet}
                headerRow={snapshot.header_row}
                itemCount={snapshot.item_count}
                createdAt={snapshot.created_at}
                exporting={exporting}
                canWrite={canWrite}
                onExport={() => void runExport()}
                onReupload={() => void startReupload()}
                onDelete={() => void removeSnapshot()}
              />

              <SummaryStrip
                summary={summary}
                matchPercent={matchPercent}
                policy={policy}
                adjustedTotal={adjustedTotal}
              />

              <PricePolicyPanel
                projectRef={ref}
                packageId={packageId}
                packageName={currentPackage?.name}
                policy={policy}
                loading={policyQuery.loading}
                error={policyQuery.error}
                canWrite={canWrite}
                totalAmount={summary?.total_amount ?? 0}
                pricedCount={summary?.priced ?? 0}
                onRetry={policyQuery.refresh}
                onSaved={handlePolicySaved}
                onAdjusted={handleAdjusted}
              />

              <QuoteVersionsPanel
                projectRef={ref}
                packageId={packageId}
                packageName={currentPackage?.name}
                canWrite={canWrite}
                canReview={canReview}
                approvalChain={project.approval_chain ?? []}
                packages={packages}
                currency={policy?.currency ?? null}
                isOwner={project.my_role === 'owner'}
                currentUserId={user?.id ?? ''}
                isSuperAdmin={isSuperAdmin}
                onChainSaved={() => void reloadProject().catch(() => undefined)}
                onRestored={handleVersionRestored}
              />

              <section className="ledger-card">
                <div className="ledger-toolbar">
                  <label className="search-field search-field--grow">
                    <Search size={15} aria-hidden />
                    <span className="sr-only">搜索清单条目</span>
                    <input
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder="搜索名称或规格"
                      aria-label="按名称或规格搜索清单条目"
                    />
                    {search && (
                      <button
                        type="button"
                        className="search-clear"
                        onClick={() => setSearch('')}
                        aria-label="清除搜索"
                      >
                        <X size={13} />
                      </button>
                    )}
                  </label>
                  <div className="segmented boq-segmented" role="group" aria-label="清单筛选">
                    {FILTER_META.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={className(filter === item.id && 'is-active')}
                        aria-pressed={filter === item.id}
                        onClick={() => {
                          setFilter(item.id)
                          setFilterTouched(true)
                        }}
                      >
                        {item.label}
                        <span className="boq-seg-count mono">{filterCounts[item.id]}</span>
                      </button>
                    ))}
                  </div>
                  <span className="toolbar-count">
                    {rows.length} / {allRows.length} 条
                  </span>
                </div>

                {rows.length === 0 ? (
                  <div className="ledger-empty">
                    <FileSpreadsheet size={22} />
                    <strong>没有匹配的清单条目</strong>
                    <p>当前筛选或搜索没有命中。全部条目均无差异时，可切换到「全部」查看。</p>
                    <Button size="sm" variant="secondary" onClick={clearFilters}>
                      清除筛选
                    </Button>
                  </div>
                ) : (
                  <div className="table-scroll">
                    <table className="data-table boq-table">
                      <thead>
                        <tr>
                          <th className="boq-col-seq">序号</th>
                          <th>名称</th>
                          <th>清单规格</th>
                          <th>清单单位</th>
                          <th className="num">清单数量</th>
                          <th className="num">单价（元）</th>
                          <th className="num">合价（元）</th>
                          <th>台账规格</th>
                          <th>台账单位</th>
                          <th className="num">台账数量</th>
                          <th>差异说明</th>
                          <th>状态</th>
                          <th className="boq-col-actions">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row) => (
                          <BoqTableRow
                            key={row.key}
                            row={row}
                            readOnly={!canWrite}
                            pending={pendingKey === row.key}
                            pricing={pricingKey === row.key}
                            onAction={(action) => void runAction(row, action)}
                            onPrice={(unitPrice) => void runPrice(row, unitPrice)}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <p className="ledger-footnote">
                对齐在每次打开时按当前台账重新计算。更新台账只写入数量、单位与规格；忽略的行保留原状态且可恢复。报价仅按你录入的单价乘以清单数量计算，程序不会自动生成价格；已忽略行不计入报价合计。
              </p>
            </>
          )}
        </>
      )}

      {packagesSupported && (
        <>
          <EnablePackageModal
            open={enableOpen}
            projectRef={ref}
            onClose={() => setEnableOpen(false)}
            onCreated={handleFirstPackageCreated}
          />
          <ManagePackagesModal
            open={manageOpen}
            projectRef={ref}
            packages={packages}
            currentId={packageId}
            readOnly={!canWrite}
            onClose={() => setManageOpen(false)}
            onChanged={() => packagesQuery.refresh()}
            onCreated={handlePackageCreated}
            onDeleted={handlePackageDeleted}
            onSelect={setSelectedPackageId}
          />
        </>
      )}

      <QuotaFillModal
        open={quotaOpen}
        projectRef={ref}
        packageId={packageId}
        packageName={currentPackage?.name}
        rows={allRows}
        readOnly={!canWrite}
        onClose={() => setQuotaOpen(false)}
        onApplied={handleQuotaApplied}
      />
    </div>
  )
}

// ---------------------------------------------------------------- upload panel

function UploadPanel({
  uploading,
  sheet,
  onSheetChange,
  onFile,
  canWrite,
  packageName,
  replacing,
  onCancel,
}: {
  uploading: { name: string; percent: number } | null
  sheet: string
  onSheetChange: (value: string) => void
  onFile: (file: File) => void
  canWrite: boolean
  packageName?: string
  replacing?: boolean
  onCancel?: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)

  const openPicker = () => {
    if (!canWrite || uploading) return
    inputRef.current?.click()
  }

  return (
    <div className="boq-upload">
      <div
        className={className(
          'dropzone',
          !canWrite && 'is-readonly',
          dragActive && 'is-dragging',
          uploading && 'is-uploading',
        )}
        role={canWrite ? 'button' : 'group'}
        tabIndex={canWrite ? 0 : undefined}
        aria-label={canWrite ? '上传工程量清单工作簿' : '只读成员不能上传文件'}
        aria-disabled={!canWrite}
        onClick={canWrite ? openPicker : undefined}
        onKeyDown={
          canWrite
            ? (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  openPicker()
                }
              }
            : undefined
        }
        onDragOver={
          canWrite
            ? (event) => {
                event.preventDefault()
                if (!uploading) setDragActive(true)
              }
            : undefined
        }
        onDragLeave={
          canWrite
            ? (event) => {
                if (event.currentTarget.contains(event.relatedTarget as Node)) return
                setDragActive(false)
              }
            : undefined
        }
        onDrop={
          canWrite
            ? (event) => {
                event.preventDefault()
                setDragActive(false)
                if (uploading) return
                const file = event.dataTransfer.files[0]
                if (file) onFile(file)
              }
            : undefined
        }
      >
        {canWrite && (
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            aria-label="选择工程量清单文件"
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file) onFile(file)
            }}
          />
        )}
        {!canWrite ? (
          <>
            <div className="dropzone-icon" aria-hidden>
              <LockKeyhole size={28} strokeWidth={1.5} />
            </div>
            <strong>只读成员不能上传清单</strong>
            <p>可以查看对齐结果与导出说明，如需导入请联系投标经理。</p>
          </>
        ) : uploading ? (
          <div className="upload-progress-state">
            <div className="upload-spinner">
              <RefreshCw size={22} className="spin" />
            </div>
            <strong>正在上传并解析清单</strong>
            <p className="upload-filename" title={uploading.name}>
              {uploading.name}
            </p>
            <p className="dropzone-hint">
              正在识别表头并解析条目，完成后自动与台账对齐。请保持页面打开。
            </p>
          </div>
        ) : (
          <>
            <div className="dropzone-icon" aria-hidden>
              <UploadCloud size={30} strokeWidth={1.5} />
            </div>
            <strong>
              {packageName ? '该包件尚未上传清单' : '拖拽工程量清单到这里'}
            </strong>
            <p>
              {packageName
                ? `「${packageName}」还没有清单，拖拽或点击选择文件`
                : '或点击选择文件，支持 .xlsx / .xlsm'}
            </p>
            <span className="dropzone-formats mono">XLSX · XLSM</span>
            <p className="dropzone-hint">
              {replacing
                ? '重新上传会替换当前清单快照与对齐结果，台账材料不受影响。'
                : packageName
                  ? '清单与报价只属于当前包件，台账材料由所有包件共享。程序会自动在前 30 行内识别表头。'
                  : '程序会自动在前 30 行内识别表头；重新上传会替换已有清单。'}
            </p>
          </>
        )}
      </div>

      <div className="boq-sheet-row">
        <label className="boq-sheet-field">
          <span>
            工作表名称
            <span className="boq-optional">可选</span>
          </span>
          <input
            value={sheet}
            onChange={(event) => onSheetChange(event.target.value)}
            placeholder="留空则自动选择数据最多的工作表"
            aria-label="工作表名称（可选）"
            disabled={!canWrite || Boolean(uploading)}
          />
        </label>
        {replacing && onCancel && (
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={Boolean(uploading)}>
            取消上传
          </Button>
        )}
        <p className="boq-sheet-hint">
          多工作表工作簿可在此填写工作表名；未填写时自动选择解析行数最多的一张。
        </p>
      </div>

      <div className="side-note side-note--quiet">
        <div className="side-note-head">
          <FileSpreadsheet size={15} />
          <strong>表头需要包含的列</strong>
        </div>
        <p>程序按列名自动识别，缺失列对齐精度会下降：</p>
        <div className="boq-chips">
          <span>名称</span>
          <span>规格型号</span>
          <span>单位</span>
          <span>数量</span>
          <span>序号（可选）</span>
          <span>备注（可选）</span>
        </div>
        <p className="boq-chip-hint">
          数量支持「1,200」「12套」「约 3.5」等写法；无法解析的取值会在解析提示中说明。
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- snapshot head

function SnapshotHeader({
  filename,
  sheet,
  headerRow,
  itemCount,
  createdAt,
  exporting,
  canWrite,
  onExport,
  onReupload,
  onDelete,
}: {
  filename: string
  sheet: string
  headerRow: number
  itemCount: number
  createdAt?: string | null
  exporting: boolean
  canWrite: boolean
  onExport: () => void
  onReupload: () => void
  onDelete: () => void
}) {
  return (
    <section className="boq-snapshot">
      <span className="boq-snapshot-icon" aria-hidden>
        <FileSpreadsheet size={20} />
      </span>
      <div className="boq-snapshot-main">
        <strong title={filename}>{filename}</strong>
        <div className="boq-snapshot-meta">
          <span>工作表 {sheet || '—'}</span>
          <span>表头第 {headerRow} 行</span>
          <span>{itemCount} 条清单条目</span>
          {createdAt && <span title={formatDate(createdAt)}>上传于 {formatDate(createdAt, false)}</span>}
        </div>
      </div>
      <div className="boq-snapshot-actions">
        <Button
          variant="secondary"
          size="sm"
          icon={<Download size={14} />}
          loading={exporting}
          disabled={!canWrite}
          title={!canWrite ? '只读成员不能导出文件' : '导出清单响应表工作簿'}
          onClick={onExport}
        >
          导出响应表
        </Button>
        <Button
          variant="ghost"
          size="sm"
          icon={<RefreshCw size={14} />}
          disabled={!canWrite}
          title={!canWrite ? '只读成员不能上传文件' : '上传新清单会替换当前快照'}
          onClick={onReupload}
        >
          重新上传
        </Button>
        <Button
          variant="ghost"
          size="sm"
          icon={<Trash2 size={14} />}
          disabled={!canWrite}
          title={!canWrite ? '只读成员不能删除清单' : '删除清单快照，台账不受影响'}
          onClick={onDelete}
        >
          删除
        </Button>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- summary

function SummaryStrip({
  summary,
  matchPercent,
  policy,
  adjustedTotal,
}: {
  summary?: {
    boq_rows: number
    matched: number
    total_diffs: number
    missing_in_ledger: number
    missing_in_boq: number
    ignored: number
    priced: number
    unpriced: number
    quantity_missing: number
    total_amount: number
    tax_amount?: number | null
    total_with_tax?: number | null
    cny_total_with_tax?: number | null
  }
  matchPercent: number
  policy: PricePolicy | null
  adjustedTotal: number | null
}) {
  const s = summary
  const aligned = s ? s.matched + s.total_diffs : 0
  const quoted = s ? s.priced + s.unpriced : 0
  const showAdjusted = Boolean(policy?.enabled) && adjustedTotal !== null
  // 税率来自策略；税额/含税合计以后端 summary 为准，旧后端缺字段时按税率本地推算。
  const taxRate = policy?.tax_rate ?? 0
  const taxAmount =
    s?.tax_amount ?? (s && taxRate > 0 ? roundTo(s.total_amount * taxRate, 2) : null)
  const totalWithTax =
    s?.total_with_tax ??
    (s && taxAmount !== null ? roundTo(s.total_amount + taxAmount, 2) : null)
  const showTax = taxRate > 0 && taxAmount !== null && totalWithTax !== null
  // 非人民币币种再给一行人民币含税合计：后端折算优先，旧后端按总额 × 汇率本地兜底。
  const cnyTotal = cnyTotalWithTax(
    s?.cny_total_with_tax,
    totalWithTax,
    policy?.exchange_rate ?? 1,
  )
  const showCny = Boolean(policy && policy.currency !== 'CNY' && cnyTotal !== null)
  return (
    <section className="ledger-summary boq-summary">
      <div className="ledger-summary-main">
        <div className="ledger-summary-title">
          <span className="eyebrow">对齐覆盖</span>
          <strong>
            {aligned}
            <span className="muted"> / {s?.boq_rows ?? 0} 条已对上台账</span>
          </strong>
        </div>
        <div className="ledger-progress">
          <div className="ledger-progress-meta">
            <span>匹配率</span>
            <span className="mono">{matchPercent}%</span>
          </div>
          <div className="ledger-progress-track" aria-hidden>
            <span style={{ width: `${matchPercent}%` }} />
          </div>
        </div>
      </div>
      <dl className="ledger-figures boq-figures">
        <div>
          <dt>清单条目</dt>
          <dd>{s?.boq_rows ?? 0}</dd>
        </div>
        <div>
          <dt>已匹配</dt>
          <dd>{s?.matched ?? 0}</dd>
        </div>
        <div className={s?.total_diffs ? 'is-warn' : undefined}>
          <dt>差异</dt>
          <dd>{s?.total_diffs ?? 0}</dd>
        </div>
        <div className={s?.missing_in_ledger ? 'is-danger' : undefined}>
          <dt>清单有·台账无</dt>
          <dd>{s?.missing_in_ledger ?? 0}</dd>
        </div>
        <div>
          <dt>台账有·清单无</dt>
          <dd>{s?.missing_in_boq ?? 0}</dd>
        </div>
        <div>
          <dt>已忽略</dt>
          <dd>{s?.ignored ?? 0}</dd>
        </div>
      </dl>
      <div className="boq-quote-line">
        <span className="eyebrow">报价</span>
        <span>
          已报价 <strong>{s?.priced ?? 0}</strong> / {quoted} 条
        </span>
        <span className={s?.unpriced ? 'is-warn' : undefined}>
          未报价 <strong>{s?.unpriced ?? 0}</strong>
        </span>
        {Boolean(s?.quantity_missing) && (
          <span className="is-warn">缺数量 {s?.quantity_missing} 条</span>
        )}
        <span className="boq-quote-total">
          合计 <strong className="mono">¥{money(s?.total_amount ?? 0)}</strong>
        </span>
        {showAdjusted && policy && (
          <span className="boq-quote-total boq-quote-total--adjusted">
            <span className="boq-adjust-tag">×{formatMoney(policy.factor, 2)}</span>
            调整后合计
            <strong className="mono">
              ¥{formatMoney(adjustedTotal, policy.round_digits)}
            </strong>
          </span>
        )}
        {showTax && (
          <>
            <span className="boq-quote-total boq-quote-total--tax">
              税额
              <strong className="mono">¥{money(taxAmount)}</strong>
            </span>
            <span className="boq-quote-total boq-quote-total--tax">
              <span className="boq-tax-tag">
                {formatMoney(taxRate * 100, 2)}%
              </span>
              含税合计
              <strong className="mono">¥{money(totalWithTax)}</strong>
            </span>
          </>
        )}
        {showCny && policy && (
          <span
            className="boq-quote-total boq-quote-total--cny"
            title={`汇率 1 ${policy.currency} = ${formatMoney(
              policy.exchange_rate,
              4,
            )} CNY，按含税合计折算`}
          >
            <span className="boq-cny-tag">≈ ¥CNY</span>
            合计
            <strong className="mono">¥{money(cnyTotal)}</strong>
          </span>
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- price cell

function PriceEditor({
  row,
  readOnly,
  saving,
  onSave,
}: {
  row: BoqRow
  readOnly: boolean
  saving: boolean
  onSave: (unitPrice: number | null) => void
}) {
  const value = row.price?.unit_price ?? null
  const [draft, setDraft] = useState(value === null ? '' : String(value))

  useEffect(() => {
    setDraft(value === null ? '' : String(value))
  }, [row.key, value])

  if (row.boq_row === null) return <span className="subtle">—</span>
  if (readOnly) {
    return value === null ? (
      <span className="subtle">—</span>
    ) : (
      <span className="boq-cell-mono">¥{money(value)}</span>
    )
  }

  const commit = () => {
    const trimmed = draft.trim()
    if (!trimmed) {
      if (value !== null) onSave(null)
      return
    }
    const parsed = Number(trimmed)
    if (!Number.isFinite(parsed) || parsed < 0) {
      setDraft(value === null ? '' : String(value))
      return
    }
    if (value !== null && Math.abs(parsed - value) < 0.00005) return
    onSave(parsed)
  }

  return (
    <input
      className={className('boq-price-input', saving && 'is-saving')}
      type="number"
      min="0"
      step="0.01"
      inputMode="decimal"
      value={draft}
      placeholder="0.00"
      disabled={saving}
      aria-label={`${row.name || `第 ${row.boq_row} 行`} 单价`}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault()
          event.currentTarget.blur()
        } else if (event.key === 'Escape') {
          setDraft(value === null ? '' : String(value))
          event.currentTarget.blur()
        }
      }}
    />
  )
}

// ---------------------------------------------------------------- table row

function BoqTableRow({
  row,
  readOnly,
  pending,
  pricing,
  onAction,
  onPrice,
}: {
  row: BoqRow
  readOnly: boolean
  pending: boolean
  pricing: boolean
  onAction: (action: BoqAction) => void
  onPrice: (unitPrice: number | null) => void
}) {
  const meta = STATUS_META[row.status] || { label: row.status, tone: 'neutral' as const }
  const notes = diffNotes(row)
  const hasBoqItem = row.boq_row !== null
  const canUpdate = !readOnly && Boolean(row.ledger) && hasBoqItem && row.status !== 'missing_in_boq'
  const canAdd = !readOnly && row.status === 'missing_in_ledger' && hasBoqItem
  const canToggleIgnore = !readOnly && row.status === 'missing_in_ledger' && hasBoqItem
  const keyTitle = row.key

  return (
    <tr className={className('boq-row', row.ignored && 'boq-row--ignored')}>
      <td className="boq-col-seq">
        <span className="boq-cell-mono" title={keyTitle}>
          {row.seq || '—'}
        </span>
      </td>
      <td>
        <strong>{row.name || '—'}</strong>
        {row.spec && <span className="cell-sub">{row.spec}</span>}
        {row.note && <span className="cell-sub">备注：{row.note}</span>}
      </td>
      <td>{row.spec || <span className="subtle">—</span>}</td>
      <td>{row.unit || <span className="subtle">—</span>}</td>
      <td className="num">{row.qty === null ? <span className="subtle">—</span> : <span className="boq-cell-mono">{formatNumber(row.qty)}</span>}</td>
      <td className="num boq-price-cell">
        <PriceEditor row={row} readOnly={readOnly} saving={pricing} onSave={onPrice} />
      </td>
      <td className="num">
        {row.price?.amount !== null && row.price?.amount !== undefined ? (
          <span className="boq-cell-mono">¥{money(row.price.amount)}</span>
        ) : row.price?.unit_price !== null && row.price?.unit_price !== undefined ? (
          <span className="subtle" title="清单数量缺失，无法计算合价">缺数量</span>
        ) : (
          <span className="subtle">—</span>
        )}
      </td>
      <td>{row.ledger?.spec || <span className="subtle">—</span>}</td>
      <td>{row.ledger?.unit || <span className="subtle">—</span>}</td>
      <td className="num">
        {row.ledger && row.ledger.qty !== null && row.ledger.qty !== undefined ? (
          <span className="boq-cell-mono">{formatNumber(row.ledger.qty)}</span>
        ) : (
          <span className="subtle">—</span>
        )}
      </td>
      <td>
        {notes.length ? (
          <div className="boq-diff-lines">
            {notes.map((note, index) => (
              <span
                key={index}
                className={className(
                  'boq-diff-line',
                  row.status === 'missing_in_ledger' || row.status === 'missing_in_boq'
                    ? 'is-missing'
                    : undefined,
                )}
              >
                {note}
              </span>
            ))}
          </div>
        ) : (
          <span className="subtle">—</span>
        )}
      </td>
      <td>
        <div className="boq-status-cell">
          <Badge tone={meta.tone} subtle>
            {meta.label}
          </Badge>
          {row.ignored && (
            <Badge tone="neutral" subtle>
              已忽略
            </Badge>
          )}
          {row.match_score !== null && row.match_score !== undefined && row.match_score > 0 && (
            <span className="boq-match-sub mono">匹配 {Math.round(row.match_score * 100)}%</span>
          )}
        </div>
      </td>
      <td className="boq-actions-cell">
        {readOnly ? (
          <span className="subtle">—</span>
        ) : canUpdate || canAdd || canToggleIgnore ? (
          <div className="boq-row-actions">
            {canUpdate && (
              <Button
                size="sm"
                variant="secondary"
                icon={<Pencil size={12} />}
                loading={pending}
                disabled={pending}
                title="按清单取值更新台账材料的数量、单位与规格"
                onClick={() => onAction('update')}
              >
                更新台账
              </Button>
            )}
            {canAdd && (
              <Button
                size="sm"
                variant="secondary"
                icon={<Plus size={12} />}
                loading={pending}
                disabled={pending}
                title="按清单行在台账新建材料"
                onClick={() => onAction('add')}
              >
                加入台账
              </Button>
            )}
            {canToggleIgnore &&
              (row.ignored ? (
                <button
                  type="button"
                  className="boq-text-action"
                  disabled={pending}
                  title="恢复该行参与对齐"
                  onClick={() => onAction('restore')}
                >
                  <RotateCcw size={12} aria-hidden />
                  恢复
                </button>
              ) : (
                <button
                  type="button"
                  className="boq-text-action"
                  disabled={pending}
                  title="忽略该清单行，不参与对齐"
                  onClick={() => onAction('ignore')}
                >
                  <EyeOff size={12} aria-hidden />
                  忽略
                </button>
              ))}
          </div>
        ) : (
          <span className="subtle">—</span>
        )}
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------- loading skeleton

function BoqSkeleton() {
  return (
    <div className="boq-skeleton-wrap" aria-busy="true">
      <section className="boq-snapshot">
        <Skeleton width={40} height={40} radius={8} />
        <div className="boq-snapshot-main">
          <Skeleton width={220} height={14} />
          <div className="boq-snapshot-meta">
            <Skeleton width={200} height={10} />
          </div>
        </div>
        <Skeleton width={220} height={28} radius={6} />
      </section>
      <section className="ledger-summary">
        <Skeleton width={180} height={12} />
        <div className="ledger-figures">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index}>
              <Skeleton width={64} height={10} />
              <Skeleton width={40} height={18} />
            </div>
          ))}
        </div>
      </section>
      <section className="ledger-card">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={index} className="boq-row-skeleton">
            <Skeleton width={30} height={12} />
            <Skeleton width="26%" height={14} />
            <Skeleton width="10%" height={12} />
            <Skeleton width="8%" height={12} />
            <Skeleton width="8%" height={12} />
            <Skeleton width={64} height={20} radius={999} />
          </div>
        ))}
      </section>
    </div>
  )
}
