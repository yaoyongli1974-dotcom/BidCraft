import { useEffect, useState } from 'react'
import { WifiOff } from 'lucide-react'

/** 桌面客户端离线只读提示：由 apiFetch 通过自定义事件驱动。 */
export function OfflineBanner() {
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    const onOffline = () => setOffline(true)
    const onOnline = () => setOffline(false)
    window.addEventListener('bidcraft-offline', onOffline)
    window.addEventListener('bidcraft-online', onOnline)
    return () => {
      window.removeEventListener('bidcraft-offline', onOffline)
      window.removeEventListener('bidcraft-online', onOnline)
    }
  }, [])

  if (!offline) return null
  return (
    <div className="offline-banner" role="status">
      <WifiOff size={14} aria-hidden />
      <span>离线只读：服务器暂不可达，当前显示本机缓存内容；恢复连接后自动回到在线状态。</span>
    </div>
  )
}
