import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  ChevronDown,
  Coins,
  Crown,
  FileText,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Settings2,
  ShieldCheck,
  Wallet,
} from 'lucide-react'
import { useAuth } from '../context/useAuth'
import { className, roleMeta } from '../lib/format'
import { Badge } from './Badge'
import { PasswordChangeModal } from './PasswordChangeModal'

export function UserMenu() {
  const { user, isSuperAdmin, logout } = useAuth()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        rootRef.current?.querySelector<HTMLButtonElement>('.user-trigger')?.focus()
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!user) return null

  const meta = roleMeta(user.role, user.role_label)
  const initial = (user.display_name || user.username || '?').trim().slice(0, 1)

  const handleLogout = async () => {
    setOpen(false)
    setLoggingOut(true)
    await logout()
    setLoggingOut(false)
    window.location.replace('/login')
  }

  return (
    <>
      <div className="user-menu" ref={rootRef}>
        <button
          type="button"
          className={className('user-trigger', open && 'is-open')}
          onClick={() => setOpen((value) => !value)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={`${user.display_name}，${meta.label}，${user.credits ?? 0} 点，账号菜单`}
          title={`${user.display_name} · ${meta.label} · ${user.credits ?? 0} 点`}
        >
          <span className="user-avatar" aria-hidden>
            {initial}
          </span>
          <span className="user-trigger-copy">
            <span className="user-trigger-name">{user.display_name || user.username}</span>
            <span className="user-trigger-role">
              {user.is_vip && <Crown size={11} className="user-vip-crown" aria-hidden />}
              {meta.label} · {user.credits ?? 0} 点
            </span>
          </span>
          <ChevronDown size={13} className="user-chevron" aria-hidden />
        </button>

        {open && (
          <div className="user-dropdown" role="menu" aria-label="账号菜单">
            <div className="user-dropdown-head">
              <span className="user-avatar user-avatar--large" aria-hidden>
                {initial}
              </span>
              <div>
                <strong>{user.display_name || user.username}</strong>
                <span className="user-handle mono">@{user.username}</span>
                <Badge tone={meta.tone} subtle>
                  {meta.label}
                </Badge>
                {user.is_vip && (
                  <Badge tone="accent" subtle>
                    <Crown size={11} /> VIP 会员
                  </Badge>
                )}
                <span className="user-dropdown-credits">
                  <Coins size={12} /> {user.credits ?? 0} 点
                </span>
              </div>
            </div>

            <Link
              to="/my-docs"
              className="user-menu-item"
              role="menuitem"
              onClick={() => setOpen(false)}
            >
              <FileText size={15} />
              <span>我的文档</span>
            </Link>
            <Link
              to="/account"
              className="user-menu-item"
              role="menuitem"
              onClick={() => setOpen(false)}
            >
              <ShieldCheck size={15} />
              <span>账号与数据</span>
            </Link>
            <Link
              to="/billing"
              className="user-menu-item"
              role="menuitem"
              onClick={() => setOpen(false)}
            >
              <Wallet size={15} />
              <span>账户与充值</span>
              <span className="user-menu-item-tail mono">{user.credits ?? 0} 点</span>
            </Link>
            {isSuperAdmin && (
              <Link
                to="/admin"
                className="user-menu-item"
                role="menuitem"
                onClick={() => setOpen(false)}
              >
                <LayoutDashboard size={15} />
                <span>管理控制台</span>
                <ShieldCheck size={13} className="user-menu-item-tail" />
              </Link>
            )}
            <button
              type="button"
              className="user-menu-item"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                setPasswordOpen(true)
              }}
            >
              <KeyRound size={15} />
              <span>修改密码</span>
            </button>
            <div className="user-menu-sep" role="separator" />
            <button
              type="button"
              className="user-menu-item user-menu-item--danger"
              role="menuitem"
              onClick={() => void handleLogout()}
              disabled={loggingOut}
            >
              <LogOut size={15} />
              <span>{loggingOut ? '正在退出…' : '退出登录'}</span>
            </button>
            <div className="user-menu-foot">
              <Settings2 size={12} />
              角色与账号由超级管理员管理
            </div>
          </div>
        )}
      </div>

      <PasswordChangeModal open={passwordOpen} onClose={() => setPasswordOpen(false)} />
    </>
  )
}
