import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowLeftRight,
  Building2,
  ChevronRight,
  ClipboardList,
  Clock3,
  Cpu,
  Download,
  Eye,
  FileStack,
  HardDrive,
  LayoutDashboard,
  ListTree,
  Package as PackageIcon,
  PenLine,
  ShieldCheck,
  Users,
  WifiOff,
  type LucideIcon,
} from 'lucide-react'
import { getProject, getSources } from '../lib/api'
import type { PackageInfo, ProjectDetail, SourceFile } from '../lib/types'
import {
  className,
  formatDate,
  formatRelative,
  outlineCount,
  projectRoleMeta,
  projectStats,
  projectStatus,
} from '../lib/format'
import { useDocumentTitle, usePackages, useQuery } from '../lib/hooks'
import { Badge, BidSectionBadge } from '../components/Badge'
import { Button } from '../components/Button'
import { Skeleton } from '../components/Feedback'
import { useHealth } from '../context/useHealth'
import { useAuth } from '../context/useAuth'

export interface WorkspaceContextValue {
  ref: string
  basePath: string
  project: ProjectDetail
  sources: SourceFile[]
  sourcesLoading: boolean
  packages: PackageInfo[]
  reloadProject: () => Promise<ProjectDetail>
  reloadSources: () => Promise<SourceFile[]>
  /** True for global viewers or members whose project role is `viewer`. */
  readOnly: boolean
  /** Project-aware write permission; use this instead of `useAuth().canWrite`. */
  canWrite: boolean
  setUnsaved: (value: boolean) => void
}

interface WorkspaceTab {
  to: string
  end?: boolean
  label: string
  icon: LucideIcon
  count?: number
  tone?: 'danger' | 'warn'
}

export function WorkspacePage() {
  const { ref = '' } = useParams<{ ref: string }>()
  const basePath = `/app/projects/${encodeURIComponent(ref)}`
  const { health } = useHealth()
  const { user, canWrite } = useAuth()
  const { packages } = usePackages()
  const [dirty, setDirty] = useState(false)
  const tabsRef = useRef<HTMLElement | null>(null)
  const location = useLocation()

  const projectQuery = useQuery(`project:${ref}`, () => getProject(ref))
  const sourcesQuery = useQuery(`sources:${ref}`, () => getSources(ref))
  const project = projectQuery.data
  const setProjectData = projectQuery.setData
  const setSourcesData = sourcesQuery.setData

  useDocumentTitle(project?.name)

  useEffect(() => {
    setDirty(false)
  }, [ref])

  // On narrow screens the tab strip scrolls horizontally; keep the active tab
  // in view when the route changes so the new "协作" tab is discoverable.
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const nav = tabsRef.current
      const active = nav?.querySelector<HTMLElement>('.workspace-tab.is-active')
      if (!nav || !active) return
      const navRect = nav.getBoundingClientRect()
      const activeRect = active.getBoundingClientRect()
      const delta =
        activeRect.left - navRect.left - (nav.clientWidth - activeRect.width) / 2
      if (Math.abs(delta) > 1) nav.scrollLeft += delta
    })
    return () => window.cancelAnimationFrame(frame)
  }, [location.pathname, project?.id])

  const reloadProject = useCallback(async () => {
    const data = await getProject(ref)
    setProjectData(data)
    return data
  }, [ref, setProjectData])

  const reloadSources = useCallback(async () => {
    const data = await getSources(ref)
    setSourcesData(data)
    return data
  }, [ref, setSourcesData])

  const sources = useMemo(
    () => sourcesQuery.data ?? project?.sources ?? [],
    [sourcesQuery.data, project?.sources],
  )

  const readOnly = !canWrite || project?.my_role === 'viewer'

  const contextValue = useMemo<WorkspaceContextValue | null>(() => {
    if (!project) return null
    return {
      ref,
      basePath,
      project,
      sources,
      sourcesLoading: sourcesQuery.loading && !project.sources,
      packages,
      reloadProject,
      reloadSources,
      readOnly,
      canWrite: !readOnly,
      setUnsaved: setDirty,
    }
  }, [
    ref,
    basePath,
    project,
    sources,
    sourcesQuery.loading,
    packages,
    reloadProject,
    reloadSources,
    readOnly,
  ])

  if (projectQuery.loading && !project) {
    return <WorkspaceSkeleton />
  }

  if (!project) {
    return (
      <div className="workspace-error">
        <div className="workspace-error-card">
          <span className="mini-seal" aria-hidden>
            标
          </span>
          <div className="eyebrow">无法打开项目</div>
          <h1>项目不存在或暂时无法读取</h1>
          <p>{projectQuery.error || '请检查项目编号，或返回项目列表后重试。'}</p>
          <div className="error-actions">
            <Button
              variant="secondary"
              icon={<ArrowLeft size={15} />}
              onClick={() => window.history.back()}
            >
              返回上一页
            </Button>
            <Link className="btn btn--primary btn--md" to="/">
              <span className="btn-label">回到项目列表</span>
            </Link>
          </div>
        </div>
      </div>
    )
  }

  const stats = projectStats(project)
  const status = projectStatus(project.status)
  const pkg = packages.find((item) => item.name === project.package)
  const missingRequired = project.missing_required || []
  const missingCount = stats.missing_required || missingRequired.length
  const outlineTotal = outlineCount(project)

  const tabs: WorkspaceTab[] = [
    {
      to: basePath,
      end: true,
      label: '概览',
      icon: LayoutDashboard,
    },
    {
      to: `${basePath}/sources`,
      label: '招标文件',
      icon: FileStack,
      count: sources.length || stats.sources || undefined,
    },
    {
      to: `${basePath}/ledger`,
      label: '事实台账',
      icon: ClipboardList,
      count: missingCount || undefined,
      tone: 'danger',
    },
    {
      to: `${basePath}/boq`,
      label: '清单对齐',
      icon: ArrowLeftRight,
    },
    {
      to: `${basePath}/outline`,
      label: '目录大纲',
      icon: ListTree,
      count: outlineTotal || undefined,
    },
    {
      to: `${basePath}/draft`,
      label: '正文',
      icon: PenLine,
      count: stats.written || undefined,
    },
    {
      to: `${basePath}/check`,
      label: '核对',
      icon: ShieldCheck,
      count: stats.issues_open || undefined,
      tone: 'warn',
    },
    {
      to: `${basePath}/export`,
      label: '导出',
      icon: Download,
    },
    {
      to: `${basePath}/collab`,
      label: '协作',
      icon: Users,
    },
  ]

  const modelLabel = health?.offline
    ? '离线模式'
    : health?.model_configured
      ? '模型已配置'
      : health
        ? '确定性草稿'
        : '服务状态未知'

  return (
    <div className="workspace">
      <header className="workspace-head">
        <div className="workspace-head-inner">
          <nav className="breadcrumb" aria-label="面包屑">
            <Link to="/">项目</Link>
            <ChevronRight size={13} aria-hidden />
            <span>{project.name}</span>
          </nav>

          <div className="workspace-title-row">
            <div className="workspace-identity">
              <div className="project-monogram" aria-hidden>
                {project.name.trim().slice(0, 1) || '标'}
              </div>
              <div className="workspace-title-copy">
                <div className="eyebrow">投标项目 · {pkg?.title || project.package}</div>
                <h1>{project.name}</h1>
                <div className="workspace-meta">
                  {project.bid_section?.trim() && (
                    <span className="workspace-meta-section">
                      <BidSectionBadge value={project.bid_section} />
                    </span>
                  )}
                  <span>
                    <Building2 size={13} />
                    {project.buyer || '招标人未提取'}
                  </span>
                  <span>
                    <PackageIcon size={13} />
                    {pkg?.title || project.package}
                  </span>
                  <span className="mono">
                    {project.number ? `项目编号 ${project.number}` : '项目编号未提取'}
                  </span>
                  <span title={formatDate(project.updated_at)}>
                    <Clock3 size={13} />
                    更新于 {formatRelative(project.updated_at)}
                  </span>
                </div>
              </div>
            </div>

            <div className="workspace-flags">
              <Badge tone={status.tone} dot>
                {status.label}
              </Badge>
              {project.my_role && (
                <Badge tone={projectRoleMeta(project.my_role).tone} subtle>
                  {projectRoleMeta(project.my_role).label}
                </Badge>
              )}
              <span
                className={className('fact-chip', dirty ? 'fact-chip--dirty' : 'fact-chip--ok')}
                title={dirty ? '当前页面有未保存的修改' : '修改需要手动保存到本机'}
              >
                <HardDrive size={13} />
                {dirty ? '有未保存修改' : '本机手动保存'}
              </span>
              <span
                className={className(
                  'fact-chip',
                  health?.offline
                    ? 'fact-chip--offline'
                    : health?.model_configured
                      ? 'fact-chip--ok'
                      : 'fact-chip--muted',
                )}
                title={modelLabel}
              >
                {health?.offline ? <WifiOff size={13} /> : <Cpu size={13} />}
                {modelLabel}
              </span>
            </div>
          </div>

          <nav className="workspace-tabs" aria-label="项目工作区" ref={tabsRef}>
            {tabs.map((tab) => (
              <NavLink
                key={tab.to}
                to={tab.to}
                end={tab.end}
                className={({ isActive }: { isActive: boolean }) =>
                  className('workspace-tab', isActive && 'is-active')
                }
              >
                <tab.icon size={15} aria-hidden />
                <span>{tab.label}</span>
                {typeof tab.count === 'number' && tab.count > 0 && (
                  <span
                    className={className(
                      'tab-count',
                      tab.tone === 'danger' && 'tab-count--danger',
                      tab.tone === 'warn' && 'tab-count--warn',
                    )}
                  >
                    {tab.count}
                  </span>
                )}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="workspace-main">
        {readOnly && (
          <div className="readonly-banner" role="status">
            <Eye size={15} />
            <strong>只读成员</strong>
            <span>
              {canWrite
                ? '你在本项目中的角色是只读成员，可以查看项目、台账、正文与核对结果，不能上传、编辑、生成、运行核对或导出。'
                : `${user?.role_label || '只读成员'}可以查看项目、台账、正文与核对结果，不能上传、编辑、生成、运行核对或导出。`}
            </span>
          </div>
        )}
        <Outlet context={contextValue} />
      </main>
    </div>
  )
}

function WorkspaceSkeleton() {
  return (
    <div className="workspace workspace--loading">
      <header className="workspace-head">
        <div className="workspace-head-inner">
          <div className="breadcrumb-skeleton">
            <Skeleton width={90} height={12} />
          </div>
          <div className="workspace-title-row">
            <div className="workspace-identity">
              <Skeleton width={52} height={52} radius={10} />
              <div className="workspace-title-copy">
                <Skeleton width={160} height={10} />
                <Skeleton width={320} height={28} />
                <Skeleton width={420} height={12} />
              </div>
            </div>
            <div className="workspace-flags">
              <Skeleton width={72} height={24} radius={999} />
              <Skeleton width={110} height={24} radius={999} />
            </div>
          </div>
          <div className="workspace-tabs">
            {Array.from({ length: 9 }).map((_, index) => (
              <Skeleton key={index} width={78} height={18} />
            ))}
          </div>
        </div>
      </header>
      <main className="workspace-main">
        <div className="panel">
          <div className="loading-block">
            <Skeleton width={120} height={12} />
            <Skeleton width="72%" height={26} />
            <Skeleton width="100%" height={120} radius={8} />
            <Skeleton width="88%" height={16} />
          </div>
        </div>
      </main>
    </div>
  )
}
