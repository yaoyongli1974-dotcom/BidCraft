import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  ApiError,
  UNAUTHORIZED_EVENT,
  changePassword as changePasswordRequest,
  getMe,
  login as loginRequest,
  logout as logoutRequest,
  register as registerRequest,
  messageOf,
} from '../lib/api'
import type { User } from '../lib/types'
import { clearQueryCache } from '../lib/queryCache'

interface AuthApi {
  user: User | null
  loading: boolean
  error: string | null
  isSuperAdmin: boolean
  canWrite: boolean
  login: (username: string, password: string) => Promise<User>
  register: (username: string, password: string, displayName?: string) => Promise<User>
  updateUser: (user: User) => void
  syncUser: () => Promise<void>
  logout: () => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  refresh: () => void
}

const AuthContext = createContext<AuthApi | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const redirecting = useRef(false)

  useEffect(() => {
    let active = true
    // A new session may belong to another account; never reuse cached API data.
    clearQueryCache()
    setLoading(true)
    setError(null)
    getMe()
      .then((result) => {
        if (!active) return
        setUser(result.user)
      })
      .catch((err: unknown) => {
        if (!active) return
        if (err instanceof ApiError && err.status === 401) {
          setUser(null)
        } else {
          setUser(null)
          setError(messageOf(err))
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [nonce])

  useEffect(() => {
    const onUnauthorized = () => {
      clearQueryCache()
      setUser(null)
      setLoading(false)
      if (redirecting.current) return
      const path = window.location.pathname
      if (path === '/login') return
      redirecting.current = true
      const next = `${window.location.pathname}${window.location.search}`
      const params = new URLSearchParams({ next, reason: 'expired' })
      window.location.replace(`/login?${params.toString()}`)
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const result = await loginRequest(username, password)
    clearQueryCache()
    setUser(result.user)
    setError(null)
    return result.user
  }, [])

  const register = useCallback(
    async (username: string, password: string, displayName = '') => {
      const result = await registerRequest(username, password, displayName)
      clearQueryCache()
      setUser(result.user)
      setError(null)
      return result.user
    },
    [],
  )

  const updateUser = useCallback((next: User) => {
    setUser(next)
  }, [])

  const syncUser = useCallback(async () => {
    const result = await getMe()
    setUser(result.user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await logoutRequest()
    } catch {
      // Clearing the local session is still the right outcome if the server
      // session has already expired or the service is unavailable.
    } finally {
      clearQueryCache()
      setUser(null)
    }
  }, [])

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await changePasswordRequest(currentPassword, newPassword)
    clearQueryCache()
    setUser(null)
  }, [])

  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  const api: AuthApi = {
    user,
    loading,
    error,
    isSuperAdmin: user?.role === 'super_admin',
    canWrite: Boolean(user && user.role !== 'viewer'),
    login,
    register,
    updateUser,
    syncUser,
    logout,
    changePassword,
    refresh,
  }

  return <AuthContext.Provider value={api}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthApi {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return context
}
