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

/** 按指标中文标签定位数值单元格，避免标签本身或别行的数字干扰断言。 */
function metricCell(label: string): HTMLElement {
  const cell = screen.getByText(label)
  const row = cell.closest('tr')
  if (!row) throw new Error(`找不到指标行：${label}`)
  const value = row.querySelector('td:last-child')
  if (!value) throw new Error(`找不到指标数值：${label}`)
  return value as HTMLElement
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
      // 六个既有键 + 四个新健康键；状态值也用后端真实口径。
      global_metrics: {
        projects_by_status: { intake: 3, drafting: 4, analyzed: 2, parsing: 1, ready: 2 },
        jobs_by_status: { queued: 5, running: 3, done: 20 },
        open_findings: 7,
        users: 9,
        projects: 12,
        jobs: 28,
        workers_running: true,
        queued_jobs: 5,
        oldest_queued_age_seconds: 12,
        stale_running_jobs: 0,
      },
    })
    render(<AdminHealthTab />)
    expect(await screen.findByText('全局指标（数据库聚合）')).toBeTruthy()
    // 六个既有键都有中文标签，其中 jobs 与 job 分词不再显示英文原码。
    expect(screen.getByText('项目状态分布')).toBeTruthy()
    expect(screen.getByText('任务状态分布')).toBeTruthy()
    expect(screen.getByText('未处置质量问题')).toBeTruthy()
    expect(screen.getByText('用户总数')).toBeTruthy()
    expect(screen.getByText('项目总数')).toBeTruthy()
    expect(screen.getByText('任务总数')).toBeTruthy()
    // 状态值复用规范标签：项目取 projectStatus()，任务取 JOB_STATUS。
    expect(screen.getByText('待导入 3 · 撰写中 4 · 已解析 2 · 解析中 1 · 可交付 2')).toBeTruthy()
    expect(screen.getByText('排队中 5 · 运行中 3 · 已完成 20')).toBeTruthy()
    // 四个新健康键按中文标签渲染，布尔值显示为 是/否。
    expect(screen.getByText('工作进程是否运行')).toBeTruthy()
    expect(screen.getByText('排队任务数')).toBeTruthy()
    expect(screen.getByText('最老排队任务年龄（秒）')).toBeTruthy()
    expect(screen.getByText('陈旧运行中任务数')).toBeTruthy()
    expect(metricCell('工作进程是否运行').textContent).toContain('是')
    expect(metricCell('排队任务数').textContent).toContain('5')
    expect(metricCell('最老排队任务年龄（秒）').textContent).toContain('12')
    expect(metricCell('陈旧运行中任务数').textContent).toContain('0')
    // 健康状态下不出现告警标记。
    expect(screen.queryByText('工作进程未运行')).toBeNull()
    expect(screen.queryByText('存在陈旧运行中任务')).toBeNull()
    // 原有单进程快照仍在。
    expect(screen.getByText('0.27.0')).toBeTruthy()
  })

  it('工作进程未运行且存在陈旧任务时显示告警', async () => {
    vi.mocked(getAdminHealth).mockResolvedValue({
      ...health,
      global_metrics: {
        projects: 12,
        jobs: 28,
        workers_running: false,
        queued_jobs: 3,
        oldest_queued_age_seconds: 1800,
        stale_running_jobs: 2,
      },
    })
    render(<AdminHealthTab />)
    expect(await screen.findByText('全局指标（数据库聚合）')).toBeTruthy()
    // 工作进程停摆：显示 否 并给出告警。
    expect(metricCell('工作进程是否运行').textContent).toContain('否')
    expect(screen.getByText('工作进程未运行')).toBeTruthy()
    // 陈旧任务 > 0：数值与告警同时出现。
    expect(metricCell('陈旧运行中任务数').textContent).toContain('2')
    expect(screen.getByText('存在陈旧运行中任务')).toBeTruthy()
    // 正常数值不应误报，仍按既有格式渲染。
    expect(metricCell('排队任务数').textContent).toContain('3')
    expect(metricCell('最老排队任务年龄（秒）').textContent).toContain('1,800')
    expect(metricCell('工作进程是否运行').querySelector('.badge--warn')).toBeTruthy()
    expect(metricCell('陈旧运行中任务数').querySelector('.badge--warn')).toBeTruthy()
    expect(metricCell('排队任务数').querySelector('.badge--warn')).toBeNull()
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
