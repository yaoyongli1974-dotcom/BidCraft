import { useContext } from 'react'
import { HealthContext, type HealthApi } from './health'

export function useHealth(): HealthApi {
  const context = useContext(HealthContext)
  if (!context) throw new Error('useHealth 必须在 HealthProvider 内使用')
  return context
}
