import { useContext } from 'react'
import { ToastContext, type ToastApi } from './toast'

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast 必须在 ToastProvider 内使用')
  return context
}
