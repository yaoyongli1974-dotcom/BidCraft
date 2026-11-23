import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminHealthTab } from './AdminHealthTab'
import { getAdminHealth } from '../../api/admin'
import type { AdminHealth } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

vi.mock('../../api/admin', () => ({ getAdminHealth: vi.fn() }))

const health: AdminHealth = {
  status: 'ok', version: '0.27.0', started_at: '2026-11-23T00:00:00Z', uptime_seconds: 3600,
  python: '3.13.0', platform: 'Linux', home: '/tmp/.bidcraft', db_size_bytes: 1024 * 1024,
  disk_free_bytes: 10 * 1024 * 1024 * 1024, web_ready: true, offline: false, docs_enabled: true,
  health_public: false, secure_cookie: false, model_configured: true, model_label: 'chat',
  embedding_configured: false, embedding_model: '', models: 2, knowledge_bases: 1, users: 3,
  auth_required: true, registration: true, wechat_login: false, wechat_pay: false,
}

describe('AdminHealthTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    vi.mocked(getAdminHealth).mockResolvedValue(health)
  })

  it('展示完整运行状态与三项指标口径', async () => {
    render(<AdminHealthTab />)
    expect(await screen.findByText('0.27.0')).toBeTruthy()
    expect(screen.getByText('人工修改率')).toBeTruthy()
    expect(screen.getByText('核对问题密度')).toBeTruthy()
    expect(screen.getByText('清单匹配率')).toBeTruthy()
    expect(screen.getAllByText('暂无全局聚合')).toHaveLength(3)
    expect(screen.getByText('对话模型')).toBeTruthy()
  })

  it('后端返回指标值时直接显示', async () => {
    vi.mocked(getAdminHealth).mockResolvedValue({
      ...health, metrics: { manual_edit_rate: '12.5%' },
    })
    render(<AdminHealthTab />)
    expect(await screen.findByText('12.5%')).toBeTruthy()
    expect(screen.getAllByText('暂无全局聚合')).toHaveLength(2)
  })

  it('加载失败可重试', async () => {
    vi.mocked(getAdminHealth).mockRejectedValueOnce(new Error('服务不可达'))
    render(<AdminHealthTab />)
    expect(await screen.findByRole('alert')).toHaveTextContent('服务不可达')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByText('0.27.0')
  })
})
