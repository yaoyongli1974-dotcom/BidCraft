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

  it('后端返回 global_metrics 时渲染全局指标区块', async () => {
    vi.mocked(getAdminHealth).mockResolvedValue({
      ...health,
      global_metrics: {
        projects: 12,
        active_users: 3,
        avg_score: 78.5,
        manual_edit_rate: '12.5%',
        unknown_key: null,
        projects_by_status: { draft: 8, active: 4 },
        jobs_by_status: { done: 20 },
      },
    })
    render(<AdminHealthTab />)
    expect(await screen.findByText('全局指标（数据库聚合）')).toBeTruthy()
    expect(screen.getByText('项目总数')).toBeTruthy()
    expect(screen.getByText('活跃用户数')).toBeTruthy()
    expect(screen.getByText('平均质量分')).toBeTruthy()
    expect(screen.getByText('项目状态分布')).toBeTruthy()
    expect(screen.getByText('草稿 8 · 进行中 4')).toBeTruthy()
    expect(screen.getByText('12.5%')).toBeTruthy()
    expect(screen.getByText('—')).toBeTruthy()
    // 原有单进程快照仍在。
    expect(screen.getByText('0.27.0')).toBeTruthy()
  })

  it('没有 global_metrics 时不展示全局指标区块', async () => {
    render(<AdminHealthTab />)
    await screen.findByText('0.27.0')
    expect(screen.queryByText('全局指标（数据库聚合）')).toBeNull()
  })

  it('加载失败可重试', async () => {
    vi.mocked(getAdminHealth).mockRejectedValueOnce(new Error('服务不可达'))
    render(<AdminHealthTab />)
    expect(await screen.findByRole('alert')).toHaveTextContent('服务不可达')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByText('0.27.0')
  })
})
