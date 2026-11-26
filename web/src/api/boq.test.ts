import { afterEach, describe, expect, it, vi } from 'vitest'
import { listBoqVersions, normalizeBoqQuoteVersion } from './boq'

function jsonFetch(payload: unknown) {
  const mock = vi.fn(
    async (_input: string, _init?: RequestInit) =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

const backendExplain = [
  {
    index: 1,
    applicable: true,
    conditions: [
      { field: 'priced', op: 'gte', value: 100, actual: 120, hit: true, missing: false },
      { field: 'package_id', op: 'in', value: ['pkg-1'], actual: 'pkg-1', hit: true, missing: false },
    ],
    missing_data: [],
  },
]

describe('报价版本 approval_explain 归一化', () => {
  it('保留后端回传的逐阶段解释（不是本地镜像）', () => {
    const version = normalizeBoqQuoteVersion({
      id: 'v1',
      name: '版本一',
      approval_explain: backendExplain,
    })
    expect(version).not.toBeNull()
    expect(version!.approval_explain).toEqual(backendExplain)
  })

  it('列表接口同样透传后端解释，坏条目丢弃', () => {
    const fetchMock = jsonFetch([
      { id: 'v2', approval_explain: backendExplain },
      { id: 'v3', approval_explain: [null, { index: 'bad', conditions: 'oops' }] },
    ])
    return listBoqVersions('p 1').then((versions) => {
      expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%201/boq/versions')
      expect(versions[0].approval_explain).toEqual(backendExplain)
      expect(versions[1].approval_explain).toEqual([
        { index: 0, applicable: false, conditions: [], missing_data: [] },
      ])
    })
  })

  it('旧后端缺字段时解释为空数组，由调用方回退本地镜像', () => {
    expect(normalizeBoqQuoteVersion({ id: 'v4' })!.approval_explain).toEqual([])
  })
})
