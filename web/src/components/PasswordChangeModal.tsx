import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { Modal } from './Modal'
import { Button } from './Button'
import { useAuth } from '../context/useAuth'
import { messageOf } from '../lib/api'

export function PasswordChangeModal({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const { changePassword } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  const reset = () => {
    setCurrent('')
    setNext('')
    setConfirm('')
    setError(null)
    setTouched(false)
    setSaving(false)
  }

  const close = () => {
    if (saving) return
    reset()
    onClose()
  }

  const submit = async () => {
    setTouched(true)
    if (!current || next.length < 8 || next !== confirm) return
    setSaving(true)
    setError(null)
    try {
      await changePassword(current, next)
      reset()
      onClose()
      window.location.replace('/login?reason=password')
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="修改密码"
      eyebrow="账号安全"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="password-change-form"
            loading={saving}
          >
            更新密码
          </Button>
        </>
      }
    >
      <div className="password-modal">
        <div className="password-icon" aria-hidden>
          <KeyRound size={20} />
        </div>
        <p className="password-lede">
          更新后会退出当前会话，需要使用新密码重新登录。至少 8 个字符。
        </p>
        <form
          id="password-change-form"
          className="password-form"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="form-field">
            <label htmlFor="password-current">当前密码</label>
            <input
              id="password-current"
              className="input"
              type="password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              autoComplete="current-password"
              data-autofocus
              disabled={saving}
            />
            {touched && !current && <span className="field-error">请输入当前密码。</span>}
          </div>
          <div className="form-field">
            <label htmlFor="password-new">新密码</label>
            <input
              id="password-new"
              className="input"
              type="password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              disabled={saving}
            />
            {touched && next.length < 8 && (
              <span className="field-error">新密码至少 8 个字符。</span>
            )}
          </div>
          <div className="form-field">
            <label htmlFor="password-confirm">确认新密码</label>
            <input
              id="password-confirm"
              className="input"
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              autoComplete="new-password"
              disabled={saving}
            />
            {touched && next !== confirm && (
              <span className="field-error">两次输入的新密码不一致。</span>
            )}
          </div>
          {error && (
            <div className="form-alert" role="alert">
              {error}
            </div>
          )}
        </form>
      </div>
    </Modal>
  )
}
