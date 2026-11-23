import { Activity, CheckCircle2, Database, Server, ShieldCheck, XCircle } from 'lucide-react'
import { getAdminHealth } from '../../api/admin'
import { useQuery } from '../../lib/hooks'
import { formatDate, formatNumber } from '../../lib/format'
import { Badge } from '../../components/Badge'
import { ErrorState, Skeleton } from '../../components/Feedback'
import { AdminSectionLabel } from './AdminShared'

/** 运营指标口径；后端暂无全局聚合时展示定义而不编造数字。 */
const OPS_METRICS = [
  {
    key: 'manual_edit_rate',
    label: '人工修改率',
    definition: '章节 revision > 1 的比例，及修改字数 / 生成字数（按项目统计）。',
  },
  {
    key: 'issue_density',
    label: '核对问题密度',
    definition: '核对产出的问题数 ÷ 正文字数（按项目统计）。',
  },
  {
    key: 'boq_match_rate',
    label: '清单匹配率',
    definition: '清单行匹配到台账材料的比例，含缺项/多项（按项目统计）。',
  },
] as const

function bytes(value: number): string {
  if (value >= 1024 * 1024 * 1024) return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

function Flag({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="admin-billing-metric">
      <span className="admin-billing-metric-icon">
        {ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
      </span>
      <div>
        <span>{label}</span>
        <strong>{ok ? '已启用' : '未启用'}</strong>
      </div>
    </div>
  )
}

/** 运维状态：`/admin/health` 的完整口径 + README 中声明的三项质量指标定义。 */
export function AdminHealthTab() {
  const query = useQuery('admin:health', () => getAdminHealth())
  const data = query.data
  const metrics = data?.metrics || {}

  if (query.loading && !data) {
    return (
      <div className="admin-billing">
        <Skeleton height={84} />
        <div className="admin-billing-metrics">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} height={84} radius={8} />
          ))}
        </div>
      </div>
    )
  }
  if (query.error && !data) {
    return (
      <div className="admin-panel-inner">
        <ErrorState message={query.error} onRetry={query.refresh} />
      </div>
    )
  }
  if (!data) return null

  return (
    <div className="admin-billing">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="运行状态"
          title="服务健康与可观测指标"
          description="读取完整健康检查（公开 /health 有意保持最小化），并说明可按项目统计的质量指标口径。"
          aside={
            <button type="button" className="text-btn" onClick={query.refresh} disabled={query.refreshing}>
              <Activity size={13} className={query.refreshing ? 'spin' : undefined} />
              刷新
            </button>
          }
        />
      </div>

      <div className="admin-billing-metrics">
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon"><Server size={16} /></span>
          <div>
            <span>服务版本</span>
            <strong className="mono">{data.version}</strong>
            <small>运行 {formatNumber(data.uptime_seconds)} 秒</small>
          </div>
        </div>
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon"><Database size={16} /></span>
          <div>
            <span>数据目录</span>
            <strong>{bytes(data.db_size_bytes)}</strong>
            <small>剩余磁盘 {bytes(data.disk_free_bytes)}</small>
          </div>
        </div>
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon"><ShieldCheck size={16} /></span>
          <div>
            <span>账号 / 知识库</span>
            <strong>{formatNumber(data.users)} / {formatNumber(data.knowledge_bases)}</strong>
            <small>{formatNumber(data.models)} 个模型配置</small>
          </div>
        </div>
        <div className="admin-billing-metric">
          <span className="admin-billing-metric-icon"><Activity size={16} /></span>
          <div>
            <span>启动时间</span>
            <strong>{formatDate(data.started_at, false)}</strong>
            <small>{data.python} · {data.platform}</small>
          </div>
        </div>
      </div>

      <div className="admin-billing-grid">
        <section className="admin-billing-card">
          <div className="admin-billing-card-head">
            <div>
              <div className="eyebrow">关键开关</div>
              <h3>配置状态</h3>
            </div>
          </div>
          <div className="admin-billing-metrics">
            <Flag ok={data.model_configured} label="对话模型" />
            <Flag ok={data.embedding_configured} label="向量模型" />
            <Flag ok={data.web_ready} label="前端构建产物" />
            <Flag ok={!data.offline} label="在线模式" />
            <Flag ok={data.wechat_login} label="微信登录" />
            <Flag ok={data.wechat_pay} label="微信支付" />
            <Flag ok={data.docs_enabled} label="文档页" />
            <Flag ok={data.secure_cookie} label="安全 Cookie" />
          </div>
          <div className="admin-footnote">
            模型：{data.model_label || '—'} · 向量模型：{data.embedding_model || '—'} · 数据目录：{data.home}
            <br />
            状态：{data.status} · 认证：{data.auth_required ? '开启' : '关闭'} · 注册：{data.registration ? '开启' : '关闭'} · 公开健康检查：{data.health_public ? '开启' : '关闭'}
          </div>
        </section>

        <section className="admin-billing-card">
          <div className="admin-billing-card-head">
            <div>
              <div className="eyebrow">质量运营指标</div>
              <h3>可观测口径</h3>
            </div>
            <Badge tone="neutral" subtle>按项目统计</Badge>
          </div>
          <table className="admin-data-table">
            <thead>
              <tr>
                <th>指标</th>
                <th>口径</th>
                <th>当前值</th>
              </tr>
            </thead>
            <tbody>
              {OPS_METRICS.map((item) => (
                <tr key={item.key}>
                  <td>{item.label}</td>
                  <td>{item.definition}</td>
                  <td className="mono">
                    {metrics[item.key] !== undefined && metrics[item.key] !== ''
                      ? String(metrics[item.key])
                      : '暂无全局聚合'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="admin-footnote">
            三项指标由章节修订、核对问题与清单行的项目数据计算；后端未暴露全局聚合前，
            此处只展示定义，不引用未经测量的数字。
          </div>
        </section>
      </div>
    </div>
  )
}
