import { lazy, Suspense, type ReactNode } from 'react'
import { Link, Navigate, createBrowserRouter, useRouteError } from 'react-router-dom'
import { ArrowLeft, FileQuestion, RotateCcw } from 'lucide-react'
import { ProtectedShell } from './components/AuthGate'
import { LoadingBlock, Skeleton } from './components/Feedback'

// Route-level code splitting keeps the initial bundle small; the workspace tabs
// (ledger, outline, draft, check…) only download when the user opens them.
const ProjectsPage = lazy(() =>
  import('./pages/ProjectsPage').then((module) => ({ default: module.ProjectsPage })),
)
const LoginPage = lazy(() =>
  import('./pages/LoginPage').then((module) => ({ default: module.LoginPage })),
)
const RegisterPage = lazy(() =>
  import('./pages/RegisterPage').then((module) => ({ default: module.RegisterPage })),
)
const BillingPage = lazy(() =>
  import('./pages/BillingPage').then((module) => ({ default: module.BillingPage })),
)
const MyDocsPage = lazy(() =>
  import('./pages/MyDocsPage').then((module) => ({ default: module.MyDocsPage })),
)
const AdminConsole = lazy(() =>
  import('./pages/admin/AdminConsole').then((module) => ({ default: module.AdminConsole })),
)
const WorkspacePage = lazy(() =>
  import('./pages/WorkspacePage').then((module) => ({ default: module.WorkspacePage })),
)
const OverviewTab = lazy(() =>
  import('./pages/tabs/OverviewTab').then((module) => ({ default: module.OverviewTab })),
)
const SourcesTab = lazy(() =>
  import('./pages/tabs/SourcesTab').then((module) => ({ default: module.SourcesTab })),
)
const LedgerTab = lazy(() =>
  import('./pages/tabs/LedgerTab').then((module) => ({ default: module.LedgerTab })),
)
const BoqTab = lazy(() =>
  import('./pages/tabs/BoqTab').then((module) => ({ default: module.BoqTab })),
)
const OutlineTab = lazy(() =>
  import('./pages/tabs/OutlineTab').then((module) => ({ default: module.OutlineTab })),
)
const DraftTab = lazy(() =>
  import('./pages/tabs/DraftTab').then((module) => ({ default: module.DraftTab })),
)
const CheckTab = lazy(() =>
  import('./pages/tabs/CheckTab').then((module) => ({ default: module.CheckTab })),
)
const ExportTab = lazy(() =>
  import('./pages/tabs/ExportTab').then((module) => ({ default: module.ExportTab })),
)
const CollabTab = lazy(() =>
  import('./pages/tabs/CollabTab').then((module) => ({ default: module.CollabTab })),
)

function page(element: ReactNode, fallback: ReactNode = <PageFallback />) {
  return <Suspense fallback={fallback}>{element}</Suspense>
}

export const router = createBrowserRouter([
  {
    path: '/login',
    element: page(<LoginPage />),
    errorElement: <RouteErrorPage />,
  },
  {
    path: '/register',
    element: page(<RegisterPage />),
    errorElement: <RouteErrorPage />,
  },
  {
    path: '/',
    element: <ProtectedShell />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: page(<ProjectsPage />, <ShellFallback />) },
      { path: 'app', element: <Navigate to="/" replace /> },
      {
        path: 'app/projects/:ref',
        element: page(<WorkspacePage />, <ShellFallback />),
        children: [
          { index: true, element: page(<OverviewTab />, <TabFallback />) },
          { path: 'sources', element: page(<SourcesTab />, <TabFallback />) },
          { path: 'ledger', element: page(<LedgerTab />, <TabFallback />) },
          { path: 'boq', element: page(<BoqTab />, <TabFallback />) },
          { path: 'outline', element: page(<OutlineTab />, <TabFallback />) },
          { path: 'draft', element: page(<DraftTab />, <TabFallback />) },
          { path: 'check', element: page(<CheckTab />, <TabFallback />) },
          { path: 'export', element: page(<ExportTab />, <TabFallback />) },
          { path: 'collab', element: page(<CollabTab />, <TabFallback />) },
          { path: '*', element: <NotFoundContent /> },
        ],
      },
      { path: 'admin', element: page(<AdminConsole />, <ShellFallback />) },
      { path: 'billing', element: page(<BillingPage />, <ShellFallback />) },
      { path: 'my-docs', element: page(<MyDocsPage />, <ShellFallback />) },
      { path: '*', element: <NotFoundContent /> },
    ],
  },
])

/** Full-page fallback for a lazily loaded route. */
function PageFallback() {
  return (
    <div
      className="standalone-state standalone-state--full"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="standalone-card">
        <span className="mini-seal" aria-hidden>
          标
        </span>
        <div className="eyebrow">正在加载</div>
        <h1>正在打开页面…</h1>
        <LoadingBlock lines={2} />
      </div>
    </div>
  )
}

/** Fallback for a lazy route that renders inside the app shell. */
function ShellFallback() {
  return (
    <div className="standalone-state" role="status" aria-live="polite" aria-busy="true">
      <div className="standalone-card">
        <span className="mini-seal" aria-hidden>
          标
        </span>
        <div className="eyebrow">正在加载</div>
        <h1>正在打开页面…</h1>
        <LoadingBlock lines={2} />
      </div>
    </div>
  )
}

/** Fallback shown inside the workspace shell while a tab chunk downloads. */
function TabFallback() {
  return (
    <div className="panel" role="status" aria-live="polite" aria-busy="true">
      <div className="loading-block">
        <Skeleton width={120} height={12} />
        <Skeleton width="58%" height={24} />
        <Skeleton width="100%" height={140} radius={8} />
        <Skeleton width="82%" height={16} />
      </div>
    </div>
  )
}

function NotFoundContent() {
  return (
    <div className="standalone-state">
      <div className="standalone-card">
        <span className="mini-seal" aria-hidden>
          标
        </span>
        <div className="eyebrow">404 · NOT FOUND</div>
        <h1>这个页面不存在</h1>
        <p>链接可能已失效，或项目已被删除。请回到项目列表继续。</p>
        <Link to="/" className="btn btn--primary btn--md">
          <span className="btn-label">回到项目列表</span>
          <ArrowLeft size={15} />
        </Link>
      </div>
    </div>
  )
}

function RouteErrorPage() {
  const error = useRouteError()
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : '页面加载时发生未知错误。'
  return (
    <div className="standalone-state standalone-state--full">
      <div className="standalone-card">
        <FileQuestion size={30} strokeWidth={1.5} />
        <div className="eyebrow">界面错误</div>
        <h1>页面加载失败</h1>
        <p>{message}</p>
        <div className="error-actions">
          <button
            type="button"
            className="btn btn--secondary btn--md"
            onClick={() => window.location.reload()}
          >
            <RotateCcw size={15} />
            <span className="btn-label">重新加载</span>
          </button>
          <a href="/" className="btn btn--primary btn--md">
            <span className="btn-label">回到项目列表</span>
          </a>
        </div>
      </div>
    </div>
  )
}
