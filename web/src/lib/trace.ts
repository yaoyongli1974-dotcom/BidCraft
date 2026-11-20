import type { FactCitation } from './types'
import { className } from './format'

const NUMERIC_VALUE = /^-?\d+(?:[.,]\d+)?$/

function isNumericValue(value: string): boolean {
  return NUMERIC_VALUE.test(value)
}

/** 与后端同一口径：数字必须独立出现，避免 150 里命中 50、1.5 里命中 5。 */
export function findValueIndex(text: string, value: string, from = 0): number {
  const numeric = isNumericValue(value)
  let start = from
  while (start <= text.length) {
    const index = text.indexOf(value, start)
    if (index < 0) return -1
    start = index + value.length
    if (!numeric) return index
    const before = index > 0 ? text[index - 1] : ''
    const after = start < text.length ? text[start] : ''
    if (before && (/[0-9]/.test(before) || before === '.' || before === ',')) continue
    if (after && (/[0-9]/.test(after) || after === '.' || after === ',')) continue
    return index
  }
  return -1
}

/**
 * 把预览 DOM 里的台账取值包成 ``mark.trace-hit``，供悬停/点击查看来源。
 * 只动文本节点，跳过代码块与已经包过的片段；先清理旧标记再重包。
 */
export function markTraceHits(root: HTMLElement, citations: FactCitation[]) {
  root.querySelectorAll('mark.trace-hit').forEach((mark) => {
    const text = document.createTextNode(mark.textContent || '')
    mark.replaceWith(text)
  })
  root.normalize()
  if (!citations.length) return

  // 长值优先，避免「3500000」被「3500」抢先切碎。
  const ordered = [...citations]
    .filter((item) => item.value)
    .sort((left, right) => right.value.length - left.value.length)

  const wrap = (node: Text, pool: FactCitation[]) => {
    if (!node.nodeValue || !pool.length) return
    const text = node.nodeValue
    let best: { index: number; item: FactCitation } | null = null
    for (const item of pool) {
      const index = findValueIndex(text, item.value)
      if (index < 0) continue
      if (!best || index < best.index) best = { index, item }
    }
    if (!best) return
    const { index, item } = best
    const parent = node.parentNode
    if (!parent) return
    const before = text.slice(0, index)
    const after = text.slice(index + item.value.length)
    const mark = document.createElement('mark')
    mark.className = className('trace-hit', item.stale && 'is-stale')
    mark.dataset.factKey = item.fact_key
    mark.textContent = item.value
    mark.title = item.stale
      ? `${item.label}：正文写着 ${item.value}，台账已改为 ${item.current_value}（需复核）`
      : `${item.label}：${item.value}（来自事实台账）`
    if (before) parent.insertBefore(document.createTextNode(before), node)
    parent.insertBefore(mark, node)
    if (after) {
      node.nodeValue = after
      wrap(node, pool.filter((entry) => entry.value !== item.value))
    } else {
      parent.removeChild(node)
    }
  }

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = (node as Text).parentElement
      if (!parent) return NodeFilter.FILTER_REJECT
      if (parent.closest('mark.trace-hit, code, pre, script, style')) {
        return NodeFilter.FILTER_REJECT
      }
      return (node.nodeValue || '').trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT
    },
  })
  const nodes: Text[] = []
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  nodes.forEach((node) => wrap(node, ordered))
}
