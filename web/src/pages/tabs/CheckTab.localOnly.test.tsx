import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CheckTab } from './CheckTab'
import { getIssues } from '../../api/compliance'
import { clearQueryCache } from '../../lib/queryCache'

vi.mock('../useWorkspace', () => ({
  useWorkspace: () => ({
    ref: 'p1',
    basePath: '/app/projects/p1',
    project: { id: 'p1', checks_total: 12 },
    reloadProject: vi.fn(async () => ({})),
    canWrite: false,
  }),
}))
vi.mock('../../api/compliance', () => ({ getIssues: vi.fn(), runCheck: vi.fn() }))
vi.mock('../../context/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

describe('CheckTab 本机记录不得冒充后端结论', () => {
  beforeEach(() => {
    window.localStorage.clear()
    clearQueryCache()
    vi.clearAllMocks()
  })

  it('仅有 localStorage 的 pass 记录且后端无问题时显示中性「尚未核对」', async () => {
    window.localStorage.setItem(
      'bidcraft:check-results',
      JSON.stringify({ p1: { verdict: 'pass', issues: 0, at: '2026-11-25T10:00:00.000Z' } }),
    )
    vi.mocked(getIssues).mockResolvedValue([])
    render(<MemoryRouter><CheckTab /></MemoryRouter>)

    expect(await screen.findByText('还没有核对结论')).toBeTruthy()
    expect(screen.getByText('核对结论 · 尚未核对')).toBeTruthy()
    expect(document.querySelector('.verdict-banner--success')).toBeNull()
    expect(screen.queryByText('核对通过')).toBeNull()
    expect(screen.queryByText('没有需要处理的问题')).toBeNull()
  })

  it('仅有 localStorage 的 warn 记录也不展示已保存结论横幅', async () => {
    window.localStorage.setItem(
      'bidcraft:check-results',
      JSON.stringify({ p1: { verdict: 'warn', issues: 2, at: '2026-11-24T10:00:00.000Z' } }),
    )
    vi.mocked(getIssues).mockResolvedValue([])
    render(<MemoryRouter><CheckTab /></MemoryRouter>)

    expect(await screen.findByText('还没有核对结论')).toBeTruthy()
    expect(screen.getByText('核对结论 · 尚未核对')).toBeTruthy()
    expect(document.querySelector('.verdict-banner--success')).toBeNull()
  })
})
