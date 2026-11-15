import { createContext } from 'react'

export type ToastKind = 'success' | 'error' | 'info' | 'warning'

export interface ToastApi {
  notify: (kind: ToastKind, title: string, description?: string) => void
  success: (title: string, description?: string) => void
  error: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
  warning: (title: string, description?: string) => void
  dismiss: (id: number) => void
}

/** Kept apart from the provider so fast refresh treats it as component-only. */
export const ToastContext = createContext<ToastApi | null>(null)
