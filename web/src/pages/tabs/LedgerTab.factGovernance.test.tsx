import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getLedger, updateFact } from '../../api/ledger'
import type { Fact } from '../../lib/types'
import { clearQueryCache } from '../../lib/queryCache'
import { LedgerTab } from './LedgerTab'

const workspace = vi.hoisted(() => ({
  ref: 'p1',
  project: { id: 'p1', name: '测试项目', my_role: 'owner' as string | undefined },
  reloadProject: vi.fn(async () => ({})),
  setUnsaved: vi.fn(),
  canWrite: true,
  readOnly: false,
}))

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('../useWorkspace', () => ({ useWorkspace: () => workspace }))
vi.mock('../../context/useToast', () => ({ useToast: () => toast }))
vi.mock('../../context/useConfirm', () => ({ useConfirm: () => vi.fn(async () => true) }))
vi.mock('../../lib/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/hooks')>()
  return { ...actual, useUnsavedGuard: vi.fn() }
})
vi.mock('../../api/ledger', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/ledger')>()
  return { ...actual, getLedger: vi.fn(), updateFact: vi.fn() }
})

function ledgerFact(overrides: Partial<Fact> = {}): Fact {
  return {
    key: 'duration',
    label: '工期',
    value: '180 天',
    status: 'confirmed',
    required: true,
    confirmed_by: 'alice',
    confirmed_at: '2026-11-20T10:00:00Z',
    source_file_id: 'src-1',
    source_quote: '工期 180 日历天',
    source_page: 12,
    ...overrides,
  }
}

function renderTab() {
  return render(
    <MemoryRouter>
      <LedgerTab />
    </MemoryRouter>,
  )
}

describe('LedgerTab 事实治理', () => {
  beforeEach(() => {
    clearQueryCache()
    vi.clearAllMocks()
    workspace.canWrite = true
    workspace.readOnly = false
    vi.mocked(getLedger).mockResolvedValue({
      facts: [ledgerFact()],
      materials: [],
      parameters: [],
      scoring: [],
      requirements: [],
      risks: [],
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ items: [] }),
        text: async () => '{"items":[]}',
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('编辑默认手工录入，要求来源证据，并把服务端盖章回写到表格', async () => {
    vi.mocked(updateFact).mockResolvedValue({
      key: 'duration',
      value: '190 天',
      status: 'manual',
      confirmed_by: 'bob',
      confirmed_at: '2026-11-25T09:00:00Z',
      source_file_id: 'src-2',
      source_file_name: '招标文件.pdf',
      source_quote: '工期 190 日历天',
      source_page: 15,
      stale_sections: [],
    })
    renderTab()
    expect(await screen.findByText('工期')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /180 天/ }))
    const status = screen.getByLabelText('事实状态') as HTMLSelectElement
    expect(status.value).toBe('manual')
    expect(screen.getByLabelText('来源文件 ID：工期')).toBeTruthy()
    expect((screen.getByLabelText('原文摘录：工期') as HTMLInputElement).value).toBe('工期 180 日历天')

    fireEvent.change(screen.getByLabelText('编辑工期取值'), { target: { value: '190 天' } })
    fireEvent.change(screen.getByLabelText('原文摘录：工期'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('来源文件 ID：工期'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(updateFact).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toContain('来源文件')

    fireEvent.change(screen.getByLabelText('原文摘录：工期'), {
      target: { value: '工期 190 日历天' },
    })
    fireEvent.change(screen.getByLabelText('来源文件 ID：工期'), {
      target: { value: 'src-2' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(updateFact).toHaveBeenCalledWith('p1', 'duration', '190 天', 'manual', {
        provenance_kind: 'tender_extract',
        source_file_id: 'src-2',
        source_quote: '工期 190 日历天',
        source_page: 12,
      }),
    )
    expect(await screen.findByText('bob')).toBeTruthy()
    expect(screen.getByText('招标文件.pdf')).toBeTruthy()
    expect(screen.getByText('ID src-2')).toBeTruthy()
    expect(screen.getByText(/190 日历天/)).toBeTruthy()
  })

  it('缺失项可按用户补充保存，无需伪造招标文件原文与页码', async () => {
    vi.mocked(getLedger).mockResolvedValue({
      facts: [
        ledgerFact({
          value: '',
          provenance_kind: 'user_supplied',
          supplier_note: '',
          source_file_id: null,
          source_quote: null,
          source_page: null,
        }),
      ],
      materials: [],
      parameters: [],
      scoring: [],
      requirements: [],
      risks: [],
    })
    vi.mocked(updateFact).mockResolvedValue({
      key: 'duration',
      value: '190 天',
      status: 'manual',
      provenance_kind: 'user_supplied',
      supplier_note: '招标文件未给出工期',
      stale_sections: [],
    })
    renderTab()
    await screen.findByText('工期')
    fireEvent.click(screen.getByRole('button', { name: /未填写/ }))
    expect(screen.queryByLabelText('来源文件 ID：工期')).toBeNull()
    expect((screen.getByLabelText('来源类型：工期') as HTMLSelectElement).value).toBe(
      'user_supplied',
    )

    fireEvent.change(screen.getByLabelText('编辑工期取值'), { target: { value: '190 天' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(updateFact).not.toHaveBeenCalled()
    expect(
      screen
        .getAllByRole('alert')
        .map((node) => node.textContent || '')
        .join(' ')
    ).toContain('来源说明')

    fireEvent.change(screen.getByLabelText('补充说明：工期'), {
      target: { value: '招标文件未给出工期' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(updateFact).toHaveBeenCalledWith('p1', 'duration', '190 天', 'manual', {
        provenance_kind: 'user_supplied',
        supplier_note: '招标文件未给出工期',
      }),
    )
  })

  it('只有服务端返回陈旧引用时才显示真实的待复核告警', async () => {
    vi.mocked(updateFact).mockResolvedValue({
      key: 'duration',
      value: '200 天',
      status: 'manual',
      source_quote: '工期 200 日历天',
      stale_sections: [{ node_id: 'n1', heading: '第一章', stale_value: '180 天' }],
    })
    renderTab()
    await screen.findByText('工期')
    fireEvent.click(screen.getByRole('button', { name: /180 天/ }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('第一章')
    expect(toast.warning).toHaveBeenCalledWith(
      '已标记待复核的章节',
      expect.stringContaining('第一章'),
    )
  })

  it('只读成员不能进入编辑，也不能看到来源证据输入框', async () => {
    workspace.canWrite = false
    workspace.readOnly = true
    renderTab()
    await screen.findByText('工期')
    const valueButton = screen.getByRole('button', { name: /180 天/ }) as HTMLButtonElement
    expect(valueButton.disabled).toBe(true)
    expect(screen.queryByLabelText('原文摘录：工期')).toBeNull()
  })
})
