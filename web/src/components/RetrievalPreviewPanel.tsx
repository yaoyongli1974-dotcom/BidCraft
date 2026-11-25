import { useState, type FormEvent } from 'react'
import { Search } from 'lucide-react'
import { previewProjectRetrieval, type RetrievalPreview } from '../api/retrieval'
import { messageOf } from '../lib/http'
import { Badge } from './Badge'
import { Button } from './Button'
import { EmptyState, ErrorState } from './Feedback'
import { RetrievalTraceBadges, RetrievalTraceMeta } from './RetrievalTraceBadges'

export interface RetrievalPreviewPanelProps {
  projectRef: string
  /** 只读视图标记：检索本身是读操作，因此查询输入仍可用。 */
  readOnly?: boolean
}

/**
 * 事实台账页内的紧凑检索预览。
 * 治理层端点是同步 GET，因此直接发请求；不启动 watchJob、不绕过 API 层，
 * 也不提供任何写操作（viewer / editor 都只读）。
 */
export function RetrievalPreviewPanel({ projectRef, readOnly = false }: RetrievalPreviewPanelProps) {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<RetrievalPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const search = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    const q = query.trim()
    if (!q || busy) return
    setBusy(true)
    setError(null)
    try {
      setResult(await previewProjectRetrieval(projectRef, q))
    } catch (failure) {
      setResult(null)
      setError(messageOf(failure))
    } finally {
      setBusy(false)
    }
  }

  const diagnostics = result?.diagnostics || null
  const diagnosticEntries =
    diagnostics && !Array.isArray(diagnostics)
      ? Object.entries(diagnostics)
          .filter(([, value]) => value !== null && value !== undefined)
          .slice(0, 4)
      : []
  const diagnosticCount = Array.isArray(diagnostics) ? diagnostics.length : 0

  return (
    <section className="panel retrieval-preview" aria-label="检索预览">
      <div className="panel-head">
        <div>
          <div className="eyebrow">检索链路</div>
          <h3>检索预览</h3>
        </div>
        <Badge tone={readOnly ? 'neutral' : 'info'} subtle>
          {readOnly ? '只读' : '只读 · 查询不写入项目'}
        </Badge>
      </div>
      <div className="panel-body">
        <form className="retrieval-preview-form" onSubmit={(event) => void search(event)}>
          <label className="search-field search-field--grow">
            <Search size={15} aria-hidden />
            <span className="sr-only">检索预览查询</span>
            <input
              aria-label="检索预览查询"
              placeholder="输入检索词，查看命中片段的打分与溯源"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <Button
            size="sm"
            variant="secondary"
            loading={busy}
            disabled={busy || !query.trim()}
            title={!query.trim() ? '请先输入检索内容' : undefined}
            onClick={() => void search()}
          >
            检索
          </Button>
        </form>

        {error && <ErrorState compact message={error} />}

        {!result && !error && (
          <p className="subtle">输入检索词后可查看片段的原始分、重排分、信任档位与块溯源。</p>
        )}

        {result && (
          <>
            <div className="retrieval-preview-summary">
              <span className="subtle">命中 {result.items.length} 条</span>
              {result.degraded === true && <Badge tone="warn" subtle>降级运行</Badge>}
              {result.degraded === null && <Badge tone="neutral" subtle>降级未知</Badge>}
              {diagnosticCount > 0 && (
                <span className="subtle">诊断 {diagnosticCount} 条</span>
              )}
              {diagnosticEntries.map(([key, value]) => (
                <span className="mono" key={key}>
                  {key} {Array.isArray(value) ? value.length : String(value)}
                </span>
              ))}
            </div>
            {result.items.length === 0 ? (
              <EmptyState
                compact
                icon={Search}
                title="没有命中片段"
                description="可换一个检索词重试；服务端未返回片段时不会补造示例内容。"
              />
            ) : (
              <ul className="retrieval-preview-list">
                {result.items.map((passage, index) => (
                  <li key={passage.chunk_id || passage.document_id || passage.source || index}>
                    <p className="retrieval-preview-text">{passage.text || '（服务端未返回片段文本）'}</p>
                    <RetrievalTraceBadges {...passage} score={passage.score} compact />
                    <RetrievalTraceMeta
                      chunk_id={passage.chunk_id}
                      chunk_index={passage.chunk_index}
                      document_id={passage.document_id}
                      page={passage.page}
                      heading={passage.heading}
                      source={passage.source}
                      kb_id={passage.kb_id}
                      compact
                    />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  )
}
