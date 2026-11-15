import { createContext } from 'react'
import type { Health } from '../lib/types'

export interface HealthApi {
  health: Health | null
  error: string | null
  loading: boolean
  refresh: () => void
}

/** Kept apart from the provider so fast refresh treats it as component-only. */
export const HealthContext = createContext<HealthApi | null>(null)
