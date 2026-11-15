import { useContext } from 'react'
import { ConfirmContext, type ConfirmFn } from './confirm'

export function useConfirm(): ConfirmFn {
  const context = useContext(ConfirmContext)
  if (!context) throw new Error('useConfirm 必须在 ConfirmProvider 内使用')
  return context
}
