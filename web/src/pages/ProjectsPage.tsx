import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Settings2,
  Download,
  Eye,
  FileStack,
  FolderOpen,
  LayoutTemplate,
  ListChecks,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { usePackages, useQuery, useDocumentTitle } from '../lib/hooks'
import {
  batchCheck,
  batchExport,
  deleteProject,
  downloadBatchZip,
  downloadBlob,
  getBatchJob,
  getProjects,
  messageOf,
} from '../lib/api'
import type {
  BatchFormat,
  BatchJob,
  Project,
  ProjectStats,
  PackageInfo,
} from '../lib/types'
import {
  formatRelative,
  formatDate,
  percent,
  projectRoleMeta,
  projectStatus,
  className,
} from '../lib/format'
import { Badge, BidSectionBadge } from '../components/Badge'
import { Button, IconButton } from '../components/Button'
import { EmptyState, ErrorState, ProgressBar, Skeleton } from '../components/Feedback'
import { NewProjectModal } from '../components/NewProjectModal'
import { EditProjectModal } from '../components/EditProjectModal'
import { DuplicateProjectModal } from '../components/DuplicateProjectModal'
import { ProjectProfileModal } from '../components/ProjectProfileModal'
import { InstantiateTemplateModal } from '../components/InstantiateTemplateModal'
import { useToast } from '../context/useToast'
import { useConfirm } from '../context/useConfirm'
import { useHelp } from '../context/useHelp'
import { useAuth } from '../context/useAuth'

const WORKFLOW_STEPS = ['导入', '解析', '校订事实', '大纲', '正文', '核对', '导出'] as const

const BATCH_LIMIT = 50

const FORMAT_OPTIONS: Array<{ value: BatchFormat; label: string; hint: string }> = [
  { value: 'docx', label: 'Word · docx', hint: '正文成稿，含封面与目录' },
  { value: 'md', label: 'Markdown · md', hint: '纯文本正文，便于二次编辑' },
  { value: 'xlsx', label: 'Excel · xlsx', hint: '事实台账清单，无需正文' },
]

type SortKey = 'updated' | 'name' | 'progress'

type BatchKind = 'check' | 'export'

interface ActiveBatch {
  id: string
  kind: BatchKind
  status: string
  done: number
  total: number
  okCount: number
  failedCount: number
  progress: number
  message: string
  error?: string
}

interface BatchReport {
  kind: BatchKind
  job: BatchJob
}

interface BatchReportRow {
  key: string
  projectId: string
  name: string
  ok: boolean
  issues?: number
  blocking?: number
  detail?: string
}

export function ProjectsPage() {
  useDocumentTitle(null)
  const navigate = useNavigate()
  const toast = useToast()
  const confirm = useConfirm()
  const { openHelp } = useHelp()
  const { canWrite, user } = useAuth()
  const projectsQuery = useQuery('projects', getProjects)
  const { packages, loading: packagesLoading, error: packagesError, refresh: refreshPackages } =
    usePackages()
  const [modalOpen, setModalOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('updated')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [sectionFilter, setSectionFilter] = useState('all')
  const [exportFormat, setExportFormat] = useState<BatchFormat>('docx')
  const [batch, setBatch] = useState<ActiveBatch | null>(null)
  const [report, setReport] = useState<BatchReport | null>(null)
  const [reportOpen, setReportOpen] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [duplicating, setDuplicating] = useState<Project | null>(null)
  const [profileTarget, setProfileTarget] = useState<Project | null>(null)
  const [instantiateOpen, setInstantiateOpen] = useState(false)
  const selectAllRef = useRef<HTMLInputElement>(null)

  const projects = useMemo(() => projectsQuery.data || [], [projectsQuery.data])
  const packageMap = useMemo(() => {
    const map = new Map<string, PackageInfo>()
    packages.forEach((pkg) => map.set(pkg.name, pkg))
    return map
  }, [packages])

  const totals = useMemo(() => {
    return projects.reduce(
      (accumulator, project) => {
        const stats = normaliseListStats(project)
        accumulator.missing += stats.missing_required
        accumulator.issues += stats.issues_open
        accumulator.written += stats.written
        return accumulator
      },
      { missing: 0, issues: 0, written: 0 },
    )
  }, [projects])

  const sections = useMemo(() => {
    const values = new Set<string>()
    projects.forEach((project) => {
      const value = (project.bid_section || '').trim()
      if (value) values.add(value)
    })
    return Array.from(values).sort((left, right) => left.localeCompare(right, 'zh-CN'))
  }, [projects])

  const hasUnsectioned = useMemo(
    () => projects.some((project) => !(project.bid_section || '').trim()),
    [projects],
  )

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    const result = projects.filter((project) => {
      const section = (project.bid_section || '').trim()
      if (sectionFilter === 'none' && section) return false
      if (sectionFilter.startsWith('section:') && section !== sectionFilter.slice(8)) {
        return false
      }
      if (!query) return true
      const packageTitle = packageMap.get(project.package)?.title || ''
      return [
        project.name,
        project.buyer,
        project.package,
        packageTitle,
        project.id,
        section,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query))
    })
    return result.sort((a, b) => {
      if (sortKey === 'name') return a.name.localeCompare(b.name, 'zh-CN')
      if (sortKey === 'progress') {
        const left = normaliseListStats(a)
        const right = normaliseListStats(b)
        return progressOf(right) - progressOf(left)
      }
      const left = new Date(a.updated_at || a.created_at || 0).getTime() || 0
      const right = new Date(b.updated_at || b.created_at || 0).getTime() || 0
      return right - left
    })
  }, [projects, search, sortKey, sectionFilter, packageMap])

  const batchBusy = batch?.status === 'queued' || batch?.status === 'running'
  const selectedCount = selected.size
  const allVisibleSelected =
    filtered.length > 0 && filtered.every((project) => selected.has(project.id))
  const someVisibleSelected = filtered.some((project) => selected.has(project.id))

  // Keep the selection aligned with the server list: a deleted project must not
  // keep a phantom count in the batch bar.
  useEffect(() => {
    setSelected((current) => {
      if (!current.size) return current
      const ids = new Set(projects.map((project) => project.id))
      const next = new Set(Array.from(current).filter((id) => ids.has(id)))
      return next.size === current.size ? current : next
    })
  }, [projects])

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = !allVisibleSelected && someVisibleSelected
    }
  }, [allVisibleSelected, someVisibleSelected])

  // A renamed section can remove the active filter option; fall back to "all".
  useEffect(() => {
    if (sectionFilter === 'all' || sectionFilter === 'none') return
    const exists = sections.some((section) => `section:${section}` === sectionFilter)
    if (!exists) setSectionFilter('all')
  }, [sectionFilter, sections])

  // Poll the batch job at a coarse cadence; the server only knows per-project
  // granularity, so finer polling would just add noise.
  useEffect(() => {
    if (!batch) return
    if (batch.status !== 'queued' && batch.status !== 'running') return
    const jobId = batch.id
    const kind = batch.kind
    let cancelled = false
    let finished = false
    let errors = 0

    const tick = async () => {
      try {
        const job = await getBatchJob(jobId)
        if (cancelled) return
        errors = 0
        const terminal = job.status === 'done' || job.status === 'failed'
        setBatch((current) =>
          current && current.id === jobId
            ? {
                ...current,
                status: job.status,
                done: job.done,
                total: job.total || current.total,
                okCount: job.okCount,
                failedCount: job.failedCount,
                progress: job.progress,
                message: job.message || current.message,
                error: job.error,
              }
            : current,
        )
        if (!terminal || finished) return
        finished = true
        window.clearInterval(timer)

        if (job.status === 'failed') {
          toast.error(
            kind === 'check' ? '批量核对失败' : '批量导出失败',
            job.error || job.message || '请稍后重试。',
          )
          return
        }

        const result = job.result
        setReport({ kind, job })
        setReportOpen(result.failed > 0)

        if (kind === 'check') {
          setBatch(null)
          const title = `核对完成：成功 ${result.ok} / 失败 ${result.failed}`
          if (result.failed > 0) {
            toast.warning(title, '下方已列出每个项目的失败原因。')
          } else {
            toast.success(title, '项目列表的问题计数已更新。')
          }
          projectsQuery.refresh()
          return
        }

        if (result.ok === 0) {
          setBatch(null)
          toast.error(
            '没有可导出的项目',
            result.failed > 0 ? '所选项目都失败了，详见导出结果。' : '所选项目还没有可导出的内容。',
          )
          return
        }
        const title = `导出完成：成功 ${result.ok} / 失败 ${result.failed}`
        const description = `正在下载打包文件（${result.ok} 个项目）…`
        if (result.failed > 0) toast.warning(title, description)
        else toast.success(title, description)
        // Keep the job state until the blob lands so the effect is not torn
        // down mid-download; the bar simply reports completion.
        try {
          const { blob, filename } = await downloadBatchZip(jobId)
          downloadBlob(blob, filename)
        } catch (error) {
          toast.error('导出包下载失败', messageOf(error))
        }
        if (!cancelled) setBatch(null)
      } catch (error) {
        if (cancelled) return
        errors += 1
        if (errors < 3) return
        window.clearInterval(timer)
        const message = messageOf(error)
        setBatch((current) =>
          current && current.id === jobId
            ? { ...current, status: 'failed', error: message }
            : current,
        )
        toast.error('无法读取批量任务进度', message)
      }
    }

    const timer = window.setInterval(() => void tick(), 1600)
    void tick()
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
    // Poll per job id only; status transitions must not restart the interval.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.id])

  const toggleSelect = (id: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectVisible = () => {
    setSelected((current) => {
      const next = new Set(current)
      if (allVisibleSelected) filtered.forEach((project) => next.delete(project.id))
      else filtered.forEach((project) => next.add(project.id))
      return next
    })
  }

  const clearFilters = () => {
    setSearch('')
    setSectionFilter('all')
  }

  const startBatch = async (kind: BatchKind) => {
    if (!canWrite || batchBusy) return
    const ids = projects.filter((project) => selected.has(project.id)).map((project) => project.id)
    if (!ids.length) return
    if (ids.length > BATCH_LIMIT) {
      toast.error('一次最多处理 50 个项目', '请减少选择后再试。')
      return
    }
    const formatLabel =
      FORMAT_OPTIONS.find((option) => option.value === exportFormat)?.label || exportFormat
    const accepted =
      kind === 'check'
        ? await confirm({
            title: `批量核对 ${ids.length} 个项目？`,
            description: (
              <>
                将逐个运行合规核对，并更新每个项目的<strong>问题数量与阻断项</strong>。
                任务在后台运行，完成后在本页提示结果。
              </>
            ),
            note: '核对不消耗点数；每个项目的成功或失败原因会列在结果里。',
            confirmLabel: '开始核对',
          })
        : await confirm({
            title: `批量导出 ${ids.length} 个项目？`,
            description: (
              <>
                逐个导出为 <strong>{formatLabel}</strong>，完成后自动打包成一个 zip 下载。
                请保持本页打开，直到下载开始。
              </>
            ),
            note: '没有正文的项目会失败，失败原因会记录在结果里，其余项目照常导出。',
            confirmLabel: '开始导出',
          })
    if (!accepted) return

    try {
      const { job_id } =
        kind === 'check' ? await batchCheck(ids) : await batchExport(ids, exportFormat)
      setReport(null)
      setReportOpen(false)
      setBatch({
        id: job_id,
        kind,
        status: 'queued',
        done: 0,
        total: ids.length,
        okCount: 0,
        failedCount: 0,
        progress: 0,
        message: '已加入队列…',
      })
      toast.info(
        kind === 'check' ? '批量核对已开始' : '批量导出已开始',
        kind === 'check'
          ? `${ids.length} 个项目在后台核对，完成后会列出结果。`
          : `${ids.length} 个项目在后台导出，完成后会自动下载 zip。`,
      )
    } catch (error) {
      toast.error(
        kind === 'check' ? '无法开始批量核对' : '无法开始批量导出',
        messageOf(error),
      )
    }
  }

  const redownloadZip = async () => {
    if (!report || report.kind !== 'export') return
    try {
      const { blob, filename } = await downloadBatchZip(report.job.id)
      downloadBlob(blob, filename)
    } catch (error) {
      toast.error('导出包下载失败', messageOf(error))
    }
  }

  const handleDelete = async (project: Project) => {
    if (!canWrite) return
    const accepted = await confirm({
      title: '删除这个项目？',
      description: (
        <>
          即将删除 <strong>「{project.name}」</strong> 及其源文件索引、事实台账、目录与正文。
        </>
      ),
      note: '删除后无法恢复。招标文件原件不会被修改。',
      confirmLabel: '删除项目',
      tone: 'danger',
    })
    if (!accepted) return
    setDeleting(project.id)
    try {
      await deleteProject(project.id)
      toast.success('项目已删除', `「${project.name}」已从本机移除。`)
      projectsQuery.refresh()
    } catch (error) {
      toast.error('删除失败', messageOf(error))
    } finally {
      setDeleting(null)
    }
  }

  const reportRows = useMemo<BatchReportRow[]>(() => {
    if (!report) return []
    const result = report.job.result
    if (report.kind === 'check') {
      return result.results.map((item) => ({
        key: `check-${item.project_id}`,
        projectId: item.project_id,
        name: item.name || item.project_id,
        ok: item.ok,
        issues: item.issues,
        blocking: item.blocking,
        detail: item.error,
      }))
    }
    const files = result.files.length
      ? result.files
      : result.results
          .filter((item) => item.ok)
          .map((item) => ({
            project_id: item.project_id,
            name: item.name,
            arcname: item.arcname || '',
          }))
    const failures = result.failedItems.length
      ? result.failedItems
      : result.results
          .filter((item) => !item.ok)
          .map((item) => ({
            project_id: item.project_id,
            name: item.name,
            error: item.error || '',
          }))
    return [
      ...files.map((file) => ({
        key: `file-${file.project_id}`,
        projectId: file.project_id,
        name: file.name || file.project_id,
        ok: true,
        detail: file.arcname,
      })),
      ...failures.map((item) => ({
        key: `fail-${item.project_id}`,
        projectId: item.project_id,
        name: item.name || item.project_id,
        ok: false,
        detail: item.error,
      })),
    ]
  }, [report])

  const batchPercent = batch
    ? Math.max(
        batch.progress,
        batch.total > 0 ? Math.round((batch.done / batch.total) * 100) : 0,
      )
    : 0
  const showBulkBar = canWrite && (selectedCount > 0 || Boolean(batch))

  return (
    <div className="projects-page">
      <section className="projects-hero">
        <div className="hero-copy">
          <div className="eyebrow eyebrow--line">投标文件工作台 · 本地优先</div>
          <h1 className="hero-title">
            把招标文件，
            <br />
            变成<span className="hero-em">可交付的标书</span>。
          </h1>
          <p className="hero-lede">
            从解析、事实台账、大纲到正文与合规核对，一条链路完成。
            文件与数据都留在本机，不发送遥测。
          </p>
          <div className="hero-actions">
            {canWrite ? (
              <Button
                variant="primary"
                size="lg"
                icon={<Plus size={17} />}
                onClick={() => setModalOpen(true)}
              >
                新建项目
              </Button>
            ) : (
              <span className="hero-readonly">
                <Eye size={15} />
                只读成员 · 仅查看
              </span>
            )}
            <Button variant="ghost" size="lg" onClick={openHelp}>
              查看工作流
            </Button>
          </div>
          <div className="hero-trust" aria-label="产品特性">
            <span>
              <ShieldCheck size={14} /> 本地优先
            </span>
            <span>
              <CheckCircle2 size={14} /> 零遥测
            </span>
            <span>
              <FileStack size={14} /> 事实可追溯
            </span>
          </div>
        </div>

        <aside className="process-sheet" aria-label="标书生产流程">
          <div className="process-sheet-head">
            <div>
              <span className="process-kicker">标书生产 · 01—07</span>
              <strong>标书生产流程</strong>
            </div>
            <span className="mini-seal" aria-hidden>
              标
            </span>
          </div>
          <ol className="process-list">
            {WORKFLOW_STEPS.map((step, index) => (
              <li key={step}>
                <span className="process-no">{String(index + 1).padStart(2, '0')}</span>
                <span>{step}</span>
                {index < WORKFLOW_STEPS.length - 1 && <i aria-hidden />}
              </li>
            ))}
          </ol>
          <div className="process-sheet-foot">
            <span>缺失必填不开放生成</span>
            <span className="mono">DOCX · PDF · XLSX</span>
          </div>
        </aside>
      </section>

      <section className="projects-section" aria-labelledby="projects-heading">
        <div className="section-head projects-head">
          <div>
            <div className="eyebrow">本机项目</div>
            <h2 id="projects-heading">项目</h2>
            <p className="section-sub">
              {projectsQuery.loading
                ? '正在读取本机项目…'
                : `共 ${projects.length} 个项目 · 数据保存在本机`}
            </p>
          </div>
          <div className="projects-tools">
            <label className="search-field">
              <Search size={15} aria-hidden />
              <span className="sr-only">搜索项目</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索项目、招标人或规则包"
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
            {sections.length > 0 && (
              <label className="select-field select-field--compact">
                <span className="sr-only">按标段筛选</span>
                <select
                  value={sectionFilter}
                  onChange={(event) => setSectionFilter(event.target.value)}
                >
                  <option value="all">全部标段</option>
                  {sections.map((section) => (
                    <option key={section} value={`section:${section}`}>
                      {section}
                    </option>
                  ))}
                  {hasUnsectioned && <option value="none">未设置标段</option>}
                </select>
              </label>
            )}
            <label className="select-field select-field--compact">
              <span className="sr-only">排序方式</span>
              <select
                value={sortKey}
                onChange={(event) => setSortKey(event.target.value as SortKey)}
              >
                <option value="updated">最近更新</option>
                <option value="progress">进度优先</option>
                <option value="name">名称排序</option>
              </select>
            </label>
            {/* R23 从模板创建仅需登录（无需模板成员关系）；全局只读角色被平台写保护拦截，故隐藏入口。 */}
            {canWrite && (
              <Button
                variant="secondary"
                icon={<LayoutTemplate size={15} />}
                onClick={() => setInstantiateOpen(true)}
              >
                从模板创建
              </Button>
            )}
            {canWrite ? (
              <Button
                variant="secondary"
                icon={<Plus size={15} />}
                onClick={() => setModalOpen(true)}
              >
                新建项目
              </Button>
            ) : (
              <span className="projects-readonly-chip">
                <Eye size={13} /> {user?.role_label || '只读成员'}
              </span>
            )}
          </div>
        </div>

        {!canWrite && (
          <div className="readonly-banner" role="status">
            <Eye size={15} />
            <strong>只读成员</strong>
            <span>
              可以查看现有项目、台账与正文，不能新建、复制、删除、上传、编辑、生成、核对或导出。
            </span>
          </div>
        )}

        {projects.length > 0 && (
          <div className="project-totals" aria-label="项目汇总">
            <div>
              <span className="total-label">项目</span>
              <strong>{projects.length}</strong>
            </div>
            <div>
              <span className="total-label">已写章节</span>
              <strong>{totals.written}</strong>
            </div>
            <div className={totals.missing > 0 ? 'is-warn' : undefined}>
              <span className="total-label">缺失必填</span>
              <strong>{totals.missing}</strong>
            </div>
            <div className={totals.issues > 0 ? 'is-warn' : undefined}>
              <span className="total-label">未关闭问题</span>
              <strong>{totals.issues}</strong>
            </div>
          </div>
        )}

        {showBulkBar && (
          <div
            className={className('bulk-bar', batchBusy && 'is-busy')}
            role="region"
            aria-label="批量操作"
          >
            <div className="bulk-bar-row">
              <div className="bulk-summary">
                <span className="bulk-count">{selectedCount}</span>
                <span className="bulk-summary-copy">
                  <strong>个项目已选</strong>
                  <span className="bulk-summary-sub">
                    {batch?.status === 'failed'
                      ? '上一次批量任务失败，可以重新发起'
                      : batchBusy
                        ? '任务在后台运行，完成后会提示结果'
                        : '核对不扣点数 · 导出打包为一个 zip'}
                  </span>
                </span>
                {!allVisibleSelected && filtered.length > 0 && (
                  <button
                    type="button"
                    className="text-btn bulk-select-all"
                    onClick={toggleSelectVisible}
                    disabled={Boolean(batchBusy)}
                  >
                    全选当前 {filtered.length} 项
                  </button>
                )}
              </div>
              <div className="bulk-actions">
                <Button
                  variant="secondary"
                  icon={<ListChecks size={15} />}
                  onClick={() => void startBatch('check')}
                  disabled={!selectedCount || Boolean(batchBusy)}
                >
                  批量核对
                </Button>
                <label className="select-field select-field--compact bulk-format">
                  <span className="sr-only">导出格式</span>
                  <select
                    value={exportFormat}
                    onChange={(event) => setExportFormat(event.target.value as BatchFormat)}
                    disabled={batchBusy}
                    title={
                      FORMAT_OPTIONS.find((option) => option.value === exportFormat)?.hint
                    }
                  >
                    {FORMAT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  variant="primary"
                  icon={<Download size={15} />}
                  onClick={() => void startBatch('export')}
                  disabled={!selectedCount || Boolean(batchBusy)}
                >
                  批量导出
                </Button>
                {selectedCount > 0 && (
                  <button
                    type="button"
                    className="text-btn bulk-clear"
                    onClick={() => setSelected(new Set())}
                    disabled={Boolean(batchBusy)}
                  >
                    <X size={13} /> 清除选择
                  </button>
                )}
              </div>
            </div>

            {(batchBusy || batch?.status === 'failed') && batch && (
              <div
                className={className('bulk-progress', batch.status === 'failed' && 'is-error')}
                role="status"
                aria-live="polite"
              >
                <div className="bulk-progress-head">
                  <span className="bulk-progress-label">
                    {batch.status === 'failed'
                      ? '任务失败'
                      : batch.kind === 'check'
                        ? '正在批量核对'
                        : '正在批量导出'}
                  </span>
                  {batch.status !== 'failed' && (
                    <span className="mono bulk-progress-count">
                      {batch.done}/{batch.total}
                    </span>
                  )}
                  <span className="bulk-progress-msg">
                    {batch.status === 'failed' ? batch.error || batch.message : batch.message}
                  </span>
                  {batch.status === 'failed' && (
                    <button
                      type="button"
                      className="text-btn"
                      onClick={() => setBatch(null)}
                    >
                      关闭
                    </button>
                  )}
                </div>
                {batch.status !== 'failed' && (
                  <ProgressBar
                    value={batchPercent}
                    thin
                    label={batch.kind === 'check' ? '批量核对进度' : '批量导出进度'}
                  />
                )}
              </div>
            )}
          </div>
        )}

        {report && (
          <section className="batch-report" aria-label="批量任务结果">
            <div className="batch-report-head">
              <div className="batch-report-copy">
                <span className="eyebrow">
                  {report.kind === 'check' ? '批量核对' : '批量导出'}
                </span>
                <h3>{report.kind === 'check' ? '核对结果' : '导出结果'}</h3>
                <p className="batch-report-sub">
                  共 {report.job.result.total || reportRows.length} 个项目 · 成功{' '}
                  <strong>{report.job.result.ok}</strong> · 失败{' '}
                  <strong className={report.job.result.failed > 0 ? 'is-warn' : undefined}>
                    {report.job.result.failed}
                  </strong>
                </p>
              </div>
              <div className="batch-report-actions">
                {report.kind === 'export' && report.job.result.files.length > 0 && (
                  <Button
                    size="sm"
                    variant="secondary"
                    icon={<Download size={14} />}
                    onClick={() => void redownloadZip()}
                  >
                    重新下载 zip
                  </Button>
                )}
                <button
                  type="button"
                  className="text-btn"
                  onClick={() => setReportOpen((value) => !value)}
                  aria-expanded={reportOpen}
                >
                  {reportOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {reportOpen ? '收起逐项结果' : `逐项结果（${reportRows.length}）`}
                </button>
                <IconButton label="关闭批量结果" onClick={() => setReport(null)}>
                  <X size={14} />
                </IconButton>
              </div>
            </div>
            {reportOpen && (
              <ul className="batch-report-list">
                {reportRows.length === 0 && (
                  <li className="batch-report-empty">没有逐项结果。</li>
                )}
                {reportRows.map((row) => (
                  <li
                    key={row.key}
                    className={className('batch-report-item', !row.ok && 'is-failed')}
                  >
                    <Link
                      to={`/app/projects/${row.projectId}`}
                      className="batch-report-name"
                      title={row.name}
                    >
                      {row.name}
                    </Link>
                    <span className="batch-report-state">
                      {row.ok ? (
                        <>
                          {row.issues === undefined ? (
                            <Badge tone="success" subtle>
                              已导出
                            </Badge>
                          ) : row.blocking ? (
                            <Badge tone="danger" subtle>
                              阻断 {row.blocking}
                            </Badge>
                          ) : row.issues ? (
                            <Badge tone="warn" subtle>
                              问题 {row.issues}
                            </Badge>
                          ) : (
                            <Badge tone="success" subtle>
                              无问题
                            </Badge>
                          )}
                          <span className="batch-report-detail">
                            {row.issues === undefined
                              ? row.detail
                              : row.issues > 0
                                ? `共 ${row.issues} 条问题${
                                    row.blocking ? ` · ${row.blocking} 条阻断` : ''
                                  }`
                                : ''}
                          </span>
                        </>
                      ) : (
                        <>
                          <Badge tone="danger" subtle>
                            失败
                          </Badge>
                          <span className="batch-report-detail">
                            {row.detail || '未知错误'}
                          </span>
                        </>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {projectsQuery.error && !projectsQuery.loading ? (
          <div className="panel">
            <ErrorState message={projectsQuery.error} onRetry={projectsQuery.refresh} />
          </div>
        ) : projectsQuery.loading ? (
          <ProjectListSkeleton selectable={canWrite} />
        ) : projects.length === 0 ? (
          <div className="panel panel--empty">
            <EmptyState
              icon={FolderOpen}
              title={canWrite ? '还没有项目' : '还没有可查看的项目'}
              description={
                canWrite
                  ? '新建一个项目，导入招标文件。系统会解析出事实台账，再带你走完大纲、正文、核对与导出。'
                  : '当前账号是只读成员，暂无可查看的项目。请联系超级管理员或投标经理创建项目。'
              }
              actions={
                canWrite ? (
                  <>
                    <Button
                      variant="primary"
                      icon={<Plus size={15} />}
                      onClick={() => setModalOpen(true)}
                    >
                      新建项目并导入招标文件
                    </Button>
                    <Button variant="ghost" onClick={openHelp}>
                      看看流程
                    </Button>
                  </>
                ) : (
                  <Button variant="ghost" onClick={openHelp}>
                    了解权限与流程
                  </Button>
                )
              }
            />
            <div className="empty-note">
              <FileStack size={15} />
              支持 PDF、DOCX、TXT、XLSX；扫描版 PDF 需先做 OCR。
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="panel panel--empty">
            <EmptyState
              icon={Search}
              compact
              title="没有匹配的项目"
              description="试试项目名称、招标人、规则包名称，或换一个标段筛选。"
              actions={
                <Button variant="secondary" onClick={clearFilters}>
                  清除筛选
                </Button>
              }
            />
          </div>
        ) : (
          <div className="project-table" role="table" aria-label="项目列表">
            <div className="project-table-head" role="row">
              <span className="cell-check" role="columnheader">
                {canWrite && (
                  <input
                    ref={selectAllRef}
                    type="checkbox"
                    className="row-check"
                    checked={allVisibleSelected}
                    onChange={toggleSelectVisible}
                    aria-label="全选当前列表"
                    title="全选当前列表"
                  />
                )}
              </span>
              <span role="columnheader">项目</span>
              <span role="columnheader">规则包 / 标包</span>
              <span role="columnheader">状态</span>
              <span role="columnheader">招标人</span>
              <span role="columnheader">编写进度</span>
              <span role="columnheader">待办</span>
              <span role="columnheader">最近更新</span>
              <span role="columnheader" className="col-actions-head">
                操作
              </span>
            </div>
            {filtered.map((project) => (
              <ProjectRow
                key={project.id}
                project={project}
                packageTitle={packageMap.get(project.package)?.title}
                deleting={deleting === project.id}
                canWrite={canWrite}
                selectable={canWrite}
                selected={selected.has(project.id)}
                onToggleSelect={() => toggleSelect(project.id)}
                onDelete={() => handleDelete(project)}
                onEdit={() => setEditing(project)}
                onDuplicate={() => setDuplicating(project)}
                onProfile={() => setProfileTarget(project)}
              />
            ))}
            <div className="project-table-foot">
              <span>
                显示 {filtered.length} / {projects.length} 个项目
                {selectedCount > 0 && ` · 已选 ${selectedCount}`}
              </span>
              <button type="button" className="text-btn" onClick={projectsQuery.refresh}>
                <RefreshCw size={13} /> 刷新列表
              </button>
            </div>
          </div>
        )}
      </section>

      <NewProjectModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        packages={packages}
        packagesLoading={packagesLoading}
        packagesError={packagesError}
        onRetryPackages={refreshPackages}
        onCreated={(project) => {
          setModalOpen(false)
          toast.success('项目已创建', '先导入招标文件，系统会自动解析事实台账。')
          projectsQuery.refresh()
          navigate(`/app/projects/${project.id}/sources`, {
            state: { justCreated: true },
          })
        }}
      />

      <EditProjectModal
        project={editing}
        isOwner={editing?.my_role === 'owner' || editing?.my_role === 'admin'}
        isTemplate={Boolean(editing?.is_template)}
        onClose={() => setEditing(null)}
        onSaved={(project) => {
          setEditing(null)
          toast.success('项目信息已更新', `「${project.name}」已保存。`)
          projectsQuery.refresh()
        }}
      />

      <ProjectProfileModal
        project={profileTarget}
        onClose={() => setProfileTarget(null)}
      />
      <DuplicateProjectModal
        project={duplicating}
        onClose={() => setDuplicating(null)}
        onDuplicated={(project) => {
          setDuplicating(null)
          toast.success('已创建副本', `「${project.name}」已就绪，源项目不受影响。`)
          projectsQuery.refresh()
          navigate(`/app/projects/${project.id}`)
        }}
      />

      <InstantiateTemplateModal
        open={instantiateOpen}
        onClose={() => setInstantiateOpen(false)}
        onCreated={(project) => {
          setInstantiateOpen(false)
          toast.success('已从模板创建项目', `「${project.name}」已就绪，模板不受影响。`)
          projectsQuery.refresh()
          navigate(`/app/projects/${project.id}`)
        }}
      />
    </div>
  )
}

function normaliseListStats(project: Project): ProjectStats {
  return {
    sources: project.stats?.sources ?? 0,
    facts_total: project.stats?.facts_total ?? 0,
    facts_filled: project.stats?.facts_filled ?? 0,
    missing_required: project.stats?.missing_required ?? 0,
    outline: project.stats?.outline ?? 0,
    written: project.stats?.written ?? 0,
    issues_open: project.stats?.issues_open ?? 0,
  }
}

function progressOf(stats: ProjectStats): number {
  return percent(stats.written, stats.outline)
}

function ProjectRow({
  project,
  packageTitle,
  deleting,
  canWrite,
  selectable,
  selected,
  onToggleSelect,
  onDelete,
  onEdit,
  onDuplicate,
  onProfile,
}: {
  project: Project
  packageTitle?: string
  deleting: boolean
  canWrite: boolean
  selectable: boolean
  selected: boolean
  onToggleSelect: () => void
  onDelete: () => void
  onEdit: () => void
  onDuplicate: () => void
  onProfile: () => void
}) {
  const stats = normaliseListStats(project)
  const status = projectStatus(project.status)
  const progress = progressOf(stats)
  const factProgress = percent(stats.facts_filled, stats.facts_total)
  const ready = stats.missing_required === 0 && stats.issues_open === 0 && stats.written > 0
  // Project deletion is owner/admin only; older servers omit `my_role`.
  const canDelete =
    canWrite &&
    (!project.my_role || project.my_role === 'owner' || project.my_role === 'admin')
  const canEdit = canWrite && project.my_role !== 'viewer'
  // R22 复制与编辑同权限：owner/editor/admin 可复制，viewer 隐藏入口；
  // 旧后端省略 my_role 时按可写处理（与 canEdit 一致）。
  const canDuplicate = canWrite && project.my_role !== 'viewer'

  return (
    <div
      className={className('project-row', selected && 'is-selected')}
      role="row"
    >
      <div className="cell-check" role="cell">
        {selectable && (
          <input
            type="checkbox"
            className="row-check"
            checked={selected}
            onChange={onToggleSelect}
            onClick={(event) => event.stopPropagation()}
            aria-label={`选择项目 ${project.name}`}
          />
        )}
      </div>
      <div className="cell-project" role="cell">
        <div className="project-name-row">
          <Link to={`/app/projects/${project.id}`} className="project-name">
            {project.name}
          </Link>
          <BidSectionBadge value={project.bid_section} />
          {project.is_template && (
            <Badge tone="accent" subtle title="项目模板：全员可见，可从模板创建新项目">
              模板
            </Badge>
          )}
          {project.my_role && (
            <Badge tone={projectRoleMeta(project.my_role).tone} subtle>
              {projectRoleMeta(project.my_role).label}
            </Badge>
          )}
        </div>
        <span className="project-id mono" title={project.number || undefined}>
          {project.number ? `项目编号 ${project.number}` : '项目编号未提取'}
        </span>
      </div>
      <div className="cell-package" role="cell">
        <span className="package-name">{packageTitle || project.package}</span>
        <span className="package-code mono">{project.package}</span>
      </div>
      <div className="cell-status" role="cell">
        <Badge tone={status.tone} dot>
          {status.label}
        </Badge>
      </div>
      <div className="cell-buyer" role="cell">
        {project.buyer || <span className="muted">未提取</span>}
      </div>
      <div className="cell-progress" role="cell">
        <div className="progress-line">
          <strong>
            {stats.written}
            <span className="muted"> / {stats.outline} 章</span>
          </strong>
          <span className="mono subtle">{progress}%</span>
        </div>
        <ProgressBar value={progress} thin tone={progress >= 100 ? 'success' : 'accent'} />
        <span className="progress-sub">
          事实 {stats.facts_filled}/{stats.facts_total} · {factProgress}%
        </span>
      </div>
      <div className="cell-todo" role="cell">
        <div className="todo-chips">
          {stats.missing_required > 0 && (
            <Badge tone="danger" subtle>
              <AlertTriangle size={11} /> 缺 {stats.missing_required} 必填
            </Badge>
          )}
          {stats.issues_open > 0 && (
            <Badge tone="warn" subtle>
              问题 {stats.issues_open}
            </Badge>
          )}
          {ready && (
            <Badge tone="success" subtle>
              <CheckCircle2 size={11} /> 就绪
            </Badge>
          )}
          {!ready && stats.missing_required === 0 && stats.issues_open === 0 && (
            <span className="subtle">—</span>
          )}
        </div>
      </div>
      <div className="cell-updated" role="cell">
        <span title={formatDate(project.updated_at || project.created_at)}>
          {formatRelative(project.updated_at || project.created_at)}
        </span>
      </div>
      <div className="cell-actions" role="cell">
        <Link
          to={`/app/projects/${project.id}`}
          className="row-open"
          aria-label={`打开项目 ${project.name}`}
        >
          打开 <ArrowUpRight size={14} />
        </Link>
        {canEdit && (
          <>
            <span className="action-divider" aria-hidden />
            <IconButton label={`编辑项目 ${project.name}`} onClick={onEdit}>
              <Pencil size={14} />
            </IconButton>
          </>
        )}
        {canEdit && (
          <>
            <span className="action-divider" aria-hidden />
            <IconButton
              label={`全局设定 ${project.name}`}
              onClick={onProfile}
            >
              <Settings2 size={14} />
            </IconButton>
          </>
        )}
        {canDuplicate && (
          <>
            <span className="action-divider" aria-hidden />
            <IconButton label={`复制项目 ${project.name}`} onClick={onDuplicate}>
              <Copy size={14} />
            </IconButton>
          </>
        )}
        {canDelete && (
          <>
            <span className="action-divider" aria-hidden />
            <IconButton
              label={`删除项目 ${project.name}`}
              tone="danger"
              onClick={onDelete}
              disabled={deleting}
            >
              {deleting ? <RefreshCw size={15} className="spin" /> : <Trash2 size={15} />}
            </IconButton>
          </>
        )}
      </div>
    </div>
  )
}

function ProjectListSkeleton({ selectable }: { selectable: boolean }) {
  return (
    <div className="project-table" aria-busy="true" aria-label="项目加载中">
      <div className="project-table-head">
        <span className="cell-check">{selectable && <Skeleton width={15} height={15} radius={4} />}</span>
        <span>项目</span>
        <span>规则包 / 标包</span>
        <span>状态</span>
        <span>招标人</span>
        <span>编写进度</span>
        <span>待办</span>
        <span>最近更新</span>
        <span className="col-actions-head">操作</span>
      </div>
      {Array.from({ length: 3 }).map((_, index) => (
        <div className="project-row project-row--skeleton" key={index}>
          <div className="cell-check">
            {selectable && <Skeleton width={15} height={15} radius={4} />}
          </div>
          <div className="cell-project">
            <Skeleton width="62%" height={17} />
            <Skeleton width="34%" height={10} />
          </div>
          <div className="cell-package">
            <Skeleton width="70%" height={13} />
            <Skeleton width="46%" height={10} />
          </div>
          <Skeleton width={64} height={22} radius={999} />
          <Skeleton width="58%" height={13} />
          <div className="cell-progress">
            <Skeleton width="45%" height={12} />
            <Skeleton height={5} />
            <Skeleton width="55%" height={10} />
          </div>
          <Skeleton width={88} height={20} radius={999} />
          <Skeleton width={60} height={12} />
          <Skeleton width={48} height={26} />
        </div>
      ))}
    </div>
  )
}
