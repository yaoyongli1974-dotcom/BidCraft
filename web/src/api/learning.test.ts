import { describe, expect, it, vi } from 'vitest'
import {
  getProjectLessons,
  lessonQuery,
  normalizeLesson,
  normalizeLessonPolicy,
  normalizeLessonsResponse,
  revokeProjectLesson,
} from './learning'

describe('建议经验客户端', () => {
  it('查询参数只包含 node_id / limit / include_revoked，空值不拼接', () => {
    expect(lessonQuery()).toBe('')
    expect(lessonQuery({ node_id: '  ', limit: 0 })).toBe('')
    expect(lessonQuery({ node_id: ' n1 ', limit: 3.6 })).toBe('?node_id=n1&limit=4')
    expect(lessonQuery({ include_revoked: false })).toBe('')
    expect(lessonQuery({ include_revoked: true })).toBe('?include_revoked=true')
  })

  it('单条建议缺治理字段时补空串 / null / false，无效条目被丢弃', () => {
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
      grade: '',
      trust: '',
      expires_at: null,
      revoked: false,
    })
  })

  it('响应归一：默认状态与空策略，不推断已验证', () => {
    const page = normalizeLessonsResponse(null)
    expect(page).toEqual({
      items: [],
      node_id: '',
      status: '未审核建议经验',
      policy: { grades: {}, trust_levels: {}, min_trust: null, ttl_days: 0, revoked_ids: [] },
    })

    const withItems = normalizeLessonsResponse({
      items: [
        {
          id: 'l1',
          text: '建议补充盖章页',
          source: 'finding',
          kind: 'revision',
          grade: 'A',
          trust: 'high',
          expires_at: '2026-12-01T00:00:00+08:00',
          revoked: true,
        },
        { id: 'l2', text: '另一条建议' },
        'bad',
      ],
      node_id: 'n1',
      policy: { grades: { l1: 'A' }, revoked_ids: ['l1', '', 3], ttl_days: 30 },
    })
    expect(withItems.items.map((item) => item.id)).toEqual(['l1', 'l2'])
    expect(withItems.items[0].grade).toBe('A')
    expect(withItems.items[0].trust).toBe('high')
    expect(withItems.items[0].revoked).toBe(true)
    expect(withItems.items[0].expires_at).toBe('2026-12-01T00:00:00+08:00')
    expect(withItems.policy.revoked_ids).toEqual(['l1'])
    expect(withItems.policy.grades).toEqual({ l1: 'A' })
    expect(withItems.status).toBe('未审核建议经验')
    expect('verified' in withItems.items[0]).toBe(false)
  })

  it('策略归一裁剪非法值与过期天数', () => {
    expect(
      normalizeLessonPolicy({
        grades: { l1: 'A', l2: 7 },
        trust_levels: { l1: 'HIGH', l2: null },
        min_trust: '',
        ttl_days: -3,
        revoked_ids: ['l1', 'l1', '', 7],
      }),
    ).toEqual({
      grades: { l1: 'A' },
      trust_levels: { l1: 'HIGH' },
      min_trust: null,
      ttl_days: 0,
      revoked_ids: ['l1'],
    })
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
      expect(page.policy.revoked_ids).toEqual([])
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('PATCH 撤销走编码后的 ref/id，并回显归一化策略', async () => {
    const fetchMock = vi.fn(
      async (_input: string, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            lesson_id: 'l 1',
            revoked: true,
            policy: { revoked_ids: ['l 1', 'l 1'], ttl_days: 7 },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    )
    vi.stubGlobal('fetch', fetchMock)
    try {
      const result = await revokeProjectLesson('p 1', 'l 1')
      expect(fetchMock.mock.calls[0][0]).toBe('/projects/p%201/lessons/l%201')
      expect(fetchMock.mock.calls[0][1]?.method).toBe('PATCH')
      expect(result.revoked).toBe(true)
      expect(result.policy.revoked_ids).toEqual(['l 1'])
      expect(result.policy.ttl_days).toBe(7)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
