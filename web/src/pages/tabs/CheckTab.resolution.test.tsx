import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CheckTab } from './CheckTab'
import { getIssues } from '../../api/compliance'
import type { Issue } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

vi.mock('../useWorkspace', () => ({
  useWorkspace: () => ({
    ref: 'p1',
    basePath: '/app/projects/p1',
    project: { id: 'p1', checks_total: 1 },
    reloadProject: vi.fn(async () => ({})),
    canWrite: false,
  }),
}))
vi.mock('../../api/compliance', () => ({ getIssues: vi.fn(), runCheck: vi.fn() }))
vi.mock('../../context/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

describe('CheckTab 问题处置信息', () => {
  beforeEach(() => {
    clearQueryCache()
    vi.clearAllMocks()
  })

  it('只读展示服务端记录的处置人、处置时间和原因', async () => {
    const issue: Issue = {
      id: 'i1',
      code: 'CHK-1',
      level: 'major',
      status: 'handled',
      message: '盖章页缺失',
      passed: false,
      resolved_by: '张三',
      resolved_at: '2026-11-18T09:00:00.000Z',
      resolution_note: '已补充证书扫描件',
    }
    vi.mocked(getIssues).mockResolvedValue([issue])
    render(
      <MemoryRouter>
        <CheckTab />
      </MemoryRouter>,
    )
    const line = await screen.findByText(/处置人：张三/)
    expect(line.textContent).toContain('处置时间')
    expect(line.textContent).toContain('已补充证书扫描件')
    expect(screen.getByRole('button', { name: /核对/ })).toBeTruthy()
  })
})
