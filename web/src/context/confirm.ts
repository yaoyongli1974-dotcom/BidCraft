import { createContext, type ReactNode } from 'react'

export interface ConfirmOptions {
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'primary' | 'danger' | 'warning'
  note?: ReactNode
}

export type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>

/** Kept apart from the provider so fast refresh treats it as component-only. */
export const ConfirmContext = createContext<ConfirmFn | null>(null)
