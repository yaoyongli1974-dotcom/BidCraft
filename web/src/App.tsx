import { Link, Navigate, createBrowserRouter, useRouteError } from 'react-router-dom'
import { ArrowLeft, FileQuestion, RotateCcw } from 'lucide-react'
import { ProtectedShell } from './components/AuthGate'
import { ProjectsPage } from './pages/ProjectsPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { BillingPage } from './pages/BillingPage'
import { AdminConsole } from './pages/admin/AdminConsole'
import { WorkspacePage } from './pages/WorkspacePage'
import { OverviewTab } from './pages/tabs/OverviewTab'
import { SourcesTab } from './pages/tabs/SourcesTab'
import { LedgerTab } from './pages/tabs/LedgerTab'
import { BoqTab } from './pages/tabs/BoqTab'
import { OutlineTab } from './pages/tabs/OutlineTab'
import { DraftTab } from './pages/tabs/DraftTab'
import { CheckTab } from './pages/tabs/CheckTab'
import { ExportTab } from './pages/tabs/ExportTab'
import { CollabTab } from './pages/tabs/CollabTab'

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
    errorElement: <RouteErrorPage />,
  },
  {
    path: '/register',
    element: <RegisterPage />,
    errorElement: <RouteErrorPage />,
  },
  {
    path: '/',
    element: <ProtectedShell />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <ProjectsPage /> },
      { path: 'app', element: <Navigate to="/" replace /> },
      {
        path: 'app/projects/:ref',
        element: <WorkspacePage />,
        children: [
          { index: true, element: <OverviewTab /> },
          { path: 'sources', element: <SourcesTab /> },
          { path: 'ledger', element: <LedgerTab /> },
          { path: 'boq', element: <BoqTab /> },
          { path: 'outline', element: <OutlineTab /> },
          { path: 'draft', element: <DraftTab /> },
          { path: 'check', element: <CheckTab /> },
          { path: 'export', element: <ExportTab /> },
          { path: 'collab', element: <CollabTab /> },
          { path: '*', element: <NotFoundContent /> },
        ],
      },
      { path: 'admin', element: <AdminConsole /> },
      { path: 'billing', element: <BillingPage /> },
      { path: '*', element: <NotFoundContent /> },
    ],
  },
])

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
