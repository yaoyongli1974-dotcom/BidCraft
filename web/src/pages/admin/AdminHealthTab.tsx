import { Activity, CheckCircle2, Database, Server, ShieldCheck, XCircle } from 'lucide-react'
import { getAdminHealth } from '../../api/admin'
import { useQuery } from '../../lib/hooks'
import { formatDate, formatNumber, ISSUE_STATUS, JOB_STATUS, PROJECT_STATUS, projectStatus } from '../../lib/format'
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

/** 后端全局聚合的常见键 → 中文标签；未知键按片段翻译，保留原键便于排查。 */
const GLOBAL_METRIC_LABELS: Record<string, string> = {
  projects: '项目总数',
  jobs: '任务总数',
  total_projects: '项目总数',
  active_projects: '活跃项目数',
  users: '用户总数',
  total_users: '用户总数',
  projects_by_status: '项目状态分布',
  jobs_by_status: '任务状态分布',
  active_users: '活跃用户数',
  nodes: '大纲节点数',
  chapters: '章节总数',
  outline_nodes: '大纲节点数',
  sources: '源文件数',
  findings: '质量检查项',
  open_findings: '未处置质量问题',
  issues: '核对问题数',
  open_issues: '未处置问题数',
  reports: '报告总数',
  evaluations: '质量评估次数',
  manual_edit_rate: '人工修改率',
  issue_density: '核对问题密度',
  boq_match_rate: '清单匹配率',
  avg_score: '平均质量分',
  average_score: '平均质量分',
  boq_rows: '清单行数',
  quotes: '报价版本数',
  documents: '商务文档数',
  templates: '模板数量',
  // 后端 workers_running() 只反映「本进程是否启动内置 worker 池」。
  // API-only / 多实例外部 worker 部署下 false 属正常，不能标成生产故障。
  workers_running: '内置工作进程已启动',
  queued_jobs: '排队任务数',
  oldest_queued_age_seconds: '最老排队任务年龄（秒）',
  stale_running_jobs: '陈旧运行中任务数',
}

const GLOBAL_METRIC_TOKENS: Record<string, string> = {
  total: '总',
  active: '活跃',
  count: '数量',
  project: '项目',
  job: '任务',
  user: '用户',
  chapter: '章节',
  node: '节点',
  issue: '问题',
  finding: '检查项',
  report: '报告',
  evaluation: '评估',
  score: '得分',
  rate: '比率',
  average: '平均',
  avg: '平均',
  quote: '报价',
  material: '材料',
  document: '文档',
  template: '模板',
  open: '未处置',
}

function globalMetricLabel(key: string): string {
  if (GLOBAL_METRIC_LABELS[key]) return GLOBAL_METRIC_LABELS[key]
  const parts = key
    .toLowerCase()
    .split(/[_\s-]+/)
    .map((part) => GLOBAL_METRIC_TOKENS[part] || part)
  return parts.join('') || key
}

/** 质量 finding 的真实处置状态（OPEN_FINDING_STATUSES 及其处置后状态）。 */
const FINDING_STATUS_LABELS: Record<string, string> = {
  open: '未处置',
  new: '未处置',
  reopened: '重新打开',
  handled: '已处置',
  ignored: '已忽略',
}

/** 按所属聚合键选用规范状态标签，避免项目态/任务态同名值互相串译。 */
function metricStatusLabel(metricKey: string, key: string): string {
  const normalized = key.toLowerCase()
  if (metricKey === 'projects_by_status') {
    return PROJECT_STATUS[normalized] ? projectStatus(normalized).label : key
  }
  if (metricKey === 'jobs_by_status') {
    return JOB_STATUS[normalized] ? JOB_STATUS[normalized].label : key
  }
  if (FINDING_STATUS_LABELS[normalized]) return FINDING_STATUS_LABELS[normalized]
  if (ISSUE_STATUS[normalized]) return ISSUE_STATUS[normalized].label
  return key
}

function metricValue(value: unknown, metricKey = ''): string {
  if (typeof value === 'number' && Number.isFinite(value)) return formatNumber(value)
  if (typeof value === 'string') return value
  if (typeof value === 'boolean') {
    if (metricKey === 'workers_running') return value ? '已启动' : '未启动'
    return value ? '是' : '否'
  }
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return '—'
    return entries
      .map(([key, count]) => `${metricStatusLabel(metricKey, key)} ${metricValue(count)}`)
      .join(' · ')
  }
  return JSON.stringify(value)
}

interface MetricNotice {
  text: string
  tone: 'info' | 'warn'
}

/**
 * 当前 /admin/health 的 10 键契约中需要额外说明的两项：
 * - workers_running=false 仅表示本进程没有内置 worker 池（API-only 部署为正常形态），
 *   因此用 info 说明而不是 warn 告警；真正的队列风险看 queued_jobs/stale_running_jobs。
 * - stale_running_jobs>0 才是需要管理员介入的告警。
 */
function globalMetricNotice(key: string, value: unknown): MetricNotice | null {
  if (key === 'workers_running' && value === false) {
    return { text: '未启动内置工作进程（可为 API-only 部署）', tone: 'info' }
  }
  if (key === 'stale_running_jobs' && typeof value === 'number' && value > 0) {
    return { text: '存在陈旧运行中任务', tone: 'warn' }
  }
  return null
}

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
  const globalMetrics = data?.global_metrics

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
            三项指标由章节修订、核对问题与清单行的项目数据计算，目前按单项目口径；
            后端提供 metrics 值时才显示，否则仅展示定义。下方「全局指标」是后端实时聚合的全库计数，不含这三个比率。
          </div>
        </section>
      </div>

      {globalMetrics && Object.keys(globalMetrics).length > 0 && (
        <section className="admin-billing-card admin-global-metrics">
          <div className="admin-billing-card-head">
            <div>
              <div className="eyebrow">全库统计</div>
              <h3>全局指标（数据库聚合）</h3>
            </div>
            <Badge tone="info" subtle>后端聚合</Badge>
          </div>
          <table className="admin-data-table">
            <thead>
              <tr>
                <th>指标</th>
                <th>数值</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(globalMetrics).map(([key, value]) => {
                const notice = globalMetricNotice(key, value)
                return (
                  <tr key={key}>
                    <td>{globalMetricLabel(key)}</td>
                    <td className="mono">
                      {metricValue(value, key)}
                      {notice && (
                        <>
                          {' '}
                          <Badge tone={notice.tone} subtle>{notice.text}</Badge>
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="admin-footnote">
            由后端对数据库做全库聚合，与上方单进程快照口径不同；老后端未返回该字段时不显示本区块。
            内置工作进程未启动仅说明本进程未启动 worker 池（API-only 部署可为正常）；
            只有陈旧运行中任务 &gt; 0 才显示告警标记。
          </div>
        </section>
      )}
    </div>
  )
}
