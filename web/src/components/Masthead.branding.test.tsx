import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../context/useHealth', () => ({
  useHealth: () => ({ health: { offline: false, model_configured: true }, error: null, loading: false }),
}))
vi.mock('../context/useHelp', () => ({ useHelp: () => ({ openHelp: vi.fn() }) }))
vi.mock('../context/useAuth', () => ({ useAuth: () => ({ isSuperAdmin: false }) }))
vi.mock('./PendingApprovals', () => ({ PendingApprovals: () => null }))
vi.mock('./OfflineBanner', () => ({ OfflineBanner: () => null }))
vi.mock('./UserMenu', () => ({ UserMenu: () => null }))

import { Masthead } from './Masthead'

describe('Masthead 品牌定位', () => {
  it('展示 AI 技术标编制工作台，不再显示旧弱电文案', () => {
    render(
      <MemoryRouter>
        <Masthead />
      </MemoryRouter>,
    )
    expect(screen.getByText('AI 技术标编制工作台')).toBeTruthy()
    expect(screen.queryByText('弱电投标文件工作台')).toBeNull()
  })
})
