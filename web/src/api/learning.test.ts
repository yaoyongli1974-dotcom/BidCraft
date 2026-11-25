import { describe, expect, it, vi } from 'vitest'
import { getProjectLessons, lessonQuery, normalizeLesson, normalizeLessonsResponse } from './learning'

describe('建议经验客户端', () => {
  it('查询参数只包含 node_id / limit，空值不拼接', () => {
    expect(lessonQuery()).toBe('')
    expect(lessonQuery({ node_id: '  ', limit: 0 })).toBe('')
    expect(lessonQuery({ node_id: ' n1 ', limit: 3.6 })).toBe('?node_id=n1&limit=4')
  })

  it('单条建议缺字段时补空串 / null，无效条目被丢弃', () => {
    expect(normalizeLesson('bad')).toBeNull()
    expect(normalizeLesson({ text: '无 id' })).toBeNull()
    const lesson = normalizeLesson({
      id: 'l1',
      source: null,
      kind: undefined,
      text: 42,
      node_id: null,
      created_at: null,
      author: 7,
      status: '未审核建议经验',
    })!
    expect(lesson).toEqual({
      id: 'l1',
      source: '',
      kind: '',
      text: '',
      node_id: null,
      created_at: null,
      author: '',
      status: '未审核建议经验',
    })
  })

  it('响应归一：默认状态为未审核建议经验，不推断已验证', () => {
    const page = normalizeLessonsResponse(null)
    expect(page).toEqual({ items: [], node_id: '', status: '未审核建议经验' })

    const withItems = normalizeLessonsResponse({
      items: [
        { id: 'l1', text: '建议补充盖章页', source: 'finding', kind: 'revision' },
        { id: 'l2', text: '另一条建议' },
        'bad',
      ],
      node_id: 'n1',
    })
    expect(withItems.items.map((item) => item.id)).toEqual(['l1', 'l2'])
    expect(withItems.node_id).toBe('n1')
    expect(withItems.status).toBe('未审核建议经验')
    expect('verified' in withItems.items[0]).toBe(false)
  })

  it('GET /projects/{ref}/lessons 使用编码后的 ref 与只读请求', async () => {
    const fetchMock = vi.fn(
      async (_input: string, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            items: [{ id: 'l1', text: '建议', node_id: 'n1' }],
            node_id: 'n1',
            status: '未审核建议经验',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    )
    vi.stubGlobal('fetch', fetchMock)
    try {
      const page = await getProjectLessons('p 1', { node_id: 'n1', limit: 10 })
      expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%201/lessons?node_id=n1&limit=10')
      expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
      expect(page.items[0].text).toBe('建议')
      expect(page.status).toBe('未审核建议经验')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
