import type { ReactNode } from 'react'
import './markdown.css'

const INLINE_PATTERN =
  /(\{\{\s*(?:待填|图片占位)[:：][^}]*\}\}|`[^`]+`|!\[[^\]]*\]\([^)]*\)|\[[^\]]+\]\([^)\s]+\)|\*\*[^*]+\*\*|~~[^~]+~~|\*[^*]+\*)/g

function safeHref(value: string): string | null {
  const href = value.trim()
  if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) return href
  return null
}

function safeImageSrc(value: string): string | null {
  const src = value.trim()
  // Generated image URLs are same-origin relative paths (e.g. /files/...).
  // Never load remote images from tender text.
  if (src.startsWith('/') && !src.startsWith('//') && !src.startsWith('/\\')) return src
  return null
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  if (!text) return []
  const nodes: ReactNode[] = []
  let lastIndex = 0
  let index = 0
  // Each recursive pass needs its own regex instance: a shared stateful /g
  // regex is corrupted when nested inline formatting calls reset lastIndex.
  const pattern = new RegExp(INLINE_PATTERN.source, 'g')
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text))) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const token = match[0]
    const key = `${keyPrefix}-${index++}`
    if (token.startsWith('{{')) {
      nodes.push(
        <mark key={key} className="md-placeholder" title="需要人工填写">
          {token}
        </mark>,
      )
    } else if (token.startsWith('`')) {
      nodes.push(
        <code key={key} className="md-inline-code">
          {token.slice(1, -1)}
        </code>,
      )
    } else if (token.startsWith('![')) {
      const image = /^!\[([^\]]*)\]\(([^)]*)\)$/.exec(token)
      const alt = image?.[1] || '图片'
      const src = image ? safeImageSrc(image[2]) : null
      if (src) {
        nodes.push(
          <img
            key={key}
            className="md-image-real"
            src={src}
            alt={alt || '配图'}
            loading="lazy"
          />,
        )
      } else {
        nodes.push(
          <span key={key} className="md-image">
            图片：{alt || '未命名'}
          </span>,
        )
      }
    } else if (token.startsWith('[')) {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token)
      const label = link?.[1] || token
      const href = link ? safeHref(link[2]) : null
      if (href) {
        nodes.push(
          <a key={key} href={href} target="_blank" rel="noreferrer noopener">
            {label}
          </a>,
        )
      } else {
        nodes.push(<span key={key}>{label}</span>)
      }
    } else if (token.startsWith('**')) {
      nodes.push(
        <strong key={key}>{renderInline(token.slice(2, -2), `${key}-b`)}</strong>,
      )
    } else if (token.startsWith('~~')) {
      nodes.push(<del key={key}>{token.slice(2, -2)}</del>)
    } else if (token.startsWith('*')) {
      nodes.push(<em key={key}>{renderInline(token.slice(1, -1), `${key}-i`)}</em>)
    } else {
      nodes.push(token)
    }
    lastIndex = match.index + token.length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isTableSeparator(line: string): boolean {
  const cells = splitTableRow(line)
  return cells.length > 0 && cells.every((cell) => /^:?-{2,}:?$/.test(cell))
}

function isBlockStart(line: string, next?: string): boolean {
  if (!line.trim()) return true
  if (/^\s*```/.test(line)) return true
  if (/^#{1,6}\s+/.test(line)) return true
  if (/^\s*>\s?/.test(line)) return true
  if (/^\s*([-*_])\1{2,}\s*$/.test(line)) return true
  if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) return true
  if (line.includes('|') && next && isTableSeparator(next)) return true
  return false
}

interface MarkdownProps {
  text: string
  className?: string
}

/** A deliberately small, dependency-free Markdown renderer. Raw HTML is never
 * injected: all text is rendered through React, so tender content cannot inject
 * scripts into the workspace. */
export function Markdown({ text, className }: MarkdownProps) {
  const lines = (text || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let index = 0
  let key = 0

  while (index < lines.length) {
    const line = lines[index]

    if (!line.trim()) {
      index += 1
      continue
    }

    const fence = /^\s*```\s*([A-Za-z0-9_+-]*)\s*$/.exec(line)
    if (fence) {
      const language = fence[1]
      index += 1
      const code: string[] = []
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        code.push(lines[index])
        index += 1
      }
      index += 1
      blocks.push(
        <pre key={`code-${key++}`} className="md-code">
          {language && <span className="md-code-lang">{language}</span>}
          <code>{code.join('\n')}</code>
        </pre>,
      )
      continue
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      const level = Math.min(heading[1].length, 6)
      const content = renderInline(heading[2], `h-${key}`)
      const headingKey = `heading-${key++}`
      if (level === 1) blocks.push(<h1 key={headingKey}>{content}</h1>)
      else if (level === 2) blocks.push(<h2 key={headingKey}>{content}</h2>)
      else if (level === 3) blocks.push(<h3 key={headingKey}>{content}</h3>)
      else if (level === 4) blocks.push(<h4 key={headingKey}>{content}</h4>)
      else if (level === 5) blocks.push(<h5 key={headingKey}>{content}</h5>)
      else blocks.push(<h6 key={headingKey}>{content}</h6>)
      index += 1
      continue
    }

    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      blocks.push(<hr key={`hr-${key++}`} />)
      index += 1
      continue
    }

    if (
      line.includes('|') &&
      index + 1 < lines.length &&
      isTableSeparator(lines[index + 1])
    ) {
      const header = splitTableRow(line)
      index += 2
      const rows: string[][] = []
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]))
        index += 1
      }
      blocks.push(
        <div key={`table-${key++}`} className="md-table-wrap">
          <table>
            <thead>
              <tr>
                {header.map((cell, cellIndex) => (
                  <th key={cellIndex}>
                    {renderInline(cell, `th-${key}-${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {header.map((_, cellIndex) => (
                    <td key={cellIndex}>
                      {renderInline(row[cellIndex] || '', `td-${key}-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ''))
        index += 1
      }
      blocks.push(
        <blockquote key={`quote-${key++}`}>
          {renderInline(quote.join('\n'), `quote-${key}`)}
        </blockquote>,
      )
      continue
    }

    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line)
      const items: string[] = []
      while (index < lines.length) {
        const itemMatch = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[index])
        if (!itemMatch || /^\s*\d+[.)]\s+/.test(lines[index]) !== ordered) break
        items.push(itemMatch[2])
        index += 1
      }
      const listKey = `list-${key++}`
      const content = items.map((item, itemIndex) => (
        <li key={itemIndex}>{renderInline(item, `${listKey}-${itemIndex}`)}</li>
      ))
      blocks.push(
        ordered ? (
          <ol key={listKey}>{content}</ol>
        ) : (
          <ul key={listKey}>{content}</ul>
        ),
      )
      continue
    }

    const paragraph: string[] = [line]
    index += 1
    while (
      index < lines.length &&
      !isBlockStart(lines[index], lines[index + 1])
    ) {
      paragraph.push(lines[index])
      index += 1
    }
    blocks.push(
      <p key={`p-${key++}`}>{renderInline(paragraph.join('\n'), `p-${key}`)}</p>,
    )
  }

  return <div className={className ? `markdown ${className}` : 'markdown'}>{blocks}</div>
}

export function MarkdownPreview({ text }: { text: string }) {
  if (!text.trim()) {
    return (
      <div className="md-empty">
        <p>正文还是空的。</p>
        <p>可以从左侧选择章节后生成，或直接开始书写。</p>
      </div>
    )
  }
  return <Markdown text={text} />
}
