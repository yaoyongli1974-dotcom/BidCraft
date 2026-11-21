import { useCallback } from 'react'
import { RefreshCw, ScrollText } from 'lucide-react'
import { getAdminAudit } from '../../api/jobs'
import type { AuditEntry } from '../../lib/types'
import {
  actionLabel,
  auditActionTone,
  auditActor,
  auditDetailText,
  className,
  formatDate,
  formatNumber,
} from '../../lib/format'
import { useAuditFeed } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { AdminSectionLabel } from './AdminShared'

export function AdminAuditTab() {
  const fetcher = useCallback(
    (before: number | null) => getAdminAudit({ limit: 30, before }),
    [],
  )
  const feed = useAuditFeed('admin:audit', fetcher)

  return (
    <div className="admin-audit">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="操作审计"
          title="审计日志"
          description="全站写操作与成员变更的倒序记录。项目删除后，该项目的记录也会一并清理。"
          aside={
            <Button
              variant="ghost"
              size="sm"
              icon={<RefreshCw size={14} />}
              loading={feed.loading}
              onClick={feed.reload}
            >
              刷新
            </Button>
          }
        />
      </div>

      <div className="admin-list-toolbar">
        <span className="admin-list-caption">
          {feed.loading
            ? '正在读取审计日志…'
            : `已载入 ${formatNumber(feed.items.length)} 条记录`}
        </span>
        <span className="admin-list-caption">按时间倒序 · 每页 30 条</span>
      </div>

      {feed.error && !feed.items.length ? (
        <div className="admin-panel-inner">
          <ErrorState message={feed.error} onRetry={feed.reload} />
        </div>
      ) : feed.loading ? (
        <div className="admin-audit-table" aria-busy="true">
          <div className="admin-audit-head">
            <span>时间</span>
            <span>操作</span>
            <span>操作人</span>
            <span>项目</span>
            <span>详情</span>
          </div>
          {Array.from({ length: 5 }).map((_, index) => (
            <div key={index} className="admin-audit-row">
              <Skeleton width={92} height={10} />
              <Skeleton width={84} height={20} radius={999} />
              <Skeleton width="58%" height={11} />
              <Skeleton width="62%" height={11} />
              <Skeleton width="82%" height={11} />
            </div>
          ))}
        </div>
      ) : feed.items.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={ScrollText}
            title="还没有审计记录"
            description="创建项目、上传文件、保存正文或调整成员后，记录会出现在这里。"
          />
        </div>
      ) : (
        <div className="admin-audit-table" role="table" aria-label="审计日志">
          <div className="admin-audit-head" role="row">
            <span role="columnheader">时间</span>
            <span role="columnheader">操作</span>
            <span role="columnheader">操作人</span>
            <span role="columnheader">项目</span>
            <span role="columnheader">详情</span>
          </div>
          {feed.items.map((entry) => (
            <AuditRow key={entry.id} entry={entry} />
          ))}
        </div>
      )}

      <div className="admin-audit-foot">
        <span className="admin-list-caption">
          {feed.error
            ? feed.error
            : feed.next === null && feed.items.length > 0
              ? '已显示全部记录'
              : '按时间倒序滚动加载'}
        </span>
        {feed.next !== null && (
          <Button
            variant="secondary"
            size="sm"
            loading={feed.loadingMore}
            disabled={feed.loadingMore}
            onClick={feed.loadMore}
          >
            加载更多
          </Button>
        )}
      </div>

      <div className="admin-footnote">
        审计记录只写入本机数据库。超级管理员可以查看全部项目；项目成员可在项目「协作」页查看本项目记录。
      </div>
    </div>
  )
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const tone = auditActionTone(entry.action)
  const detail = auditDetailText(entry.detail)
  const projectName = entry.project_name || ''
  return (
    <div className="admin-audit-row" role="row">
      <div className="admin-audit-time mono" role="cell">
        {formatDate(entry.created_at)}
      </div>
      <div className="admin-audit-action" role="cell">
        <span className={className('audit-dot', `audit-dot--${tone}`)} aria-hidden />
        <Badge tone={tone} subtle>
          {actionLabel(entry.action)}
        </Badge>
      </div>
      <div className="admin-audit-person" role="cell">
        <strong>{auditActor(entry)}</strong>
        {entry.username && entry.username !== 'system' && <span>@{entry.username}</span>}
      </div>
      <div className="admin-audit-project" role="cell" title={projectName || undefined}>
        {projectName || <span className="subtle">—</span>}
      </div>
      <div className="admin-audit-detail" role="cell">
        {detail || <span className="subtle">—</span>}
      </div>
    </div>
  )
}
