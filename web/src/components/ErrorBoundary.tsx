import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, ChevronDown, RotateCcw } from 'lucide-react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

/**
 * Last line of defence around the router. Route-level `errorElement` handles
 * loader/render errors, but a provider or router crash would otherwise leave a
 * blank page; this keeps the shell readable and offers a way out.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the report in the console for local diagnosis; nothing is sent out.
    console.error('界面渲染失败', error, info.componentStack)
  }

  private reload = () => {
    window.location.reload()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const summary = error.stack || `${error.name}: ${error.message}`
    return (
      <div className="standalone-state standalone-state--full" role="alert">
        <div className="standalone-card error-boundary-card">
          <AlertTriangle size={30} strokeWidth={1.5} aria-hidden />
          <div className="eyebrow">界面错误</div>
          <h1>这个页面刚刚出错了</h1>
          <p>
            页面在渲染时遇到问题，已停止继续绘制，以免数据显示错乱。可以先重新加载；
            如果反复出现，请把下方错误摘要发给管理员。
          </p>
          <div className="error-actions">
            <button
              type="button"
              className="btn btn--primary btn--md"
              onClick={this.reload}
            >
              <RotateCcw size={15} />
              <span className="btn-label">重新加载</span>
            </button>
            <a href="/" className="btn btn--secondary btn--md">
              <span className="btn-label">回到项目列表</span>
            </a>
          </div>
          <details className="error-boundary-details">
            <summary>
              <ChevronDown size={14} aria-hidden />
              错误摘要
            </summary>
            <pre>{summary}</pre>
          </details>
        </div>
      </div>
    )
  }
}
