import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { className } from '../lib/format'

interface ModalProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  title?: string
  eyebrow?: string
  size?: 'sm' | 'md' | 'lg'
  variant?: 'dialog' | 'drawer'
  footer?: ReactNode
  className?: string
}

export function Modal({
  open,
  onClose,
  children,
  title,
  eyebrow,
  size = 'md',
  variant = 'dialog',
  footer,
  className: extraClass,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const previousFocus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    previousFocus.current = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const timer = window.setTimeout(() => {
      const root = dialogRef.current
      const target =
        root?.querySelector<HTMLElement>('[data-autofocus]') ||
        root?.querySelector<HTMLElement>(
          'input:not([type="hidden"]), textarea, select, button, [href], [tabindex]:not([tabindex="-1"])',
        )
      target?.focus()
    }, 10)
    return () => {
      window.clearTimeout(timer)
      document.body.style.overflow = previousOverflow
      previousFocus.current?.focus?.()
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const root = dialogRef.current
      if (!root) return
      const nodes = Array.from(
        root.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((node) => node.offsetParent !== null)
      if (!nodes.length) {
        event.preventDefault()
        return
      }
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKey, true)
    return () => document.removeEventListener('keydown', handleKey, true)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className={className('overlay', variant === 'drawer' && 'overlay--drawer')}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        className={className(
          'modal',
          `modal--${size}`,
          variant === 'drawer' && 'drawer-panel',
          extraClass,
        )}
      >
        {(title || eyebrow) && (
          <header className={className('modal-head', variant === 'drawer' && 'drawer-head')}>
            <div>
              {eyebrow && <div className="eyebrow">{eyebrow}</div>}
              {title && (
                <h2 id={titleId} className="modal-title">
                  {title}
                </h2>
              )}
            </div>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
              <X size={18} />
            </button>
          </header>
        )}
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  )
}
