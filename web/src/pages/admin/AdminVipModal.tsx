import { useEffect, useState } from 'react'
import { Crown } from 'lucide-react'
import type { User } from '../../lib/types'
import { grantUserVip, messageOf } from '../../lib/api'
import { formatDate } from '../../lib/format'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'

const PRESETS = [
  { days: 30, label: '月卡 30 天' },
  { days: 90, label: '季卡 90 天' },
  { days: 365, label: '年卡 365 天' },
]

export function AdminVipModal({
  open,
  user,
  onClose,
  onSaved,
}: {
  open: boolean
  user: User
  onClose: () => void
  onSaved: (user: User) => void
}) {
  const [days, setDays] = useState('30')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setDays('30')
    setNote('')
    setSaving(false)
    setError(null)
  }, [open, user.id])

  const submit = async () => {
    const value = Number(days)
    if (!Number.isInteger(value) || value < 0 || value > 3650) {
      setError('天数须为 0–3650 的整数；填 0 表示清除 VIP。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const result = await grantUserVip(user.id, value, note.trim())
      onSaved(result.user)
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
      title="设置 VIP 会员"
      eyebrow="会员管理"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" type="submit" form="vip-grant-form" loading={saving}>
            保存
          </Button>
        </>
      }
    >
      <form
        id="vip-grant-form"
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="credit-adjust-user">
          <span className="admin-user-avatar" aria-hidden>
            <Crown size={16} />
          </span>
          <div>
            <strong>{user.display_name || user.username}</strong>
            <span className="mono">@{user.username}</span>
          </div>
          <span className="credit-adjust-balance">
            {user.is_vip ? `VIP 至 ${formatDate(user.vip_expires_at, false)}` : '非 VIP'}
          </span>
        </div>
        <div className="form-field">
          <label htmlFor="vip-days">续期天数</label>
          <input
            id="vip-days"
            className="input input--number"
            type="number"
            min={0}
            max={3650}
            step={1}
            value={days}
            onChange={(event) => setDays(event.target.value)}
            data-autofocus
            disabled={saving}
          />
          <div className="vip-preset-row">
            {PRESETS.map((preset) => (
              <button
                key={preset.days}
                type="button"
                className="vip-preset"
                onClick={() => setDays(String(preset.days))}
                disabled={saving}
              >
                {preset.label}
              </button>
            ))}
            <button
              type="button"
              className="vip-preset vip-preset--clear"
              onClick={() => setDays('0')}
              disabled={saving}
            >
              清除 VIP
            </button>
          </div>
          <p className="field-hint">在现有到期日基础上累加；填 0 立即清除 VIP。</p>
        </div>
        <div className="form-field">
          <label htmlFor="vip-note">备注（可选）</label>
          <input
            id="vip-note"
            className="input"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="例如：活动赠送 / 线下付费"
            maxLength={200}
            disabled={saving}
          />
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
