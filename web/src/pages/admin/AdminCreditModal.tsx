import { useEffect, useState } from 'react'
import { Coins } from 'lucide-react'
import type { User } from '../../lib/types'
import { adjustUserCredits, messageOf } from '../../lib/api'
import { formatNumber } from '../../lib/format'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'

export interface CreditModalState {
  user: User
}

export function AdminCreditModal({
  open,
  user,
  onClose,
  onSaved,
}: {
  open: boolean
  user: User
  onClose: () => void
  onSaved: (user: User, amount: number) => void
}) {
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setAmount('')
    setNote('')
    setSaving(false)
    setError(null)
  }, [open, user.id])

  const submit = async () => {
    const value = Math.round(Number(amount))
    if (!Number.isFinite(value) || value === 0) {
      setError('调整数量必须是非零整数；负数表示扣除。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const result = await adjustUserCredits(user.id, value, note.trim())
      onSaved(result.user, value)
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
      title="调整点数"
      eyebrow="管理员点数调整"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="credit-adjust-form"
            loading={saving}
          >
            确认调整
          </Button>
        </>
      }
    >
      <form
        id="credit-adjust-form"
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="credit-adjust-user">
          <span className="admin-user-avatar" aria-hidden>
            {(user.display_name || user.username).trim().slice(0, 1)}
          </span>
          <div>
            <strong>{user.display_name || user.username}</strong>
            <span className="mono">@{user.username}</span>
          </div>
          <span className="credit-adjust-balance">
            <Coins size={13} />
            {formatNumber(user.credits ?? 0)} 点
          </span>
        </div>
        <div className="form-field">
          <label htmlFor="credit-amount">调整数量</label>
          <input
            id="credit-amount"
            className="input input--number"
            type="number"
            step={1}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            placeholder="正数增加，负数扣除，例如 10 或 -5"
            data-autofocus
            disabled={saving}
          />
          <p className="field-hint">扣除后余额不能小于 0；后端会拒绝并返回原因。</p>
        </div>
        <div className="form-field">
          <label htmlFor="credit-note">备注（可选）</label>
          <input
            id="credit-note"
            className="input"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="例如：活动赠送 / 退款调整"
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
