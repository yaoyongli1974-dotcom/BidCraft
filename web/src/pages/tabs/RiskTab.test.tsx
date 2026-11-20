import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RiskTab } from './RiskTab'
import {
  getRequirements,
  getRiskOverview,
  updateIssueStatus,
  updateRequirementResponse,
} from '../../lib/api'
import type { Issue, RequirementResponse, RiskOverview } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  basePath: '/app/projects/p1',
  project: {
    id: 'p1',
    name: '测试项目',
    package: 'pkg',
    status: 'draft',
    my_role: 'owner' as string | undefined,
  },
  sources: [],
  sourcesLoading: false,
  packages: [],
  reloadProject: vi.fn(async () => workspace.project),
  reloadSources: vi.fn(async () => []),
  readOnly: false,
  canWrite: true,
  setUnsaved: vi.fn(),
}))

const toast = vi.hoisted(() => ({
  notify: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}))

const confirmStub = vi.hoisted(() => vi.fn(async () => true))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => confirmStub }))
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    getRiskOverview: vi.fn(),
    updateIssueStatus: vi.fn(),
    bulkIssueStatus: vi.fn(),
    runCheck: vi.fn(),
    getRequirements: vi.fn(),
    updateRequirementResponse: vi.fn(),
  }
})

function issue(overrides: Partial<Issue> & { id: string; code: string }): Issue {
  return {
    level: 'blocking',
    category: '资格',
    finding: '未提供安全生产许可证',
    message: '正文与材料清单均未找到对应证书。',
    recommendation: '补充扫描件并在资格章节写明证书编号。',
    status: 'open',
    node_id: 'ch-1',
    heading: '资格证明文件',
    location: '第三章 资格证明文件',
    ...overrides,
  }
}

const OPEN_BLOCKING = issue({ id: 'i1', code: 'B-001' })
const HANDLED_BLOCKING = issue({
  id: 'i2',
  code: 'B-002',
  finding: '已修复的阻断项',
  status: 'handled',
  resolved_by: '张三',
  resolved_at: '2026-11-18T09:00:00.000Z',
  resolution_note: '已补充证书扫描件',
})
const CRITICAL = issue({
  id: 'i3',
  code: 'C-001',
  level: 'critical',
  finding: '工期承诺与评分要求不一致',
  node_id: 'ch-2',
  heading: '工期与进度计划',
})
const MAJOR = issue({
  id: 'i4',
  code: 'M-001',
  level: 'major',
  finding: '项目班子配置偏少',
  node_id: null,
  heading: '项目管理机构',
})
const MINOR = issue({
  id: 'i5',
  code: 'N-001',
  level: 'info',
  finding: '建议补充图表编号',
  node_id: null,
  heading: '',
  location: '',
})

function makeOverview(overrides: Partial<RiskOverview> = {}): RiskOverview {
  return {
    project_id: 'p1',
    verdict: 'blocked',
    checked_at: '2026-11-19T08:00:00.000Z',
    gate: {
      blocked: true,
      blocking_open: 1,
      reason: '存在 1 项未处置的废标/阻断风险，请先在风险总览中处置。',
    },
    tiers: [
      { level: 'blocking', label: '废标/阻断', total: 2, open: 1, handled: 1, ignored: 0 },
      { level: 'critical', label: '严重', total: 1, open: 1, handled: 0, ignored: 0 },
      { level: 'major', label: '主要', total: 1, open: 1, handled: 0, ignored: 0 },
      { level: 'minor', label: '提示', total: 1, open: 1, handled: 0, ignored: 0 },
    ],
    by_category: [
      { category: '资格', total: 3, open: 2, blocking_open: 1 },
      { category: '技术', total: 2, open: 2, blocking_open: 0 },
    ],
    items: [OPEN_BLOCKING, HANDLED_BLOCKING, CRITICAL, MAJOR, MINOR],
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return (
    <div data-testid="location">
      {location.pathname}
      {'|'}
      {JSON.stringify(location.state ?? null)}
    </div>
  )
}

function renderRisk() {
  return render(
    <MemoryRouter initialEntries={['/app/projects/p1/risk']}>
      <Routes>
        <Route path="/app/projects/p1/risk" element={<RiskTab />} />
        <Route path="/app/projects/p1/draft" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

function itemCard(title: string): HTMLElement {
  const node = screen.getByText(title).closest('article')
  if (!node) throw new Error(`没有找到风险卡片：${title}`)
  return node as HTMLElement
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  workspace.canWrite = true
  workspace.readOnly = false
  workspace.project.my_role = 'owner'
  confirmStub.mockResolvedValue(true)
  vi.mocked(getRiskOverview).mockResolvedValue(makeOverview())
  vi.mocked(getRequirements).mockResolvedValue([])
  vi.mocked(updateIssueStatus).mockImplementation(async (_ref, id, status) => {
    const source = [OPEN_BLOCKING, HANDLED_BLOCKING, CRITICAL, MAJOR, MINOR].find(
      (item) => item.id === id,
    )
    return { ...(source || OPEN_BLOCKING), status }
  })
})

describe('RiskTab', () => {
  it('renders the four risk tiers with open/total and handled counts', async () => {
    renderRisk()

    const tierStrip = await screen.findByRole('group', { name: '按风险档筛选' })
    const blockingCard = within(tierStrip).getByRole('button', { name: /废标\/阻断/ })
    expect(within(blockingCard).getByText('1')).toBeInTheDocument()
    expect(within(blockingCard).getByText('共 2 项 · 已处置 1')).toBeInTheDocument()
    expect(within(blockingCard).getByText('必须处置后才能导出')).toBeInTheDocument()
    expect(within(tierStrip).getByRole('button', { name: /严重/ })).toBeInTheDocument()
    expect(within(tierStrip).getByRole('button', { name: /主要/ })).toBeInTheDocument()
    expect(within(tierStrip).getByRole('button', { name: /提示/ })).toBeInTheDocument()

    // 未处置阻断项存在时，阻断档加最醒目的 alert 样式。
    expect(blockingCard.className).toContain('risk-tier--alert')
    expect(screen.getByText(/存在 1 项未处置的废标\/阻断风险/)).toBeInTheDocument()
  })

  it('filters handled items out when 只看未处置 is on', async () => {
    renderRisk()

    expect(await screen.findByText(HANDLED_BLOCKING.finding as string)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '只看未处置' }))

    await waitFor(() => {
      expect(screen.queryByText(HANDLED_BLOCKING.finding as string)).not.toBeInTheDocument()
    })
    expect(screen.getByText(OPEN_BLOCKING.finding as string)).toBeInTheDocument()
  })

  it('marks an issue as handled through the PATCH endpoint', async () => {
    const update = vi.mocked(updateIssueStatus)
    renderRisk()

    const card = await waitFor(() => itemCard(OPEN_BLOCKING.finding as string))
    fireEvent.click(within(card).getByRole('button', { name: '标记已处理' }))

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith('p1', 'i1', 'handled')
    })
    expect(toast.success).toHaveBeenCalled()
  })

  it('requires a reason before ignoring an issue', async () => {
    const update = vi.mocked(updateIssueStatus)
    renderRisk()

    const card = await waitFor(() => itemCard(OPEN_BLOCKING.finding as string))
    fireEvent.click(within(card).getByRole('button', { name: '忽略' }))

    const confirmButton = await screen.findByRole('button', { name: '确认忽略' })
    expect(confirmButton).toBeDisabled()
    expect(update).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('忽略原因'), {
      target: { value: '该模板条款已在投标函中承诺' },
    })
    expect(confirmButton).toBeEnabled()
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(
        'p1',
        'i1',
        'ignored',
        '该模板条款已在投标函中承诺',
      )
    })
  })

  it('disables handling actions for viewers but keeps jump available', async () => {
    workspace.canWrite = false
    workspace.readOnly = true
    renderRisk()

    const card = await waitFor(() => itemCard(OPEN_BLOCKING.finding as string))
    expect(within(card).getByRole('button', { name: '标记已处理' })).toBeDisabled()
    expect(within(card).getByRole('button', { name: '忽略' })).toBeDisabled()
    expect(within(card).getByRole('button', { name: '跳转修改' })).toBeEnabled()
    expect(screen.getByText(/你是只读成员/)).toBeInTheDocument()
  })

  it('navigates to the draft tab with the chapter anchor and quote', async () => {
    vi.mocked(getRiskOverview).mockResolvedValue(
      makeOverview({
        items: [
          issue({
            id: 'i9',
            code: 'B-009',
            finding: '★条款未响应',
            node_id: 'ch-7',
            heading: '技术响应',
            location: '第五章 技术响应',
            evidence: { quote: '★ 投标人须逐条响应，不得负偏离' },
          }),
        ],
      }),
    )
    renderRisk()

    const card = await waitFor(() => itemCard('★条款未响应'))
    fireEvent.click(within(card).getByRole('button', { name: '跳转修改' }))

    const location = await screen.findByTestId('location')
    expect(location.textContent).toContain('/app/projects/p1/draft')
    const state = JSON.parse(location.textContent?.split('|')[1] || 'null') as {
      focusNode?: string
      quote?: string
    }
    expect(state.focusNode).toBe('ch-7')
    expect(state.quote).toBe('★ 投标人须逐条响应，不得负偏离')
  })

  it('shows 未定位到章节 when an issue has no node anchor', async () => {
    renderRisk()

    // 主要档默认折叠，先按档筛选把它展开。
    const filterGroup = await screen.findByRole('group', { name: '风险档筛选' })
    fireEvent.click(within(filterGroup).getByRole('button', { name: /主要/ }))

    const card = await waitFor(() => itemCard(MAJOR.finding as string))
    expect(within(card).getByText(/未定位到章节/)).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: '跳转修改' })).toBeDisabled()
  })
})

function requirement(overrides: Partial<RequirementResponse> = {}): RequirementResponse {
  return {
    id: 'r1',
    kind: 'substantive',
    marker: '★',
    text: '★ 投标人须提供近三年同类项目业绩不少于3项并附合同复印件',
    mandatory: true,
    source_quote: '★ 投标人须提供近三年同类项目业绩不少于3项并附合同复印件',
    response_status: 'pending',
    response_note: '',
    responded_by: '',
    responded_at: null,
    section_id: null,
    node_id: null,
    section_heading: '',
    auto_covered: false,
    ...overrides,
  }
}

/** 实质性条款区块：★ 未响应最优先，人工确认必须留痕。 */
describe('RiskTab substantive clauses', () => {
  it('shows an unanswered ★ clause as top priority', async () => {
    const clause = requirement({ id: 'r1' })
    vi.mocked(getRequirements).mockResolvedValue([clause])
    renderRisk()

    const panel = await screen.findByRole('region', { name: '实质性条款响应' })
    expect(within(panel).getByText(/1 条 ★ 条款未响应/)).toBeInTheDocument()
    const card = within(panel).getByText(clause.text).closest('article') as HTMLElement
    expect(within(card).getByText('★')).toBeInTheDocument()
    expect(within(card).getByText('最高优先级')).toBeInTheDocument()
    expect(within(card).getByText('待确认')).toBeInTheDocument()
  })

  it('requires a reason before marking a clause as not applicable', async () => {
    const clause = requirement({ id: 'r2', marker: '▲', text: '▲ 项目经理须具备一级建造师执业资格' })
    vi.mocked(getRequirements).mockResolvedValue([clause])
    vi.mocked(updateRequirementResponse).mockResolvedValue({
      ...clause,
      response_status: 'na',
      response_note: '本次为设备采购，不涉及项目经理执业资格。',
      responded_by: '张三',
      responded_at: '2026-11-20T10:00:00+08:00',
    })
    renderRisk()

    const panel = await screen.findByRole('region', { name: '实质性条款响应' })
    fireEvent.click(within(panel).getByRole('button', { name: '标记不适用' }))

    const reason = await screen.findByLabelText('不适用原因')
    fireEvent.change(reason, { target: { value: '无' } })
    fireEvent.click(screen.getByRole('button', { name: '确认不适用' }))
    expect(await screen.findByText(/至少 4 个字/)).toBeInTheDocument()
    expect(updateRequirementResponse).not.toHaveBeenCalled()

    fireEvent.change(reason, { target: { value: '本次为设备采购，不涉及项目经理执业资格。' } })
    fireEvent.click(screen.getByRole('button', { name: '确认不适用' }))
    await waitFor(() =>
      expect(updateRequirementResponse).toHaveBeenCalledWith(
        'p1',
        'r2',
        'na',
        '本次为设备采购，不涉及项目经理执业资格。',
      ),
    )
  })

  it('links a covered clause to the chapter that already responds to it', async () => {
    const covered = requirement({
      id: 'r3',
      marker: '★',
      text: '★ 投标人须提供近三年同类项目业绩不少于3项',
      source_quote: '★ 投标人须提供近三年同类项目业绩不少于3项',
      auto_covered: true,
      section_id: 'sec-9',
      node_id: 'ch-9',
      section_heading: '商务响应',
    })
    vi.mocked(getRequirements).mockResolvedValue([covered])
    renderRisk()

    const panel = await screen.findByRole('region', { name: '实质性条款响应' })
    fireEvent.click(within(panel).getByRole('button', { name: /正文已覆盖/ }))

    const card = (await within(panel).findByText(/正文已覆盖于「商务响应」/)).closest(
      'article',
    ) as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: '查看章节' }))

    const location = await screen.findByTestId('location')
    expect(location.textContent).toContain('/app/projects/p1/draft')
    const state = JSON.parse(location.textContent?.split('|')[1] || 'null') as {
      focusNode?: string
      quote?: string
    }
    expect(state.focusNode).toBe('ch-9')
    expect(state.quote).toBe('★ 投标人须提供近三年同类项目业绩不少于3项')
  })

  it('disables clause confirmation for viewers', async () => {
    workspace.canWrite = false
    workspace.readOnly = true
    workspace.project.my_role = 'viewer'
    vi.mocked(getRequirements).mockResolvedValue([requirement({ id: 'r4' })])
    renderRisk()

    const panel = await screen.findByRole('region', { name: '实质性条款响应' })
    expect(within(panel).getByRole('button', { name: '标记已响应' })).toBeDisabled()
    expect(within(panel).getByRole('button', { name: '标记不适用' })).toBeDisabled()
    expect(within(panel).queryByRole('button', { name: '查看章节' })).toBeNull()
  })
})
