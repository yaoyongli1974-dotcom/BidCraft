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

  it('后端返回 global_metrics 时按真实后端键渲染全局指标区块', async () => {
    vi.mocked(getAdminHealth).mockResolvedValue({
      ...health,
      // bidcraft/health.py 实际返回的六个键，状态值也用后端真实口径。
      global_metrics: {
        projects_by_status: { intake: 3, drafting: 4, analyzed: 2, parsing: 1, ready: 2 },
        jobs_by_status: { queued: 5, running: 3, done: 20 },
        open_findings: 7,
        users: 9,
        projects: 12,
        jobs: 28,
      },
    })
    render(<AdminHealthTab />)
    expect(await screen.findByText('全局指标（数据库聚合）')).toBeTruthy()
    // 六个真实键都有中文标签，其中 jobs 与 job 分词不再显示英文原码。
    expect(screen.getByText('项目状态分布')).toBeTruthy()
    expect(screen.getByText('任务状态分布')).toBeTruthy()
    expect(screen.getByText('未处置质量问题')).toBeTruthy()
    expect(screen.getByText('用户总数')).toBeTruthy()
    expect(screen.getByText('项目总数')).toBeTruthy()
    expect(screen.getByText('任务总数')).toBeTruthy()
    // 状态值复用规范标签：项目取 projectStatus()，任务取 JOB_STATUS。
    expect(screen.getByText('待导入 3 · 撰写中 4 · 已解析 2 · 解析中 1 · 可交付 2')).toBeTruthy()
    expect(screen.getByText('排队中 5 · 运行中 3 · 已完成 20')).toBeTruthy()
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
