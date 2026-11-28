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
      // 当前 /admin/health 的 10 键契约（6 个计数/分布 + 4 个队列健康键）。
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
    // workers_running 是「当前进程是否启动内置 worker 池」，不是生产故障开关。
    expect(screen.getByText('内置工作进程已启动')).toBeTruthy()
    expect(screen.getByText('排队任务数')).toBeTruthy()
    expect(screen.getByText('最老排队任务年龄（秒）')).toBeTruthy()
    expect(screen.getByText('陈旧运行中任务数')).toBeTruthy()
    expect(metricCell('内置工作进程已启动').textContent).toContain('已启动')
    expect(metricCell('排队任务数').textContent).toContain('5')
    expect(metricCell('最老排队任务年龄（秒）').textContent).toContain('12')
    expect(metricCell('陈旧运行中任务数').textContent).toContain('0')
    // 当前后端契约的 10 个键全部渲染；健康状态下不出现任何告警标记。
    const rows = document.querySelectorAll('.admin-global-metrics tbody tr')
    expect(rows).toHaveLength(10)
    expect(screen.queryByText('工作进程未运行')).toBeNull()
    expect(screen.queryByText('存在陈旧运行中任务')).toBeNull()
    expect(document.querySelector('.admin-global-metrics .badge--warn')).toBeNull()
    // 原有单进程快照仍在。
    expect(screen.getByText('0.27.0')).toBeTruthy()
  })

  it('内置 worker 未启动只作说明，陈旧运行中任务才显示告警', async () => {
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
    // API-only 部署的 workers_running=false 只显示说明，不是「生产故障」。
    expect(metricCell('内置工作进程已启动').textContent).toContain('未启动')
    expect(screen.getByText('未启动内置工作进程（可为 API-only 部署）')).toBeTruthy()
    expect(metricCell('内置工作进程已启动').querySelector('.badge--info')).toBeTruthy()
    expect(metricCell('内置工作进程已启动').querySelector('.badge--warn')).toBeNull()
    expect(screen.queryByText('工作进程未运行')).toBeNull()
    // 陈旧任务 > 0：数值与告警同时出现。
    expect(metricCell('陈旧运行中任务数').textContent).toContain('2')
    expect(screen.getByText('存在陈旧运行中任务')).toBeTruthy()
    // 正常数值不应误报，仍按既有格式渲染。
    expect(metricCell('排队任务数').textContent).toContain('3')
    expect(metricCell('最老排队任务年龄（秒）').textContent).toContain('1,800')
    expect(metricCell('陈旧运行中任务数').querySelector('.badge--warn')).toBeTruthy()
    expect(metricCell('排队任务数').querySelector('.badge--warn')).toBeNull()
  })

  it('仅 workers_running=false 且队列健康时不出现任何告警', async () => {
    vi.mocked(getAdminHealth).mockResolvedValue({
      ...health,
      global_metrics: {
        workers_running: false, queued_jobs: 0, oldest_queued_age_seconds: 0,
        stale_running_jobs: 0,
      },
    })
    render(<AdminHealthTab />)
    expect(await screen.findByText('全局指标（数据库聚合）')).toBeTruthy()
    expect(metricCell('内置工作进程已启动').textContent).toContain('未启动')
    expect(screen.getByText('未启动内置工作进程（可为 API-only 部署）')).toBeTruthy()
    expect(document.querySelector('.admin-global-metrics .badge--warn')).toBeNull()
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
