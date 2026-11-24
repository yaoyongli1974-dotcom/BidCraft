import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_EXPORT_TEMPLATE_ID,
  getExportTemplates,
  normalizeExportTemplates,
  setExportTemplate,
} from './project'
import { ApiError } from '../lib/http'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PRESETS = [
  { id: 'standard', name: '标准投标文件', description: '通用格式' },
  { id: 'gov', name: '政府采购', description: '公文风格' },
]

describe('export templates API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('GET 读取预设与当前模板', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({ presets: PRESETS, current: PRESETS[1] }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await getExportTemplates('p/1')
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%2F1/export-templates')
    expect(result.presets.map((item) => item.id)).toEqual(['standard', 'gov'])
    expect(result.current.id).toBe('gov')
  })

  it('旧后端 404 时回退到后端默认 standard 预设', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Not Found' }, 404)))
    const result = await getExportTemplates('p1')
    expect(result.current.id).toBe(DEFAULT_EXPORT_TEMPLATE_ID)
    expect(result.presets).toHaveLength(1)
  })

  it('SPA 兜底返回 HTML 时同样回退', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<!doctype html>', { status: 200 })))
    const result = await getExportTemplates('p1')
    expect(result.current.id).toBe(DEFAULT_EXPORT_TEMPLATE_ID)
  })

  it('PUT 保存模板 id 并回显最新选择', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({ presets: PRESETS, current: PRESETS[0] }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await setExportTemplate('p1', 'standard')
    expect(fetchMock.mock.calls[0][0]).toBe('/projects/p1/export-template')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'PUT' })
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ id: 'standard' })
    expect(result.current.id).toBe('standard')
  })

  it('未知模板 400 由 ApiError 透出，不吞掉错误', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: '未知导出模板：bad' }, 400)))
    await expect(setExportTemplate('p1', 'bad')).rejects.toBeInstanceOf(ApiError)
  })

  it('normalize 把 default/空 id 回退到 standard，且当前值一定在选项里', () => {
    const result = normalizeExportTemplates({ presets: [], current: { id: 'default' } })
    expect(result.current.id).toBe(DEFAULT_EXPORT_TEMPLATE_ID)
    expect(result.presets.some((item) => item.id === DEFAULT_EXPORT_TEMPLATE_ID)).toBe(true)
  })
})
