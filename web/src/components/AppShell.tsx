import { useEffect, useRef } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Masthead } from './Masthead'

export function AppShell() {
  const location = useLocation()
  const mainRef = useRef<HTMLDivElement>(null)
  const previousPath = useRef(location.pathname)

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
  }, [location.pathname])

  // Client-side navigation keeps focus on the link that was just used, which
  // screen readers read as "nothing changed". Move it into the new page heading
  // (or the main region while a lazy chunk is still loading).
  useEffect(() => {
    if (previousPath.current === location.pathname) return
    previousPath.current = location.pathname
    const main = mainRef.current
    if (!main) return
    const heading = main.querySelector<HTMLElement>('h2') || main.querySelector<HTMLElement>('h1')
    const target = heading || main
    if (heading) {
      heading.tabIndex = -1
      heading.addEventListener(
        'blur',
        () => {
          heading.removeAttribute('tabindex')
        },
        { once: true },
      )
    }
    target.focus({ preventScroll: true })
  }, [location.pathname])

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        跳到主要内容
      </a>
      <Masthead />
      <div id="main-content" className="app-main" ref={mainRef} tabIndex={-1}>
        <Outlet />
      </div>
      <footer className="app-footer">
        <span>BidCraft · AI 技术标编制工作台</span>
        <span className="app-footer-sep" aria-hidden>
          /
        </span>
        <span>本地优先 · 零遥测 · 数据不出内网</span>
      </footer>
    </div>
  )
}
