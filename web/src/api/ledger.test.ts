import { describe, expect, it } from 'vitest'
import {
  mergeFactUpdate,
  normalizeFact,
  normalizeFactEvidence,
  normalizeFactUpdate,
} from './ledger'

describe('ledger 事实证据归一化', () => {
  it('确认与来源字段缺失 / null 时保持 null，不编造文件名或页码', () => {
    const fact = normalizeFact({
      key: 'f1',
      label: '工期',
      value: '180 天',
      status: 'manual',
      required: true,
      confirmed_by: null,
      confirmed_at: undefined,
      source_file_id: null,
      source_file_name: null,
      source_quote: undefined,
      source_page: null,
    })
    expect(fact).not.toBeNull()
    expect(fact!.source_file_name).toBeNull()
    expect(fact!.confirmed_by).toBeNull()
    expect(fact!.confirmed_at).toBeNull()
    expect(fact!.source_file_id).toBeNull()
    expect(fact!.source_quote).toBeNull()
    expect(fact!.source_page).toBeNull()
    expect(fact!.source).toBeUndefined()
  })

  it('新契约 source_* 优先，旧契约嵌套 source 作为兼容回退', () => {
    const legacy = normalizeFactEvidence({
      source: { file: '招标文件.pdf', page: 12, quote: '旧原文' },
    })
    expect(legacy).toEqual({
      source_file_id: null,
      source_quote: '旧原文',
      source_page: 12,
    })

    const fact = normalizeFact({
      key: 'f2',
      confirmed_by: 'alice',
      confirmed_at: '2026-11-25T10:00:00Z',
      source_file_id: 'src-1',
      source_quote: '新原文',
      source_page: 0,
      source: { file: '招标文件.pdf', page: 99, quote: '旧原文' },
    })
    expect(fact!.source_file_id).toBe('src-1')
    expect(fact!.source_quote).toBe('新原文')
    expect(fact!.source_page).toBe(0)
    expect(fact!.source?.file).toBe('招标文件.pdf')
    expect(fact!.confirmed_by).toBe('alice')
  })

  it('脏值不会变成事实：非对象 / 无 key 的条目被丢弃', () => {
    expect(normalizeFact(null)).toBeNull()
    expect(normalizeFact('bad')).toBeNull()
    expect(normalizeFact({ label: '无 key' })).toBeNull()
  })

  it('PATCH 响应归一化保留服务端盖章与真实陈旧引用', () => {
    const update = normalizeFactUpdate({
      key: 'f1',
      value: '新值',
      status: 'manual',
      confirmed_by: 'alice',
      confirmed_at: '2026-11-25T10:00:00Z',
      source_quote: '原文',
      stale_sections: [{ node_id: 'n1', heading: '第一章', stale_value: '旧值' }, null],
    })
    expect(update!.confirmed_by).toBe('alice')
    expect(update!.confirmed_at).toBe('2026-11-25T10:00:00Z')
    expect(update!.source_quote).toBe('原文')
    expect(update!.stale_sections).toEqual([
      { node_id: 'n1', heading: '第一章', stale_value: '旧值' },
    ])
    expect(normalizeFactUpdate({ value: 'x' })).toBeNull()
  })

  it('合并回本地台账时旧后端未返回的字段不被清空', () => {
    const original = normalizeFact({
      key: 'f1',
      value: '旧值',
      status: 'extracted',
      confirmed_by: 'bob',
      source_file_id: 'src-9',
      source_page: 4,
    })!
    const merged = mergeFactUpdate(original, {
      key: 'f1',
      value: '新值',
      status: 'manual',
      confirmed_by: undefined,
      confirmed_at: undefined,
      source_file_id: undefined,
      source_quote: undefined,
      source_page: undefined,
    })
    expect(merged.value).toBe('新值')
    expect(merged.status).toBe('manual')
    expect(merged.confirmed_by).toBe('bob')
    expect(merged.source_file_id).toBe('src-9')
    expect(merged.source_page).toBe(4)

    const explicitNull = mergeFactUpdate(original, {
      key: 'f1',
      value: '新值',
      status: 'manual',
      confirmed_by: null,
      source_file_id: null,
    })
    expect(explicitNull.confirmed_by).toBeNull()
    expect(explicitNull.source_file_id).toBeNull()
  })
})
