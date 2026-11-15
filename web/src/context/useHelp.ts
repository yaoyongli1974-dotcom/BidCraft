import { useContext } from 'react'
import { HelpContext, type HelpApi } from './help'

export function useHelp(): HelpApi {
  const context = useContext(HelpContext)
  if (!context) throw new Error('useHelp 必须在 HelpProvider 内使用')
  return context
}
