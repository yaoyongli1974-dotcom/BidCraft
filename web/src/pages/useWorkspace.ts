import { useOutletContext } from 'react-router-dom'
import type { WorkspaceContextValue } from './WorkspacePage'

export function useWorkspace(): WorkspaceContextValue {
  const outlet = useOutletContext<WorkspaceContextValue | null>()
  if (!outlet) {
    throw new Error('工作区数据尚未加载')
  }
  return outlet
}
