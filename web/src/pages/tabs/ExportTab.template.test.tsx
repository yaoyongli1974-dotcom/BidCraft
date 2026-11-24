import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ExportTab } from './ExportTab'
import { getExportTemplates, setExportTemplate } from '../../api/project'
import { downloadBlob, exportProject } from '../../api/generation'
import { getRiskOverview } from '../../api/compliance'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  basePath: '/app/projects/p1',
  project: {
    id: 'p1', name: '测试项目', package: 'pkg', status: 'draft', my_role: 'owner' as string | undefined,
    outline: [{ id: 'n1', has_body: true, words: 100 }],
    stats: { written: 1, outline: 1 },
  },
  reloadProject: vi.fn(async () => ({})),
  canWrite: true,
}))
const toast = vi.hoisted(() => ({
  notify: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../context/useHealth', () => ({ useHealth: () => ({ health: null, error: null }) }))
vi.mock('../../api/compliance', () => ({ getRiskOverview: vi.fn() }))
vi.mock('../../api/generation', () => ({ exportProject: vi.fn(), downloadBlob: vi.fn() }))
vi.mock('../../api/project', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/project')>()
  return {
    ...actual,
    getExportTemplates: vi.fn(),
    setExportTemplate: vi.fn(),
  }
})
vi.mock('../../lib/hooks', () => ({
  useQuery: () => ({ data: undefined, loading: false, error: null, refresh: vi.fn() }),
  useLocalStorageState: () => [{}, vi.fn()],
}))

const PRESETS = [
  { id: 'standard', name: '标准投标文件', description: '通用格式' },
  { id: 'gov', name: '政府采购', description: '公文风格' },
]

describe('ExportTab 导出模板选择器', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workspace.canWrite = true
    vi.mocked(getRiskOverview).mockResolvedValue({} as never)
    vi.mocked(getExportTemplates).mockResolvedValue({ presets: PRESETS, current: PRESETS[0] })
    vi.mocked(setExportTemplate).mockImplementation(async (_ref, id) => ({
      presets: PRESETS,
      current: PRESETS.find((item) => item.id === id) || PRESETS[0],
    }))
  })

  function renderTab() {
    return render(<MemoryRouter><ExportTab /></MemoryRouter>)
  }

  it('列出后端预设并在切换时保存模板', async () => {
    renderTab()
    const select = await screen.findByLabelText('导出模板') as HTMLSelectElement
    await waitFor(() => expect(Array.from(select.options).map((o) => o.value)).toEqual(['standard', 'gov']))
    expect(select.value).toBe('standard')

    fireEvent.change(select, { target: { value: 'gov' } })
    await waitFor(() => expect(setExportTemplate).toHaveBeenCalledWith('p1', 'gov'))
    expect(toast.success).toHaveBeenCalled()
  })

  it('接口不可用时回退到默认模板且不阻断页面', async () => {
    vi.mocked(getExportTemplates).mockRejectedValue(new Error('接口未就绪'))
    renderTab()
    const select = await screen.findByLabelText('导出模板') as HTMLSelectElement
    await waitFor(() => expect(Array.from(select.options).map((o) => o.value)).toEqual(['standard']))
    expect(screen.getByRole('alert')).toHaveTextContent('接口未就绪')
  })

  it('导出请求不会携带 template 字段', async () => {
    vi.mocked(exportProject).mockResolvedValue({ blob: new Blob(['x']), filename: 'bid.docx' })
    renderTab()
    await screen.findByLabelText('导出模板')
    fireEvent.click(screen.getAllByRole('button', { name: '导出' })[0])
    await waitFor(() => expect(exportProject).toHaveBeenCalledWith('p1', 'docx', undefined))
    expect(downloadBlob).toHaveBeenCalled()
    expect(vi.mocked(exportProject).mock.calls[0][2]).toBeUndefined()
  })
})
