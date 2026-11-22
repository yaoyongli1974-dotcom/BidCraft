import { FileStack, ShieldCheck } from 'lucide-react'
import type { EnterpriseDocument } from '../lib/types'
import { formatDate, truncate } from '../lib/format'
import { Badge } from './Badge'

/** 企业共享资料列表；空数据时给出明确占位，不伪造条目。 */
export function EnterpriseAssetList({
  documents,
  emptyText = '还没有入库的企业资料。',
}: {
  documents: EnterpriseDocument[]
  emptyText?: string
}) {
  if (documents.length === 0) {
    return (
      <div className="ent-asset-empty" role="status">
        <FileStack size={20} aria-hidden />
        <p>{emptyText}</p>
      </div>
    )
  }
  return (
    <ul className="ent-asset-list" aria-label="企业共享资料">
      {documents.map((doc) => (
        <li key={doc.id || `${doc.source}-${doc.created_at || ''}`} className="ent-asset">
          <div className="ent-asset-head">
            <strong>{doc.source || '未命名资料'}</strong>
            <Badge tone="info" subtle>
              {doc.kind || 'other'}
            </Badge>
            {doc.valid_to && (
              <span className="ent-asset-validity mono">
                <ShieldCheck size={12} /> 有效至 {formatDate(doc.valid_to, false)}
              </span>
            )}
          </div>
          {doc.content && <p>{truncate(doc.content, 160)}</p>}
          {doc.tags.length > 0 && (
            <div className="ent-asset-tags">
              {doc.tags.map((tag) => (
                <span key={tag} className="ent-asset-tag">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
