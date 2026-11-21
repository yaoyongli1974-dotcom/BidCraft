import { describe, expect, it } from 'vitest'

import {
  canEditProject,
  canForceWrite,
  canGlobalWrite,
  canManageProject,
  canReviewChapter,
  isProjectAdmin,
  isProjectViewer,
  isSuperAdmin,
} from './permissions'
import type { Project, User } from './types'

const project = (my_role?: string) => ({ my_role }) as Project
const user = (role: string) => ({ role }) as User

describe('permission capabilities', () => {
  it('全局写权限：viewer 只读，其余可写', () => {
    expect(canGlobalWrite(user('viewer'))).toBe(false)
    expect(canGlobalWrite(user('manager'))).toBe(true)
    expect(canGlobalWrite(null)).toBe(false)
    expect(isSuperAdmin(user('super_admin'))).toBe(true)
    expect(isSuperAdmin(user('manager'))).toBe(false)
  })

  it('负责人/超管为项目管理员，editor 与 viewer 不是', () => {
    expect(isProjectAdmin(project('owner'))).toBe(true)
    expect(isProjectAdmin(project('admin'))).toBe(true)
    expect(isProjectAdmin(project('editor'))).toBe(false)
    expect(isProjectAdmin(project('viewer'))).toBe(false)
  })

  it('复核、强制写入只认负责人/超管', () => {
    expect(canReviewChapter(project('owner'))).toBe(true)
    expect(canReviewChapter(project('editor'))).toBe(false)
    expect(canForceWrite(project('admin'))).toBe(true)
    expect(canForceWrite(project('viewer'))).toBe(false)
  })

  it('项目编辑：viewer 被拒，其余沿用全局 canWrite', () => {
    expect(canEditProject(project('editor'), true)).toBe(true)
    expect(canEditProject(project('viewer'), true)).toBe(false)
    expect(canEditProject(project('owner'), false)).toBe(false)
    expect(isProjectViewer(project('viewer'))).toBe(true)
  })

  it('老后端缺 my_role：管理类按负责人兼容，编辑类沿用全局开关', () => {
    expect(canManageProject(project())).toBe(true)
    expect(isProjectAdmin(project())).toBe(false)
    expect(isProjectAdmin(project(), true)).toBe(true)
    expect(canEditProject(project(), true)).toBe(true)
    expect(canEditProject(project(), false)).toBe(false)
  })
})
