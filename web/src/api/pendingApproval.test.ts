import { describe, expect, it } from 'vitest'

import { normalizePendingApproval, normalizePendingWorkflowApproval } from './shared'

describe('normalizePendingApproval', () => {
  it('保留商务标待办标识以便跳转商务标页签', () => {
    const item = normalizePendingApproval({
      project_id: 'p1',
      project_name: '项目甲',
      version_id: 'c1',
      version_name: '商务标 · 终版',
      doc_id: 'c1',
      doc_name: '终版',
      stage: 1,
      stage_count: 1,
      mode: '',
      submitted_by: '张三',
      submitted_at: '2026-11-24T09:00:00+08:00',
      total_amount: 100,
    })

    expect(item?.doc_id).toBe('c1')
    expect(item?.doc_name).toBe('终版')
  })

  it('旧报价待办没有商务标字段时保持 undefined', () => {
    const item = normalizePendingApproval({
      project_id: 'p1',
      version_id: 'v1',
    })

    expect(item?.doc_id).toBeUndefined()
    expect(item?.doc_name).toBeUndefined()
  })

  it('保留后端当前审批级解释与空链负责人说明', () => {
    const item = normalizePendingApproval({
      project_id: 'p1',
      version_id: 'v1',
      stage: 1,
      stage_count: 1,
      approval_explain: {
        index: 1,
        applicable: true,
        conditions: [],
        missing_data: [],
        owner_only: true,
        note: '未配置审批链，由项目 owner 审批',
      },
    })

    expect(item?.approval_explain?.owner_only).toBe(true)
    expect(item?.approval_explain?.note).toContain('owner')
    expect(item?.approval_explain?.index).toBe(1)
  })
})

describe('normalizePendingWorkflowApproval', () => {
  it('保留工作流闸门的项目、运行与标签字段以便跳转 Agent 工作流页签', () => {
    const item = normalizePendingWorkflowApproval({
      id: 'a1',
      run_id: 'r1',
      task_id: 't1',
      approval_type: 'gate',
      status: 'pending',
      project_id: 'p1',
      project_name: '项目甲',
      workflow_type: 'bid',
      run_status: 'waiting_approval',
      goal: '复核报价',
      labels: ['关键', '关键', '复核'],
      submitted_by: '张三',
      submitted_at: '2026-11-24T09:00:00+08:00',
    })

    expect(item).toMatchObject({
      id: 'a1',
      run_id: 'r1',
      task_id: 't1',
      approval_type: 'gate',
      status: 'pending',
      project_id: 'p1',
      workflow_type: 'bid',
      labels: ['关键', '复核'],
    })
  })

  it('旧数据只有 run_id 时也能识别，缺失 project_id 时丢弃', () => {
    const item = normalizePendingWorkflowApproval({ run_id: 'r2', project_id: 'p2', type: 'review' })

    expect(item?.id).toBe('r2')
    expect(item?.approval_type).toBe('review')
    expect(item?.task_id).toBeNull()
    expect(normalizePendingWorkflowApproval({ id: 'a3' })).toBeNull()
  })
})
