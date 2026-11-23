import { useRef, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  ArrowLeft,
  BookMarked,
  BookOpen,
  Cpu,
  Layers,
  LibraryBig,
  MessageCircle,
  Plug,
  ScrollText,
  ShieldAlert,
  SpellCheck,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react'
import { useAuth } from '../../context/useAuth'
import { useHealth } from '../../context/useHealth'
import { useDocumentTitle, useQuery } from '../../lib/hooks'
import { getUsers } from '../../api/auth'
import { getQuotas } from '../../api/boq'
import { getModels, getRulePackages, getTextQa } from '../../api/compliance'
import { getKnowledgeBases, getTerms } from '../../api/knowledge'
import { formatNumber, roleMeta } from '../../lib/format'
import { Badge } from '../../components/Badge'
import { AdminModelsTab } from './AdminModelsTab'
import { AdminKnowledgeTab } from './AdminKnowledgeTab'
import { AdminQuotaTab } from './AdminQuotaTab'
import { AdminTermsTab } from './AdminTermsTab'
import { AdminTextQaTab } from './AdminTextQaTab'
import { AdminRulePackagesTab } from './AdminRulePackagesTab'
import { AdminUsersTab } from './AdminUsersTab'
import { AdminBillingTab } from './AdminBillingTab'
import { AdminHealthTab } from './AdminHealthTab'
import { AdminAuditTab } from './AdminAuditTab'
import { AdminPluginsTab } from './AdminPluginsTab'
import { AdminWechatTab } from './AdminWechatTab'

type AdminTab =
  | 'models'
  | 'knowledge'
  | 'packages'
  | 'quotas'
  | 'terms'
  | 'textqa'
  | 'users'
  | 'audit'
  | 'billing'
  | 'wechat'
  | 'plugins'
  | 'health'

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
    id: 'terms',
    label: '术语库',
    icon: BookMarked,
    description: '企业用词规范：禁用表述与建议替换',
  },
  {
    id: 'textqa',
    label: '文本规则',
    icon: SpellCheck,
    description: '错别字词库：内置规则不可删，企业自定义优先',
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
  {
    id: 'wechat',
    label: '微信开放平台',
    icon: MessageCircle,
    description: '扫码登录的 AppID、密钥、回调地址与模拟模式',
  },
  {
    id: 'health',
    label: '运行状态',
    icon: Activity,
    description: '完整健康检查与质量指标口径：版本、模型、磁盘、开关',
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
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const modelsQuery = useQuery('admin:models', getModels)
  const kbsQuery = useQuery('admin:knowledge-bases', getKnowledgeBases)
  const rulePackagesQuery = useQuery('admin:rule-packages', () => getRulePackages(true))
  const quotasQuery = useQuery('admin:quotas', () => getQuotas('', 100))
  const termsQuery = useQuery('admin:terms', () => getTerms({ limit: 200 }))
  const textQaQuery = useQuery('admin:textqa', getTextQa)
  const usersQuery = useQuery('admin:users', getUsers)

  const models = modelsQuery.data || []
  const kbs = kbsQuery.data || []
  const rulePackages = rulePackagesQuery.data || []
  const quotas = quotasQuery.data || []
  const terms = termsQuery.data || []
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
    terms: termsQuery.loading ? undefined : terms.length,
    textqa: textQaQuery.loading ? undefined : textQaQuery.data?.count ?? 0,
    users: usersQuery.loading ? undefined : users.length,
    audit: undefined,
    billing: undefined,
    wechat: undefined,
    plugins: undefined,
    health: undefined,
  }
  const currentTab = ADMIN_TABS.find((item) => item.id === tab) || ADMIN_TABS[0]

  // WAI-ARIA horizontal tabs: arrows wrap, Home/End jump to the edges.
  const onTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    const total = ADMIN_TABS.length
    let nextIndex: number
    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (index + 1) % total
        break
      case 'ArrowLeft':
        nextIndex = (index - 1 + total) % total
        break
      case 'Home':
        nextIndex = 0
        break
      case 'End':
        nextIndex = total - 1
        break
      default:
        return
    }
    event.preventDefault()
    const next = ADMIN_TABS[nextIndex]
    if (!next) return
    setTab(next.id)
    tabRefs.current[next.id]?.focus()
  }

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
          {ADMIN_TABS.map((item, index) => {
            const Icon = item.icon
            const count = tabCounts[item.id]
            return (
              <button
                key={item.id}
                id={`admin-tab-${item.id}`}
                ref={(element) => {
                  tabRefs.current[item.id] = element
                }}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                aria-controls="admin-tabpanel"
                tabIndex={tab === item.id ? 0 : -1}
                className={`admin-tab ${tab === item.id ? 'is-active' : ''}`}
                onClick={() => setTab(item.id)}
                onKeyDown={(event) => onTabKeyDown(event, index)}
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

      <div
        className="admin-panel"
        id="admin-tabpanel"
        role="tabpanel"
        aria-labelledby={`admin-tab-${tab}`}
      >
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
        {tab === 'terms' && (
          <AdminTermsTab
            terms={terms}
            loading={termsQuery.loading}
            refreshing={termsQuery.refreshing}
            error={termsQuery.error}
            refresh={termsQuery.refresh}
          />
        )}
        {tab === 'textqa' && (
          <AdminTextQaTab
            settings={textQaQuery.data}
            loading={textQaQuery.loading}
            refreshing={textQaQuery.refreshing}
            error={textQaQuery.error}
            refresh={textQaQuery.refresh}
            onSaved={textQaQuery.setData}
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
        {tab === 'wechat' && <AdminWechatTab />}
        {tab === 'health' && <AdminHealthTab />}
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
