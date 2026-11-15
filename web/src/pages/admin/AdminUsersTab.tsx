import { useState } from 'react'
import {
  Coins,
  Eye,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserCog,
  UserRound,
  type LucideIcon,
} from 'lucide-react'
import type { User } from '../../lib/types'
import {
  className,
  formatDate,
  formatNumber,
  formatRelative,
  roleHint,
  roleMeta,
} from '../../lib/format'
import { deleteUser, messageOf, updateUser } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { useConfirm } from '../../context/ConfirmContext'
import { useToast } from '../../context/ToastContext'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState } from '../../components/Feedback'
import {
  AdminActionError,
  AdminSectionLabel,
  AdminSkeletonRows,
} from './AdminShared'
import { AdminUserModal, type UserModalState } from './AdminUserModal'
import { AdminCreditModal } from './AdminCreditModal'

interface UsersTabProps {
  users: User[]
  loading: boolean
  refreshing: boolean
  error: string | null
  refresh: () => void
}

interface RoleLegend {
  role: string
  label: string
  hint: string
  icon: LucideIcon
  tone: 'accent' | 'info' | 'neutral'
}

const ROLE_LEGEND: RoleLegend[] = [
  {
    role: 'super_admin',
    label: '超级管理员',
    hint: '全部权限，可进入管理控制台',
    icon: ShieldCheck,
    tone: 'accent',
  },
  {
    role: 'manager',
    label: '投标经理',
    hint: '项目读写、生成、核对与导出',
    icon: UserCog,
    tone: 'info',
  },
  {
    role: 'viewer',
    label: '只读成员',
    hint: '查看项目、台账、正文与核对结果',
    icon: Eye,
    tone: 'neutral',
  },
]

export function AdminUsersTab({
  users,
  loading,
  refreshing,
  error,
  refresh,
}: UsersTabProps) {
  const toast = useToast()
  const confirm = useConfirm()
  const { user: currentUser, syncUser } = useAuth()
  const [modal, setModal] = useState<UserModalState | null>(null)
  const [creditUser, setCreditUser] = useState<User | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const toggleActive = async (target: User) => {
    const nextActive = !target.active
    const accepted = await confirm({
      title: nextActive ? '启用这个账号？' : '停用这个账号？',
      description: `${target.display_name || target.username}（@${target.username}）${
        nextActive ? '将可以重新登录并访问系统。' : '将不能继续登录，现有会话会失效。'
      }`,
      note: '当前登录账号与最后一名超级管理员由后端保护，不允许停用。',
      confirmLabel: nextActive ? '启用账号' : '停用账号',
      tone: nextActive ? 'primary' : 'warning',
    })
    if (!accepted) return
    setBusyId(target.id)
    setActionError(null)
    try {
      await updateUser(target.id, { active: nextActive })
      refresh()
      toast.success(nextActive ? '账号已启用' : '账号已停用', target.display_name || target.username)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('更新账号状态失败', message)
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (target: User) => {
    const accepted = await confirm({
      title: '删除这个账号？',
      description: `${target.display_name || target.username}（@${target.username}）将无法再登录。`,
      note: '当前登录账号与最后一名超级管理员由后端保护，不允许删除。',
      confirmLabel: '删除账号',
      tone: 'danger',
    })
    if (!accepted) return
    setBusyId(target.id)
    setActionError(null)
    try {
      await deleteUser(target.id)
      refresh()
      toast.success('账号已删除', target.display_name || target.username)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除账号失败', message)
    } finally {
      setBusyId(null)
    }
  }

  const onSaved = (saved: User, created: boolean) => {
    setModal(null)
    refresh()
    toast.success(created ? '成员已创建' : '成员已更新', saved.display_name || saved.username)
  }

  return (
    <div className="admin-users">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="团队成员"
          title="用户与权限"
          description="创建账号并分配角色。只读成员可以查看全部项目内容，但不能执行任何写操作。"
          aside={
            <Button
              variant="primary"
              icon={<Plus size={15} />}
              onClick={() => setModal({ mode: 'create' })}
            >
              添加成员
            </Button>
          }
        />
      </div>

      <div className="admin-role-legend" aria-label="角色权限说明">
        {ROLE_LEGEND.map((item) => {
          const Icon = item.icon
          return (
            <div key={item.role} className="admin-role-card">
              <span className="admin-role-icon" aria-hidden>
                <Icon size={16} />
              </span>
              <div>
                <Badge tone={item.tone} subtle>
                  {item.label}
                </Badge>
                <p>{item.hint}</p>
              </div>
              <span className="admin-role-count">
                {users.filter((user) => user.role === item.role).length}
              </span>
            </div>
          )
        })}
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      <div className="admin-list-toolbar">
        <span className="admin-list-caption">
          共 {users.length} 个账号 · {users.filter((user) => user.active).length} 个启用
        </span>
        <button type="button" className="text-btn" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={13} className={refreshing ? 'spin' : undefined} />
          刷新
        </button>
      </div>

      {error ? (
        <div className="admin-panel-inner">
          <ErrorState message={error} onRetry={refresh} />
        </div>
      ) : loading ? (
        <AdminSkeletonRows rows={3} />
      ) : users.length === 0 ? (
        <div className="admin-panel-inner">
          <EmptyState
            icon={UserRound}
            title="还没有成员"
            description="创建第一个投标经理或只读成员，账号会立即生效。"
            actions={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={() => setModal({ mode: 'create' })}
              >
                添加成员
              </Button>
            }
          />
        </div>
      ) : (
        <div className="admin-user-table" role="table" aria-label="用户列表">
          <div className="admin-user-head" role="row">
            <span role="columnheader">成员</span>
            <span role="columnheader">用户名</span>
            <span role="columnheader">角色</span>
            <span role="columnheader">状态</span>
            <span role="columnheader">点数</span>
            <span role="columnheader">最近登录</span>
            <span role="columnheader" className="admin-user-actions-head">
              操作
            </span>
          </div>
          {users.map((item) => {
            const meta = roleMeta(item.role, item.role_label)
            const isCurrent = currentUser?.id === item.id
            return (
              <div
                key={item.id}
                className={className('admin-user-row', !item.active && 'is-disabled')}
                role="row"
              >
                <div className="admin-user-person" role="cell">
                  <span className="admin-user-avatar" aria-hidden>
                    {(item.display_name || item.username).trim().slice(0, 1)}
                  </span>
                  <div>
                    <strong>{item.display_name || item.username}</strong>
                    <span>
                      {isCurrent ? '当前登录账号' : `创建于 ${formatDate(item.created_at, false)}`}
                    </span>
                  </div>
                </div>
                <div className="admin-user-handle mono" role="cell">
                  @{item.username}
                </div>
                <div className="admin-user-role" role="cell">
                  <Badge tone={meta.tone} subtle>
                    {meta.label}
                  </Badge>
                  <small className="admin-user-role-hint">{roleHint(item.role)}</small>
                </div>
                <div className="admin-user-status" role="cell">
                  <Badge tone={item.active ? 'success' : 'neutral'} subtle>
                    {item.active ? '已启用' : '已停用'}
                  </Badge>
                </div>
                <div className="admin-user-credits" role="cell">
                  <span className="admin-credit-value">
                    <Coins size={12} />
                    {formatNumber(item.credits ?? 0)}
                    <small>点</small>
                  </span>
                </div>
                <div className="admin-user-last" role="cell">
                  <span title={formatDate(item.last_login_at)}>
                    {item.last_login_at ? formatRelative(item.last_login_at) : '从未登录'}
                  </span>
                </div>
                <div className="admin-user-actions" role="cell">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Pencil size={12} />}
                    onClick={() => setModal({ mode: 'edit', user: item })}
                  >
                    编辑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Coins size={12} />}
                    onClick={() => setCreditUser(item)}
                  >
                    点数
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={busyId === item.id}
                    disabled={Boolean(busyId)}
                    onClick={() => void toggleActive(item)}
                  >
                    {item.active ? '停用' : '启用'}
                  </Button>
                  <IconButton
                    label={`删除成员 ${item.display_name || item.username}`}
                    tone="danger"
                    disabled={Boolean(busyId)}
                    onClick={() => void remove(item)}
                  >
                    <Trash2 size={14} />
                  </IconButton>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="admin-footnote">
        超级管理员可以管理账号、模型与知识库；投标经理拥有项目写权限；只读成员只能读取。
        删除或停用当前账号、最后一名超级管理员会被后端拒绝并显示原始原因。
      </div>

      {modal && (
        <AdminUserModal
          open
          mode={modal.mode}
          user={modal.mode === 'edit' ? modal.user : undefined}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}

      {creditUser && (
        <AdminCreditModal
          open
          user={creditUser}
          onClose={() => setCreditUser(null)}
          onSaved={(saved, amount) => {
            setCreditUser(null)
            refresh()
            void syncUser()
            toast.success(
              amount > 0 ? '点数已增加' : '点数已扣除',
              `${saved.display_name || saved.username} · ${amount > 0 ? '+' : ''}${formatNumber(amount)} 点`,
            )
          }}
        />
      )}
    </div>
  )
}
