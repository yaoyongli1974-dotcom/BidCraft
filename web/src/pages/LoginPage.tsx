import { useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowRight,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  QrCode,
  ShieldCheck,
  UserRound,
} from 'lucide-react'
import { useAuth } from '../context/useAuth'
import { useHealth } from '../context/useHealth'
import { awaitDesktopBridge, messageOf, wechatLoginUrl } from '../lib/api'
import { useDocumentTitle } from '../lib/hooks'
import { AuthSplash } from '../components/AuthGate'

export function LoginPage() {
  useDocumentTitle('登录')
  const [bridge, setBridge] = useState<Awaited<ReturnType<typeof awaitDesktopBridge>>>(null)
  const [server, setServer] = useState('')
  useEffect(() => {
    let alive = true
    void awaitDesktopBridge(1500).then(async (api) => {
      if (!alive || !api) return
      setBridge(api)
      try {
        const info = await api.info()
        if (alive) setServer(info.server)
      } catch {
        /* 服务未响应时保持占位 */
      }
    })
    return () => {
      alive = false
    }
  }, [])
  async function changeServer() {
    if (!bridge?.choose_server) return
    try {
      const result = await bridge.choose_server()
      if (result?.changed) {
        setServer(result.server)
        window.location.reload()
      }
    } catch {
      /* 取消或失败时保持原状 */
    }
  }
  const { user, loading, login } = useAuth()
  const { health } = useHealth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  const nextPath = useMemo(() => {
    const raw = searchParams.get('next') || '/'
    if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/login')) return '/'
    return raw
  }, [searchParams])

  const reason = searchParams.get('reason')
  const wechatMessage = searchParams.get('message')
  const notice =
    reason === 'password'
      ? '密码已修改，请使用新密码重新登录。'
      : reason === 'expired'
        ? '登录状态已失效，请重新登录后继续。'
        : null
  const wechatError = reason === 'wechat' ? wechatMessage || '微信登录未完成，请重试。' : null
  const wechatEnabled = Boolean(health?.wechat_login)
  const registrationOpen = health?.registration !== false

  if (loading) return <AuthSplash title="正在检查登录状态" description="正在读取本机会话与权限。" />
  if (user) return <Navigate to={nextPath} replace />

  const submit = async () => {
    setTouched(true)
    if (!username.trim() || !password) return
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
      navigate(nextPath, { replace: true })
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <section className="login-brand-panel" aria-label="BidCraft 标书工坊">
        <div className="login-brand">
          <span className="brand-seal login-seal" aria-hidden>
            标
          </span>
          <div>
            <span className="brand-name">BidCraft</span>
            <span className="login-brand-sub">标书工坊</span>
          </div>
        </div>

        <div className="login-brand-copy">
          <div className="eyebrow login-eyebrow">弱电投标文件工作台</div>
          <h1>
            把招标文件，
            <br />
            变成<span>可交付的标书</span>。
          </h1>
          <p>
            从解析、事实台账、大纲到正文与合规核对，一条链路完成。
            模型、知识与成员权限都留在本机部署中。
          </p>
        </div>

        <ul className="login-features">
          <li>
            <ShieldCheck size={16} />
            <div>
              <strong>本地优先</strong>
              <span>项目与导出件仅保存在本机服务器</span>
            </div>
          </li>
          <li>
            <KeyRound size={16} />
            <div>
              <strong>角色分权</strong>
              <span>超级管理员、投标经理、只读成员</span>
            </div>
          </li>
          <li>
            <UserRound size={16} />
            <div>
              <strong>账号与点数</strong>
              <span>自注册赠送体验点数，生成按章扣点</span>
            </div>
          </li>
        </ul>

        <div className="login-brand-foot">
          <span>标书工坊 / 内部系统</span>
          <span className="mono">v{health?.version || '—'}</span>
        </div>
      </section>

      <section className="login-form-panel">
        <div className="login-card">
          <div className="eyebrow">成员登录</div>
          <h2>登录工作台</h2>
          <p className="login-lede">
            使用账号密码登录，或使用微信扫码。登录状态保留在当前浏览器，不会同步到其他设备。
          </p>

          {notice && (
            <div className="login-notice" role="status">
              <ShieldCheck size={16} />
              <span>{notice}</span>
            </div>
          )}

          {bridge && (
            <div className="login-server">
              <span className="login-server-label">服务器</span>
              <code className="login-server-value">{server || '连接中…'}</code>
              <button
                type="button"
                className="text-btn"
                onClick={() => void changeServer()}
                disabled={!bridge.choose_server}
              >
                更改
              </button>
            </div>
          )}

          <form
            className="login-form"
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            <div className="form-field">
              <label htmlFor="login-username">用户名</label>
              <div className="login-input-wrap">
                <UserRound size={16} aria-hidden />
                <input
                  id="login-username"
                  className="input login-input"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="请输入用户名"
                  autoComplete="username"
                  autoCapitalize="none"
                  autoFocus
                  maxLength={80}
                  aria-invalid={touched && !username.trim()}
                  disabled={submitting}
                />
              </div>
              {touched && !username.trim() && (
                <span className="field-error">请输入用户名。</span>
              )}
            </div>

            <div className="form-field">
              <label htmlFor="login-password">密码</label>
              <div className="login-input-wrap">
                <KeyRound size={16} aria-hidden />
                <input
                  id="login-password"
                  className="input login-input"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="请输入密码"
                  autoComplete="current-password"
                  maxLength={200}
                  aria-invalid={touched && !password}
                  disabled={submitting}
                />
                <button
                  type="button"
                  className="login-eye"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  title={showPassword ? '隐藏密码' : '显示密码'}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              {touched && !password && <span className="field-error">请输入密码。</span>}
            </div>

            {error && (
              <div className="login-error" role="alert">
                <span>{error}</span>
              </div>
            )}
            {wechatError && (
              <div className="login-error" role="alert">
                <span>{wechatError}</span>
              </div>
            )}

            <button
              type="submit"
              className="btn btn--primary btn--lg login-submit"
              disabled={submitting}
            >
              {submitting ? (
                <>
                  <Loader2 size={16} className="spin" /> 正在登录…
                </>
              ) : (
                <>
                  登录 <ArrowRight size={16} />
                </>
              )}
            </button>
          </form>

          {wechatEnabled && (
            <div className="login-wechat">
              <div className="login-divider">
                <span>或</span>
              </div>
              <button
                type="button"
                className="btn btn--secondary btn--lg login-wechat-btn"
                onClick={() => window.location.assign(wechatLoginUrl(health, nextPath))}
              >
                <QrCode size={17} />
                微信扫码登录
              </button>
              <p className="login-wechat-note">
                微信登录将自动创建账号
                {health?.wechat_mock ? '（当前为模拟模式）' : ''}
              </p>
            </div>
          )}

          <div className="login-card-foot">
            {registrationOpen ? (
              <span>
                还没有账号？{' '}
                <Link to="/register" className="login-register-link">
                  注册账号
                </Link>
              </span>
            ) : (
              <span>注册暂未开放，请联系超级管理员创建账号。</span>
            )}
            <span className="login-local">
              <ShieldCheck size={13} /> 本机认证
            </span>
          </div>
        </div>

        <p className="login-footnote">
          BidCraft 标书工坊 · 本地部署 · 零遥测 · 数据不出内网
        </p>
      </section>
    </div>
  )
}
