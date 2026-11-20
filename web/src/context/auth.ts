import { createContext } from 'react'
import type { User } from '../lib/types'

export interface AuthApi {
  user: User | null
  loading: boolean
  error: string | null
  isSuperAdmin: boolean
  canWrite: boolean
  login: (username: string, password: string) => Promise<User>
  register: (
    username: string,
    password: string,
    displayName?: string,
    acceptTerms?: boolean,
  ) => Promise<User>
  updateUser: (user: User) => void
  syncUser: () => Promise<void>
  logout: () => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  refresh: () => void
}

/** Kept apart from the provider so fast refresh can treat the provider as a
 * component-only module. */
export const AuthContext = createContext<AuthApi | null>(null)
