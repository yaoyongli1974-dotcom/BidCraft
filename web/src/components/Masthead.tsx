import { Link, useLocation } from 'react-router-dom'
import { BadgeCheck, Cpu, FileText, Loader2, Settings2, ShieldCheck, WifiOff } from 'lucide-react'
import { useHealth } from '../context/useHealth'
import { useHelp } from '../context/useHelp'
import { useAuth } from '../context/useAuth'
import { UserMenu } from './UserMenu'
import { OfflineBanner } from './OfflineBanner'
import { PendingApprovals } from './PendingApprovals'
import { className } from '../lib/format'

export function Masthead() {
  const { health, error, loading } = useHealth()
  const { openHelp } = useHelp()
  const { isSuperAdmin } = useAuth()
  const location = useLocation()
  const onProjects = location.pathname === '/'
  const onAdmin = location.pathname === '/admin'
  const onMyDocs = location.pathname === '/my-docs'
  const onQualifications = location.pathname === '/qualifications'

  const { label, tone, Icon } = (() => {
    if (loading) return { label: '检查本地服务', tone: 'neutral', Icon: Loader2 }
    if (error) return { label: '本地服务未连接', tone: 'danger', Icon: WifiOff }
    if (health?.offline) return { label: '离线模式', tone: 'warn', Icon: WifiOff }
    if (health?.model_configured) return { label: '模型已配置', tone: 'success', Icon: Cpu }
    return { label: '确定性草稿', tone: 'info', Icon: Cpu }
  })()

  return (
    <header className="masthead">
      <OfflineBanner />
      <div className="masthead-inner">
        <Link to="/" className="brand" aria-label="BidCraft 项目列表">
          <span className="brand-seal" aria-hidden>
            标
          </span>
          <span className="brand-type">
            <span className="brand-name">BidCraft</span>
            <span className="brand-sub">标书工坊</span>
          </span>
        </Link>

        <div className="masthead-nav">
          <Link to="/" className={className('masthead-link', onProjects && 'is-active')}>
            项目
          </Link>
          <Link
            to="/my-docs"
            className={className('masthead-link', onMyDocs && 'is-active')}
          >
            <FileText size={13} />
            我的文档
          </Link>
          <Link
            to="/qualifications"
            className={className(
              'masthead-link',
              onQualifications && 'is-active',
            )}
          >
            <BadgeCheck size={13} />
            资质与自查
          </Link>
          {isSuperAdmin && (onProjects || onAdmin) && (
            <Link
              to="/admin"
              className={className(
                'masthead-link masthead-admin-link',
                onAdmin && 'is-active',
              )}
            >
              <ShieldCheck size={13} />
              管理
            </Link>
          )}
          <span className="masthead-divider" aria-hidden />
          <span className="masthead-tagline">弱电投标文件工作台</span>
        </div>

        <div className="masthead-actions">
          <button
            type="button"
            className={className('service-pill', `service-pill--${tone}`)}
            onClick={openHelp}
            title={error || label}
            aria-label={label}
          >
            <Icon size={14} className={loading ? 'spin' : undefined} aria-hidden />
            <span>{label}</span>
          </button>
          <PendingApprovals />
          <button
            type="button"
            className="masthead-help"
            onClick={openHelp}
            aria-label="设置与帮助"
          >
            <Settings2 size={17} />
            <span>设置与帮助</span>
          </button>
          <UserMenu />
        </div>
      </div>
    </header>
  )
}
