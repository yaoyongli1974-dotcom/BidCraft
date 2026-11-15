import {
  useCallback,
  useState,
  type ReactNode,
} from 'react'
import { AlertTriangle, ShieldQuestion } from 'lucide-react'
import { Modal } from '../components/Modal'
import { Button } from '../components/Button'
import { className } from '../lib/format'
import { ConfirmContext, type ConfirmFn, type ConfirmOptions } from './confirm'

interface PendingConfirm extends ConfirmOptions {
  resolve: (value: boolean) => void
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null)

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      setPending((current) => {
        if (current) current.resolve(false)
        return { ...options, resolve }
      })
    })
  }, [])

  const settle = (value: boolean) => {
    if (!pending) return
    pending.resolve(value)
    setPending(null)
  }

  const tone = pending?.tone || 'primary'
  const Icon = tone === 'danger' ? AlertTriangle : ShieldQuestion

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        open={Boolean(pending)}
        onClose={() => settle(false)}
        size="sm"
        title={pending?.title}
        eyebrow="请确认"
        className="confirm-modal"
      >
        {pending && (
          <div className="confirm-body">
            <div className={className('confirm-icon', `confirm-icon--${tone}`)} aria-hidden>
              <Icon size={22} strokeWidth={1.7} />
            </div>
            <div className="confirm-copy">
              {pending.description && <div className="confirm-desc">{pending.description}</div>}
              {pending.note && <div className="confirm-note">{pending.note}</div>}
            </div>
          </div>
        )}
        <div className="confirm-actions">
          <Button variant="secondary" onClick={() => settle(false)} data-autofocus>
            {pending?.cancelLabel || '取消'}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={() => settle(true)}
          >
            {pending?.confirmLabel || '确认'}
          </Button>
        </div>
      </Modal>
    </ConfirmContext.Provider>
  )
}
