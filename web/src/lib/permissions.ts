/**
 * 统一的权限能力判断。
 *
 * 过去权限规则散落在各页面（owner/admin、viewer、旧后端省略 my_role 的兼容），
 * 同一条规则在不同页面写法不同、边界不一致。这里把能力收敛成少量语义化函数，
 * 页面只问「能不能做这件事」，不再自己拼角色字符串。
 *
 * 兼容约定：老版本后端不返回 `my_role`，历史行为是按「可写/可审批」处理，
 * 需要兼容的函数用 `allowMissing` 参数显式表达，避免各处隐式重复。
 */
import type { Project, ProjectRole, User } from './types'

export const PROJECT_ADMIN_ROLES: readonly ProjectRole[] = ['owner', 'admin']

type ProjectRef = Pick<Project, 'my_role'> | null | undefined

function role(project: ProjectRef): ProjectRole | undefined {
  return project?.my_role
}

/** 全局写权限：除 viewer 外都可写。 */
export function canGlobalWrite(user: User | null | undefined): boolean {
  return Boolean(user && user.role !== 'viewer')
}

export function isSuperAdmin(user: User | null | undefined): boolean {
  return user?.role === 'super_admin'
}

/** 项目负责人 / 超管；老后端缺 my_role 时可选按负责人处理。 */
export function isProjectAdmin(project: ProjectRef, allowMissing = false): boolean {
  const value = role(project)
  if (!value) return allowMissing
  return PROJECT_ADMIN_ROLES.includes(value)
}

/** 项目级写权限：viewer 只读；老后端缺 my_role 时沿用全局 canWrite。 */
export function canEditProject(project: ProjectRef, globalCanWrite = true): boolean {
  return Boolean(globalCanWrite) && role(project) !== 'viewer'
}

/** 只读成员。 */
export function isProjectViewer(project: ProjectRef): boolean {
  return role(project) === 'viewer'
}

/** 章节复核：确认 / 锁定 / 解锁 / 撤回已确认，仅负责人或超管。 */
export function canReviewChapter(project: ProjectRef): boolean {
  return isProjectAdmin(project)
}

/** 强制导出 / 强制保存等越权操作。 */
export function canForceWrite(project: ProjectRef): boolean {
  return isProjectAdmin(project)
}

/** 审批 / 删除项目 / 管理成员：老后端缺 my_role 时按负责人处理。 */
export function canManageProject(project: ProjectRef): boolean {
  return isProjectAdmin(project, true)
}
