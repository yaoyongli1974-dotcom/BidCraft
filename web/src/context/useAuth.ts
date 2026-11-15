import { useContext } from 'react'
import { AuthContext, type AuthApi } from './auth'

export function useAuth(): AuthApi {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return context
}
