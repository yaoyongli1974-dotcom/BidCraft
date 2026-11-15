import { Navigate, useLocation } from 'react-router-dom'
import { AlertTriangle, RotateCcw, ShieldCheck } from 'lucide-react'
import { useAuth } from '../context/useAuth'
import { AppShell } from './AppShell'
import { Button } from './Button'

export function ProtectedShell() {
  const { user, loading, error, refresh } = useAuth()
  const location = useLocation()

  if (loading) return <AuthSplash />

  if (error) {
    return (
      <AuthUnavailable
        message={error}
        onRetry={() => {
          refresh()
        }}
      />
    )
  }

  if (!user) {
    const next = `${location.pathname}${location.search}`
    const params = new URLSearchParams({ next })
    return <Navigate to={`/login?${params.toString()}`} replace />
  }

  return <AppShell />
}

export function AuthSplash({
  title = '正在验证登录状态',
  description = '正在读取本机会话与权限，请稍候。',
}: {
  title?: string
  description?: string
}) {
  return (
    <div className="auth-splash" role="status" aria-live="polite">
      <div className="auth-splash-card">
        <div className="auth-splash-brand">
          <span className="brand-seal" aria-hidden>
            标
          </span>
          <div>
            <span className="auth-splash-name">BidCraft</span>
            <span className="auth-splash-sub">标书工坊</span>
          </div>
        </div>
        <div className="auth-splash-rule" aria-hidden>
          <span />
        </div>
        <h1>{title}</h1>
        <p>{description}</p>
        <div className="auth-splash-skeleton" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <div className="auth-splash-foot">
          <ShieldCheck size={14} /> 本机会话 · 无遥测
        </div>
      </div>
    </div>
  )
}

export function AuthUnavailable({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <div className="auth-splash">
      <div className="auth-splash-card auth-splash-card--error">
        <div className="auth-error-icon" aria-hidden>
          <AlertTriangle size={24} />
        </div>
        <div className="eyebrow">无法验证会话</div>
        <h1>本地服务暂时不可用</h1>
        <p>{message}</p>
        <Button variant="primary" icon={<RotateCcw size={15} />} onClick={onRetry}>
          重新连接
        </Button>
        <p className="auth-splash-hint">
          请确认 BidCraft 后端已启动，并且当前页面由它托管。
        </p>
      </div>
    </div>
  )
}
