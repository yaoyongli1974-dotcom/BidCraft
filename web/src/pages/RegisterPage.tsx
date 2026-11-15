import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  QrCode,
  ShieldCheck,
  UserRound,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { useHealth } from '../context/HealthContext'
import { messageOf, wechatLoginUrl } from '../lib/api'
import { useDocumentTitle } from '../lib/hooks'
import { AuthSplash } from '../components/AuthGate'

const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,40}$/

export function RegisterPage() {
  useDocumentTitle('注册')
  const { user, loading, register } = useAuth()
  const { health } = useHealth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  if (loading) return <AuthSplash title="正在检查登录状态" description="正在读取本机会话与权限。" />
  if (user) return <Navigate to="/" replace />

  const registrationOpen = health?.registration !== false
  const wechatEnabled = Boolean(health?.wechat_login)

  const submit = async () => {
    setTouched(true)
    if (
      !USERNAME_PATTERN.test(username.trim()) ||
      password.length < 8 ||
      password !== confirm
    ) {
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await register(username.trim(), password, displayName.trim())
      navigate('/', { replace: true })
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page register-page">
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
            注册账号，
            <br />
            从<span>事实台账</span>开始。
          </h1>
          <p>
            自注册账号默认具备投标经理权限，并获得体验点数。查看、编辑、核对与导出免费，
            AI 生成按章扣点。
          </p>
        </div>

        <ul className="login-features">
          <li>
            <CheckCircle2 size={16} />
            <div>
              <strong>注册即赠体验点数</strong>
              <span>足够先跑通导入、大纲与章节生成</span>
            </div>
          </li>
          <li>
            <ShieldCheck size={16} />
            <div>
              <strong>项目相互隔离</strong>
              <span>每位成员只能访问自己的项目</span>
            </div>
          </li>
          <li>
            <KeyRound size={16} />
            <div>
              <strong>没有演示账号</strong>
              <span>账号与密码由你自行设置</span>
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
          <div className="eyebrow">创建账号</div>
          <h2>注册工作台</h2>
          <p className="login-lede">
            {registrationOpen
              ? '填写基本信息即可开始。注册成功后自动登录，并获得体验点数。'
              : '当前部署未开放自助注册，请联系超级管理员创建账号。'}
          </p>

          {registrationOpen ? (
            <form
              className="login-form"
              onSubmit={(event) => {
                event.preventDefault()
                void submit()
              }}
            >
              <div className="form-field">
                <label htmlFor="register-username">用户名</label>
                <div className="login-input-wrap">
                  <UserRound size={16} aria-hidden />
                  <input
                    id="register-username"
                    className="input login-input"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="3–40 位字母、数字、下划线或短横线"
                    autoComplete="username"
                    autoCapitalize="none"
                    maxLength={40}
                    data-autofocus
                    disabled={submitting}
                  />
                </div>
                <p className="field-hint">用于登录，创建后不可修改。</p>
                {touched && !USERNAME_PATTERN.test(username.trim()) && (
                  <span className="field-error">
                    用户名需为 3–40 位字母、数字、下划线、点或短横线。
                  </span>
                )}
              </div>

              <div className="form-field">
                <label htmlFor="register-display-name">显示名称（可选）</label>
                <input
                  id="register-display-name"
                  className="input"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="例如：张三 · 投标经理"
                  maxLength={120}
                  disabled={submitting}
                />
              </div>

              <div className="form-field">
                <label htmlFor="register-password">密码</label>
                <div className="login-input-wrap">
                  <KeyRound size={16} aria-hidden />
                  <input
                    id="register-password"
                    className="input login-input"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="至少 8 个字符"
                    autoComplete="new-password"
                    disabled={submitting}
                  />
                  <button
                    type="button"
                    className="login-eye"
                    onClick={() => setShowPassword((value) => !value)}
                    aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
                {touched && password.length < 8 && (
                  <span className="field-error">密码至少 8 个字符。</span>
                )}
              </div>

              <div className="form-field">
                <label htmlFor="register-confirm">确认密码</label>
                <input
                  id="register-confirm"
                  className="input"
                  type={showPassword ? 'text' : 'password'}
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  placeholder="再次输入密码"
                  autoComplete="new-password"
                  disabled={submitting}
                />
                {touched && password !== confirm && (
                  <span className="field-error">两次输入的密码不一致。</span>
                )}
              </div>

              {error && (
                <div className="login-error" role="alert">
                  <span>{error}</span>
                </div>
              )}

              <button
                type="submit"
                className="btn btn--primary btn--lg login-submit"
                disabled={submitting}
              >
                {submitting ? (
                  <>
                    <Loader2 size={16} className="spin" /> 正在注册…
                  </>
                ) : (
                  <>
                    创建账号 <ArrowRight size={16} />
                  </>
                )}
              </button>
            </form>
          ) : (
            <div className="login-closed">
              <ShieldCheck size={22} />
              <p>自助注册暂未开放。请联系超级管理员创建账号并分配角色。</p>
              <Link to="/login" className="btn btn--secondary btn--md">
                返回登录
              </Link>
            </div>
          )}

          {wechatEnabled && registrationOpen && (
            <div className="login-wechat">
              <div className="login-divider">
                <span>或</span>
              </div>
              <button
                type="button"
                className="btn btn--secondary btn--lg login-wechat-btn"
                onClick={() => window.location.assign(wechatLoginUrl(health, '/'))}
              >
                <QrCode size={17} />
                微信扫码登录 / 注册
              </button>
              <p className="login-wechat-note">
                微信登录将自动创建账号
                {health?.wechat_mock ? '（当前为模拟模式）' : ''}
              </p>
            </div>
          )}

          <div className="login-card-foot">
            <span>
              已有账号？{' '}
              <Link to="/login" className="login-register-link">
                返回登录
              </Link>
            </span>
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
