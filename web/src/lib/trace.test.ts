import { describe, expect, it } from 'vitest'

import { findValueIndex, markTraceHits } from './trace'
import type { FactCitation } from './types'

function citation(patch: Partial<FactCitation> = {}): FactCitation {
  return {
    node_id: 'node-1',
    heading: '技术方案',
    position: 0,
    fact_key: 'project.budget',
    label: '预算金额',
    value: '3500000',
    current_value: '3500000',
    occurrences: 1,
    stale: false,
    status: 'confirmed',
    source_quote: '招标文件原文：3500000',
    source_page: 2,
    checked_at: null,
    ...patch,
  }
}

function root(html: string): HTMLElement {
  const element = document.createElement('div')
  element.innerHTML = html
  return element
}

describe('findValueIndex', () => {
  it('matches text values anywhere', () => {
    expect(findValueIndex('某某科技有限公司', '某某科技')).toBe(0)
  })

  it('requires numbers to stand alone', () => {
    expect(findValueIndex('工期 90 日历天', '90')).toBe(3)
    expect(findValueIndex('共 190 个信息点', '90')).toBe(-1)
    expect(findValueIndex('单价 1.90 元', '90')).toBe(-1)
  })
})

describe('markTraceHits', () => {
  it('wraps cited values with their ledger field', () => {
    const element = root('<p>本项目预算 3500000 元。</p>')
    markTraceHits(element, [citation()])

    const marks = element.querySelectorAll('mark.trace-hit')
    expect(marks).toHaveLength(1)
    expect(marks[0].textContent).toBe('3500000')
    expect((marks[0] as HTMLElement).dataset.factKey).toBe('project.budget')
    expect(marks[0].getAttribute('title')).toContain('预算金额')
    expect(element.textContent).toBe('本项目预算 3500000 元。')
  })

  it('prefers the longer value when one contains another', () => {
    const element = root('<p>报价 3500000 元</p>')
    markTraceHits(element, [
      citation({ fact_key: 'short', label: '短值', value: '3500' }),
      citation({ fact_key: 'long', label: '长值', value: '3500000' }),
    ])

    const marks = Array.from(element.querySelectorAll('mark.trace-hit'))
    expect(marks).toHaveLength(1)
    expect(marks[0].textContent).toBe('3500000')
  })

  it('does not match a number inside a larger number', () => {
    const element = root('<p>共 190 个信息点</p>')
    markTraceHits(element, [citation({ value: '90' })])
    expect(element.querySelectorAll('mark.trace-hit')).toHaveLength(0)
  })

  it('marks stale citations and explains the change', () => {
    const element = root('<p>投标总报价 3500000 元。</p>')
    markTraceHits(element, [citation({ stale: true, current_value: '3600000' })])

    const mark = element.querySelector('mark.trace-hit')
    expect(mark?.classList.contains('is-stale')).toBe(true)
    expect(mark?.getAttribute('title')).toContain('3600000')
    expect(mark?.getAttribute('title')).toContain('需复核')
  })

  it('is idempotent when the preview re-renders', () => {
    const element = root('<p>预算 3500000 元，工期 90 天。</p>')
    const items = [citation(), citation({ fact_key: 'project.duration', label: '工期', value: '90' })]

    markTraceHits(element, items)
    element.innerHTML = '<p>预算 3500000 元，工期 90 天。</p>'
    markTraceHits(element, items)

    expect(element.querySelectorAll('mark.trace-hit')).toHaveLength(2)
    expect(element.querySelectorAll('mark.trace-hit mark')).toHaveLength(0)
  })

  it('skips code blocks', () => {
    const element = root('<pre><code>3500000</code></pre><p>3500000</p>')
    markTraceHits(element, [citation()])
    expect(element.querySelectorAll('pre mark')).toHaveLength(0)
    expect(element.querySelectorAll('p mark')).toHaveLength(1)
  })
})
