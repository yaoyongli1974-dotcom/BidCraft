import { useEffect, useState } from 'react'
import { UserRound } from 'lucide-react'
import type { User, UserInput } from '../../lib/types'
import { ROLE_HINTS, ROLE_META, roleHint, roleMeta } from '../../lib/format'
import { createUser, messageOf, updateUser } from '../../lib/api'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'
import { Badge } from '../../components/Badge'
import { useAuth } from '../../context/useAuth'

export type UserModalState =
  | { mode: 'create' }
  | { mode: 'edit'; user: User }

interface UserModalProps {
  open: boolean
  mode: 'create' | 'edit'
  user?: User
  onClose: () => void
  onSaved: (user: User, created: boolean) => void
}

const ROLE_OPTIONS = ['manager', 'viewer', 'super_admin']

export function AdminUserModal({
  open,
  mode,
  user,
  onClose,
  onSaved,
}: UserModalProps) {
  const { user: currentUser } = useAuth()
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('viewer')
  const [active, setActive] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setTouched(false)
    if (mode === 'edit' && user) {
      setUsername(user.username)
      setDisplayName(user.display_name || '')
      setPassword('')
      setRole(user.role)
      setActive(user.active)
    } else {
      setUsername('')
      setDisplayName('')
      setPassword('')
      setRole('viewer')
      setActive(true)
    }
  }, [open, mode, user])

  const isSelf = mode === 'edit' && user?.id === currentUser?.id
  const selectedRole = roleMeta(role)
  const selfHint =
    isSelf && role !== 'super_admin'
      ? '当前登录账号不能降低自己的角色，后端会拒绝并返回原因。'
      : isSelf
        ? '这是当前登录账号；可修改显示名称或密码，但不能停用自己。'
        : null

  const submit = async () => {
    setTouched(true)
    if (mode === 'create' && username.trim().length < 2) return
    if (mode === 'create' && password.length < 8) return
    if (mode === 'edit' && password && password.length < 8) return
    setSaving(true)
    setError(null)
    try {
      if (mode === 'create') {
        const payload: UserInput = {
          username: username.trim(),
          display_name: displayName.trim(),
          password,
          role,
        }
        const saved = await createUser(payload)
        onSaved(saved, true)
      } else if (user) {
        const payload: Partial<UserInput> = {
          display_name: displayName.trim(),
          role,
          active,
        }
        if (password) payload.password = password
        const saved = await updateUser(user.id, payload)
        onSaved(saved, false)
      }
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={saving ? () => undefined : onClose}
      title={mode === 'edit' ? '编辑成员' : '添加成员'}
      eyebrow={mode === 'edit' ? '账号、角色与状态' : '创建一个登录账号'}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="user-form"
            loading={saving}
          >
            {mode === 'edit' ? '保存修改' : '创建成员'}
          </Button>
        </>
      }
    >
      <form
        id="user-form"
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="admin-form-note">
          <UserRound size={15} />
          <span>
            {mode === 'edit'
              ? '成员修改会立即生效；密码留空表示不重置。'
              : '账号创建后可立即登录；密码至少 8 个字符。'}
          </span>
        </div>

        <div className="admin-form-grid">
          <div className="form-field">
            <label htmlFor="user-username">
              用户名 <span className="required-mark">必填</span>
            </label>
            <input
              id="user-username"
              className="input mono"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="例如：zhangsan"
              autoComplete="off"
              autoCapitalize="none"
              maxLength={80}
              readOnly={mode === 'edit'}
              disabled={saving}
              data-autofocus
            />
            <p className="field-hint">
              {mode === 'edit' ? '用户名创建后不可修改。' : '至少 2 个字符，用于登录。'}
            </p>
            {touched && mode === 'create' && username.trim().length < 2 && (
              <span className="field-error">用户名至少 2 个字符。</span>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="user-display-name">显示名称</label>
            <input
              id="user-display-name"
              className="input"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="例如：张三 · 投标经理"
              maxLength={120}
              disabled={saving}
            />
            <p className="field-hint">留空时使用用户名显示。</p>
          </div>

          <div className="form-field">
            <label htmlFor="user-password">
              {mode === 'edit' ? '重置密码' : '初始密码'}{' '}
              {mode === 'create' && <span className="required-mark">必填</span>}
            </label>
            <input
              id="user-password"
              className="input"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={mode === 'edit' ? '留空则不修改密码' : '至少 8 个字符'}
              autoComplete="new-password"
              disabled={saving}
            />
            <p className="field-hint">
              {mode === 'edit'
                ? '填写后该成员现有登录会话会失效，需要重新登录。'
                : '创建后请通过安全方式告知成员。'}
            </p>
            {touched && password.length > 0 && password.length < 8 && (
              <span className="field-error">密码至少 8 个字符。</span>
            )}
            {touched && mode === 'create' && password.length === 0 && (
              <span className="field-error">请填写初始密码。</span>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="user-role">角色</label>
            <select
              id="user-role"
              className="select"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              disabled={saving}
            >
              {ROLE_OPTIONS.map((item) => (
                <option key={item} value={item}>
                  {ROLE_META[item]?.label || item}
                </option>
              ))}
            </select>
            <div className="admin-role-hint">
              <Badge tone={selectedRole.tone} subtle>
                {selectedRole.label}
              </Badge>
              <span>{roleHint(role)}</span>
            </div>
            {selfHint && <p className="admin-self-hint">{selfHint}</p>}
          </div>

          {mode === 'edit' && (
            <div className="form-field admin-form-span">
              <label className="admin-check">
                <input
                  type="checkbox"
                  checked={active}
                  onChange={(event) => setActive(event.target.checked)}
                  disabled={saving}
                />
                <span>
                  <strong>账号启用</strong>
                  <small>
                    停用后立即不能登录，现有会话失效；当前账号与最后一名超级管理员由后端保护。
                  </small>
                </span>
              </label>
            </div>
          )}
        </div>

        <div className="admin-role-reference">
          {ROLE_OPTIONS.map((item) => (
            <div key={item}>
              <Badge tone={roleMeta(item).tone} subtle>
                {ROLE_META[item]?.label || item}
              </Badge>
              <span>{ROLE_HINTS[item]}</span>
            </div>
          ))}
        </div>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}
      </form>
    </Modal>
  )
}
