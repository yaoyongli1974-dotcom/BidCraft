import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { getHealth, messageOf } from '../lib/api'
import type { Health } from '../lib/types'

interface HealthApi {
  health: Health | null
  error: string | null
  loading: boolean
  refresh: () => void
}

const HealthContext = createContext<HealthApi | null>(null)

export function HealthProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    getHealth()
      .then((data) => {
        if (!active) return
        setHealth(data)
        setError(null)
      })
      .catch((err) => {
        if (!active) return
        setHealth(null)
        setError(messageOf(err))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [nonce])

  const refresh = useCallback(() => setNonce((value) => value + 1), [])
  return (
    <HealthContext.Provider value={{ health, error, loading, refresh }}>
      {children}
    </HealthContext.Provider>
  )
}

export function useHealth(): HealthApi {
  const context = useContext(HealthContext)
  if (!context) throw new Error('useHealth 必须在 HealthProvider 内使用')
  return context
}
