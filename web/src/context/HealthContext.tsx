import {
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { getHealth } from '../api/tender'
import { messageOf } from '../lib/http'
import type { Health } from '../lib/types'
import { HealthContext } from './health'

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
