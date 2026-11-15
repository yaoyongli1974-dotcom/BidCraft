import { useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Masthead } from './Masthead'

export function AppShell() {
  const location = useLocation()

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
  }, [location.pathname])

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        跳到主要内容
      </a>
      <Masthead />
      <div id="main-content" className="app-main">
        <Outlet />
      </div>
      <footer className="app-footer">
        <span>BidCraft · 弱电投标文件工作台</span>
        <span className="app-footer-sep" aria-hidden>
          /
        </span>
        <span>本地优先 · 零遥测 · 数据不出内网</span>
      </footer>
    </div>
  )
}
