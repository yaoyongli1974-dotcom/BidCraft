import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft,
  BookOpen,
  Cpu,
  Layers,
  LibraryBig,
  Plug,
  ScrollText,
  ShieldAlert,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { useHealth } from '../../context/HealthContext'
import { useDocumentTitle, useQuery } from '../../lib/hooks'
import {
  getKnowledgeBases,
  getModels,
  getQuotas,
  getRulePackages,
  getUsers,
} from '../../lib/api'
import { formatNumber, roleMeta } from '../../lib/format'
import { Badge } from '../../components/Badge'
import { AdminModelsTab } from './AdminModelsTab'
import { AdminKnowledgeTab } from './AdminKnowledgeTab'
import { AdminQuotaTab } from './AdminQuotaTab'
import { AdminRulePackagesTab } from './AdminRulePackagesTab'
import { AdminUsersTab } from './AdminUsersTab'
import { AdminBillingTab } from './AdminBillingTab'
import { AdminAuditTab } from './AdminAuditTab'
import { AdminPluginsTab } from './AdminPluginsTab'

type AdminTab =
  | 'models'
  | 'knowledge'
  | 'packages'
  | 'quotas'
  | 'users'
  | 'audit'
  | 'billing'
  | 'plugins'

interface AdminTabDef {
  id: AdminTab
  label: string
  icon: LucideIcon
  description: string
}

const ADMIN_TABS: AdminTabDef[] = [
  {
    id: 'models',
    label: '模型配置',
    icon: Cpu,
    description: '对话、图片与向量模型的端点、密钥与默认策略',
  },
  {
    id: 'knowledge',
    label: '知识库',
    icon: BookOpen,
    description: '本地目录索引与第三方检索接口',
  },
  {
    id: 'packages',
    label: '规则包',
    icon: Layers,
    description: '行业规则包、YAML 校验与项目类型',
  },
  {
    id: 'quotas',
    label: '定额库',
    icon: LibraryBig,
    description: '企业综合单价、分类维护与匹配测试',
  },
  {
    id: 'users',
    label: '用户与权限',
    icon: Users,
    description: '团队成员、角色与登录状态',
  },
  {
    id: 'audit',
    label: '审计日志',
    icon: ScrollText,
    description: '全站写操作与成员变更的倒序记录',
  },
  {
    id: 'billing',
    label: '计费与充值',
    icon: Wallet,
    description: '点数、充值套餐、兑换码与订单',
  },
  {
    id: 'plugins',
    label: '插件市场',
    icon: Plug,
    description: 'MCP 客户端插件：本机命令与 HTTP 服务的工具接入',
  },
]

export function AdminConsole() {
  useDocumentTitle('管理控制台')
  const { user, isSuperAdmin } = useAuth()

  if (!user) return null
  if (!isSuperAdmin) {
    return <AdminForbidden user={user} />
  }
  return <AdminWorkspace />
}

function AdminWorkspace() {
  const { health, refresh: refreshHealth } = useHealth()
  const [tab, setTab] = useState<AdminTab>('models')
  const modelsQuery = useQuery('admin:models', getModels)
  const kbsQuery = useQuery('admin:knowledge-bases', getKnowledgeBases)
  const rulePackagesQuery = useQuery('admin:rule-packages', () => getRulePackages(true))
  const quotasQuery = useQuery('admin:quotas', () => getQuotas('', 100))
  const usersQuery = useQuery('admin:users', getUsers)

  const models = modelsQuery.data || []
  const kbs = kbsQuery.data || []
  const rulePackages = rulePackagesQuery.data || []
  const quotas = quotasQuery.data || []
  const users = usersQuery.data || []
  const chatModels = models.filter((model) => model.kind === 'chat')
  const activeUsers = users.filter((user) => user.active)
  const visiblePackages = rulePackages.filter((pkg) => !pkg.hidden)
  const builtinPackages = visiblePackages.filter((pkg) => pkg.builtin)
  const tabCounts: Record<AdminTab, number | undefined> = {
    models: modelsQuery.loading ? undefined : models.length,
    knowledge: kbsQuery.loading ? undefined : kbs.length,
    packages: rulePackagesQuery.loading ? undefined : visiblePackages.length,
    quotas: quotasQuery.loading ? undefined : quotas.length,
    users: usersQuery.loading ? undefined : users.length,
    audit: undefined,
    billing: undefined,
    plugins: undefined,
  }
  const currentTab = ADMIN_TABS.find((item) => item.id === tab) || ADMIN_TABS[0]

  return (
    <div className="admin-page">
      <section className="admin-hero">
        <div className="admin-hero-copy">
          <div className="admin-hero-eyebrow">
            <span className="admin-hero-dot" aria-hidden />
            系统控制台 · 超级管理员
          </div>
          <h1>把系统握在手里</h1>
          <p>
            配置生成模型、接入检索知识、管理规则包与团队角色。修改即时生效，并只写入本机数据库。
          </p>
          <div className="admin-hero-meta">
            <Badge tone="accent" subtle>
              <ShieldAlert size={12} /> 全部权限
            </Badge>
            <span>认证与会话由本机服务管理</span>
          </div>
        </div>
        <div className="admin-hero-metrics" aria-label="系统概览">
          <div>
            <span className="admin-metric-label">模型</span>
            <strong>{modelsQuery.loading ? '—' : formatNumber(models.length)}</strong>
            <span className="admin-metric-sub">
              {modelsQuery.loading ? '读取中' : `${chatModels.length} 个对话模型`}
            </span>
          </div>
          <div>
            <span className="admin-metric-label">知识库</span>
            <strong>{kbsQuery.loading ? '—' : formatNumber(kbs.length)}</strong>
            <span className="admin-metric-sub">
              {kbsQuery.loading
                ? '读取中'
                : `${kbs.filter((kb) => kb.kind === 'local').length} 个本地目录`}
            </span>
          </div>
          <div>
            <span className="admin-metric-label">规则包</span>
            <strong>
              {rulePackagesQuery.loading ? '—' : formatNumber(visiblePackages.length)}
            </strong>
            <span className="admin-metric-sub">
              {rulePackagesQuery.loading ? '读取中' : `${builtinPackages.length} 个内置`}
            </span>
          </div>
          <div>
            <span className="admin-metric-label">成员</span>
            <strong>{usersQuery.loading ? '—' : formatNumber(users.length)}</strong>
            <span className="admin-metric-sub">
              {usersQuery.loading ? '读取中' : `${activeUsers.length} 个启用`}
            </span>
          </div>
        </div>
        <div className="admin-hero-foot">
          <span>
            服务版本 <span className="mono">{health?.version || '—'}</span>
          </span>
          <Link to="/" className="admin-back-link">
            <ArrowLeft size={14} /> 返回项目
          </Link>
        </div>
      </section>

      <div className="admin-tabs-wrap">
        <nav className="admin-tabs" role="tablist" aria-label="管理控制台分类">
          {ADMIN_TABS.map((item) => {
            const Icon = item.icon
            const count = tabCounts[item.id]
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                className={`admin-tab ${tab === item.id ? 'is-active' : ''}`}
                onClick={() => setTab(item.id)}
              >
                <Icon size={16} aria-hidden />
                <span>{item.label}</span>
                {count !== undefined && <span className="admin-tab-count">{count}</span>}
              </button>
            )
          })}
        </nav>
        <p className="admin-tab-description">{currentTab.description}</p>
      </div>

      <div className="admin-panel" role="tabpanel">
        {tab === 'models' && (
          <AdminModelsTab
            models={models}
            loading={modelsQuery.loading}
            refreshing={modelsQuery.refreshing}
            error={modelsQuery.error}
            refresh={modelsQuery.refresh}
            refreshHealth={refreshHealth}
          />
        )}
        {tab === 'knowledge' && (
          <AdminKnowledgeTab
            kbs={kbs}
            loading={kbsQuery.loading}
            refreshing={kbsQuery.refreshing}
            error={kbsQuery.error}
            refresh={kbsQuery.refresh}
          />
        )}
        {tab === 'packages' && (
          <AdminRulePackagesTab
            packages={rulePackages}
            loading={rulePackagesQuery.loading}
            refreshing={rulePackagesQuery.refreshing}
            error={rulePackagesQuery.error}
            refresh={rulePackagesQuery.refresh}
          />
        )}
        {tab === 'quotas' && (
          <AdminQuotaTab
            quotas={quotas}
            loading={quotasQuery.loading}
            refreshing={quotasQuery.refreshing}
            error={quotasQuery.error}
            refresh={quotasQuery.refresh}
          />
        )}
        {tab === 'users' && (
          <AdminUsersTab
            users={users}
            loading={usersQuery.loading}
            refreshing={usersQuery.refreshing}
            error={usersQuery.error}
            refresh={usersQuery.refresh}
          />
        )}
        {tab === 'audit' && <AdminAuditTab />}
        {tab === 'billing' && <AdminBillingTab />}
        {tab === 'plugins' && <AdminPluginsTab />}
      </div>
    </div>
  )
}

function AdminForbidden({ user }: { user: { display_name: string; username: string; role: string; role_label: string } }) {
  const meta = roleMeta(user.role, user.role_label)
  return (
    <div className="admin-page">
      <div className="admin-forbidden">
        <div className="admin-forbidden-card">
          <div className="admin-forbidden-icon" aria-hidden>
            <ShieldAlert size={28} strokeWidth={1.5} />
          </div>
          <div className="eyebrow">权限不足 · 403</div>
          <h1>仅超级管理员可访问</h1>
          <p>
            当前账号 <strong>{user.display_name || user.username}</strong> 的角色是
            <Badge tone={meta.tone} subtle>
              {meta.label}
            </Badge>
            ，可以继续使用项目工作台，但不能进入管理控制台。
          </p>
          <div className="admin-forbidden-actions">
            <Link to="/" className="btn btn--primary btn--md">
              <ArrowLeft size={15} />
              <span className="btn-label">返回项目列表</span>
            </Link>
          </div>
          {user.role === 'viewer' && (
            <p className="admin-forbidden-hint">如需管理权限，请联系超级管理员调整角色。</p>
          )}
        </div>
      </div>
    </div>
  )
}
