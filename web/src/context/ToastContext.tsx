import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { className } from '../lib/format'

type ToastKind = 'success' | 'error' | 'info' | 'warning'

interface ToastItem {
  id: number
  kind: ToastKind
  title: string
  description?: string
}

interface ToastApi {
  notify: (kind: ToastKind, title: string, description?: string) => void
  success: (title: string, description?: string) => void
  error: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
  warning: (title: string, description?: string) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const ICONS = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
  warning: AlertTriangle,
} as const

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id))
    const timer = timers.current.get(id)
    if (timer) window.clearTimeout(timer)
    timers.current.delete(id)
  }, [])

  const notify = useCallback(
    (kind: ToastKind, title: string, description?: string) => {
      const id = nextId.current++
      setItems((current) => [...current.slice(-3), { id, kind, title, description }])
      const timer = window.setTimeout(() => dismiss(id), kind === 'error' ? 7600 : 5200)
      timers.current.set(id, timer)
    },
    [dismiss],
  )

  useEffect(() => {
    const currentTimers = timers.current
    return () => {
      currentTimers.forEach((timer) => window.clearTimeout(timer))
      currentTimers.clear()
    }
  }, [])

  const api: ToastApi = {
    notify,
    dismiss,
    success: (title, description) => notify('success', title, description),
    error: (title, description) => notify('error', title, description),
    info: (title, description) => notify('info', title, description),
    warning: (title, description) => notify('warning', title, description),
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-region" aria-live="polite" aria-relevant="additions">
        {items.map((item) => {
          const Icon = ICONS[item.kind]
          return (
            <div key={item.id} className={className('toast', `toast--${item.kind}`)}>
              <Icon size={18} className="toast-icon" aria-hidden />
              <div className="toast-copy">
                <div className="toast-title">{item.title}</div>
                {item.description && <p className="toast-desc">{item.description}</p>}
              </div>
              <button
                type="button"
                className="toast-close"
                onClick={() => dismiss(item.id)}
                aria-label="关闭提示"
              >
                <X size={14} />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast 必须在 ToastProvider 内使用')
  return context
}
