import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

function Exploding(): never {
  throw new Error('渲染炸了')
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    // React also logs the caught error; keep the test output readable.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps rendering children while nothing throws', () => {
    render(
      <ErrorBoundary>
        <p>正常内容</p>
      </ErrorBoundary>,
    )
    expect(screen.getByText('正常内容')).toBeInTheDocument()
  })

  it('shows a Chinese fallback with a reload action and collapsible summary', () => {
    render(
      <ErrorBoundary>
        <Exploding />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('heading', { name: '这个页面刚刚出错了' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '回到项目列表' })).toHaveAttribute('href', '/')
    expect(screen.getByText('错误摘要')).toBeInTheDocument()
    expect(screen.getByText(/渲染炸了/)).toBeInTheDocument()
  })
})
