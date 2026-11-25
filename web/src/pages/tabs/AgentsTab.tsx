import { useEffect, useState, type FormEvent } from 'react'
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Play,
  RefreshCw,
  XCircle,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  cancelWorkflow,
  createWorkflow,
  getAgentTools,
  getWorkflow,
  listAgents,
  listWorkflowApprovals,
  listWorkflowTasks,
  listWorkflows,
  resumeWorkflow,
  reviewWorkflowApproval,
} from '../../api/orchestration'
import { messageOf } from '../../lib/http'
import { approvalTypeLabel } from './AgentsTab.meta'
import type {
  AgentInfo,
  AgentTool,
  ToolCallTrace,
  WorkflowApproval,
  WorkflowRun,
  WorkflowTask,
} from '../../lib/types'
import { className, formatNumber, formatRelative, type Tone } from '../../lib/format'
import { useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../../components/Feedback'
import { TabHeading } from '../../components/Workbench'
import { useToast } from '../../context/useToast'

const STATUS_META: Record<string, { label: string; tone: Tone }> = {
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'info' },
  waiting_approval: { label: '待审批', tone: 'warn' },
  done: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

const TASK_STATUS_META: Record<string, { label: string; tone: Tone }> = {
  pending: { label: '待执行', tone: 'neutral' },
  running: { label: '执行中', tone: 'info' },
  done: { label: '完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  skipped: { label: '跳过', tone: 'neutral' },
}

function statusMeta(status: string): { label: string; tone: Tone } {
  return STATUS_META[status] || { label: status || '未知', tone: 'neutral' }
}

const TERMINAL = new Set(['done', 'failed', 'cancelled'])
/** 后端 resume_workflow 仅在失败重试 / 审批闸门后可以继续推进。 */
const RESUMABLE = new Set(['failed', 'waiting_approval'])
/** planner.build_workflow 接受的可以直接启动的类型（custom 需提供 tasks，界面不开放）。 */
const WORKFLOW_TYPES = [
  { value: 'standard', label: '标准编排' },
  { value: 'full', label: '全流程编排' },
  { value: 'bid', label: '投标编排' },
]

function statLine(run: WorkflowRun): string {
  const stats = run.stats || {}
  const parts: string[] = []
  const done = stats.done ?? stats.succeeded
  const total = stats.tasks ?? stats.total
  if (typeof done === 'number' && typeof total === 'number') parts.push(`${done}/${total} 任务`)
  if (typeof stats.tokens === 'number') parts.push(`${formatNumber(stats.tokens)} tokens`)
  if (typeof stats.cost === 'number') parts.push(`¥${stats.cost}`)
  return parts.join(' · ')
}

type VerificationState = 'verified' | 'unverified' | 'failed'

const VERIFICATION_META: Record<VerificationState, { label: string; tone: Tone }> = {
  verified: { label: 'verified', tone: 'success' },
  unverified: { label: 'unverified', tone: 'warn' },
  failed: { label: 'failed', tone: 'danger' },
}

/** 只认后端真实给出的值；缺失一律 unverified，不推断成功或 ok。 */
function verificationState(call: ToolCallTrace): VerificationState {
  const value = (call.verification || '').trim().toLowerCase()
  if (value === 'verified') return 'verified'
  if (value === 'failed') return 'failed'
  return 'unverified'
}

function traceText(value: unknown): string {
  if (value === undefined || value === null) return '—'
  try {
    const text = JSON.stringify(value)
    return text === undefined ? String(value) : text
  } catch {
    return String(value)
  }
}

function truncate(text: string, limit = 180): string {
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

/** 注册表卡片上的只读工具目录；传入项目 ref，viewer 也可查看。 */
function AgentToolCatalog({ projectRef, agentType }: { projectRef: string; agentType: string }) {
  const [open, setOpen] = useState(false)
  const query = useQuery(
    `agent-tools:${projectRef}:${agentType}`,
    () => getAgentTools(projectRef, agentType),
    { enabled: open },
  )
  const tools: AgentTool[] = query.data || []
  return (
    <div className="agent-tools">
      <button
        type="button"
        className="agent-tools-toggle"
        aria-label={`工具目录：${agentType}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        工具目录{tools.length ? ` (${tools.length})` : ''}
      </button>
      {open && (
        query.loading ? (
          <small className="agent-tools-hint">加载中…</small>
        ) : query.error ? (
          <small className="agent-tools-hint is-error" role="alert">加载失败：{query.error}</small>
        ) : tools.length === 0 ? (
          <small className="agent-tools-hint">无可用工具</small>
        ) : (
          <ul className="agent-tool-list">
            {tools.map((tool) => (
              <li key={tool.name} className="agent-tool-item" title={tool.description}>
                <code>{tool.name}</code>
                <span>{tool.readonly ? '只读' : tool.side_effect}</span>
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  )
}

/** Agent 编排：注册表、工作流列表/新建、任务时间线、人工审批。 */
export function AgentsTab() {
  const { ref, readOnly } = useWorkspace()
  const toast = useToast()
  const agentsQuery = useQuery('agents', () => listAgents())
  const workflowsQuery = useQuery(`workflows:${ref}`, () => listWorkflows(ref))
  const approvalsQuery = useQuery(`workflow-approvals:${ref}`, () => listWorkflowApprovals(ref))

  const [selectedId, setSelectedId] = useState('')
  const [goal, setGoal] = useState('')
  const [workflowType, setWorkflowType] = useState('standard')
  const [creating, setCreating] = useState(false)
  const [detail, setDetail] = useState<WorkflowRun | null>(null)
  const [tasks, setTasks] = useState<WorkflowTask[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailNonce, setDetailNonce] = useState(0)
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({})
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({})

  const workflows = workflowsQuery.data || []
  const approvals = approvalsQuery.data || []
  const agents = agentsQuery.data || []

  // 运行中/排队/待审批的工作流：2s 轮询，直到进入终态。
  const hasActive = workflows.some((run) => !TERMINAL.has(run.status))
  useEffect(() => {
    if (!hasActive) return
    const timer = window.setInterval(() => workflowsQuery.refresh(), 2000)
    return () => window.clearInterval(timer)
  }, [hasActive, workflowsQuery])

  const selected = workflows.find((run) => run.id === selectedId) || null

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      setTasks([])
      setDetailError(null)
      return
    }
    let cancelled = false
    // 切换运行或重试时先清空旧详情，避免加载失败后出现永久骨架屏。
    setDetail(null)
    setTasks([])
    setDetailError(null)
    const load = async () => {
      try {
        const [run, taskList] = await Promise.all([
          getWorkflow(ref, selected.id),
          listWorkflowTasks(ref, selected.id),
        ])
        if (cancelled) return
        setDetail(run)
        setTasks(taskList)
      } catch (error) {
        if (cancelled) return
        setDetail(null)
        setTasks([])
        setDetailError(messageOf(error))
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [ref, selected, detailNonce])

  /** resume / 审批会推进任务轨迹，动作成功后回读任务列表（失败保留旧列表）。 */
  const refreshTasks = async (runId: string) => {
    try {
      setTasks(await listWorkflowTasks(ref, runId))
    } catch {
      // 详情轮询仍会重试；此处不把成功的动作标成失败。
    }
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (readOnly || creating || !goal.trim()) return
    setCreating(true)
    setActionError(null)
    try {
      const run = await createWorkflow(ref, {
        workflow_type: workflowType,
        goal: goal.trim(),
        input: {},
        budget: {},
      })
      toast.success('工作流已创建', run.id)
      setGoal('')
      setSelectedId(run.id)
      await workflowsQuery.refresh()
      await approvalsQuery.refresh()
    } catch (error) {
      setActionError(messageOf(error))
    } finally {
      setCreating(false)
    }
  }

  const act = async (runId: string, action: 'cancel' | 'resume') => {
    if (readOnly) return
    setActionError(null)
    try {
      const fn = action === 'cancel' ? cancelWorkflow : resumeWorkflow
      await fn(ref, runId)
      toast.success(action === 'cancel' ? '已取消工作流' : '已恢复工作流', runId)
      await workflowsQuery.refresh()
      await refreshTasks(runId)
      if (selectedId === runId) {
        try {
          setDetail(await getWorkflow(ref, runId))
        } catch (error) {
          setDetail(null)
          setDetailError(messageOf(error))
        }
      }
    } catch (error) {
      setActionError(messageOf(error))
    }
  }

  const review = async (approval: WorkflowApproval, decision: 'approve' | 'reject') => {
    if (readOnly) return
    setActionError(null)
    try {
      await reviewWorkflowApproval(ref, approval.id, {
        decision,
        note: (reviewNotes[approval.id] || '').trim(),
      })
      toast.success(
        decision === 'approve' ? '已通过审批' : '已驳回',
        approvalTypeLabel(approval.approval_type),
      )
      setReviewNotes((current) => ({ ...current, [approval.id]: '' }))
      await Promise.all([approvalsQuery.refresh(), workflowsQuery.refresh()])
      const reviewedRunId = detail?.id || approval.run_id
      if (reviewedRunId) await refreshTasks(reviewedRunId)
      if (selectedId && detail) {
        try {
          setDetail(await getWorkflow(ref, detail.id))
        } catch (error) {
          setDetail(null)
          setDetailError(messageOf(error))
        }
      }
    } catch (error) {
      setActionError(messageOf(error))
    }
  }

  const pendingApprovals = approvals.filter((item) => item.status === 'pending')
  const processedApprovals = approvals.filter((item) => item.status !== 'pending')

  return (
    <div className="tab-content agents-tab">
      <TabHeading
        eyebrow="AI Agent 编排"
        title="Agent 工作流"
        description="按 DAG 依赖编排招标解析、技术标、商务标、合规与审批；运行中自动刷新进度。"
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon={<RefreshCw size={14} />}
            onClick={() => {
              agentsQuery.refresh()
              workflowsQuery.refresh()
              approvalsQuery.refresh()
            }}
            disabled={workflowsQuery.refreshing || approvalsQuery.refreshing}
          >
            刷新
          </Button>
        }
      />

      {actionError && (
        <div className="inline-error" role="alert">
          <p>{actionError}</p>
        </div>
      )}

      <div className="agents-layout">
        <div className="agents-column">
          <section className="panel agents-card">
            <div className="panel-head">
              <div>
                <div className="eyebrow">可用 Agent</div>
                <h3>注册表</h3>
              </div>
            </div>
            {agentsQuery.loading ? (
              <Skeleton height={90} />
            ) : agentsQuery.error ? (
              <ErrorState message={agentsQuery.error} onRetry={agentsQuery.refresh} />
            ) : agents.length === 0 ? (
              <EmptyState icon={Bot} title="暂无 Agent" description="服务端未注册任何 Agent。" />
            ) : (
              <div className="agent-grid">
                {agents.map((agent: AgentInfo) => (
                  <div key={agent.agent_type} className="agent-card">
                    <div className="agent-card-head">
                      <Bot size={15} />
                      <strong>{agent.name || agent.agent_type}</strong>
                      {agent.enabled === false && <Badge tone="neutral" subtle>停用</Badge>}
                    </div>
                    {agent.description && <p>{agent.description}</p>}
                    {!!agent.capabilities?.length && (
                      <div className="agent-chips">
                        {agent.capabilities.slice(0, 4).map((cap) => (
                          <span key={cap} className="agent-chip">{cap}</span>
                        ))}
                      </div>
                    )}
                    <AgentToolCatalog projectRef={ref} agentType={agent.agent_type} />
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel agents-card">
            <div className="panel-head">
              <div>
                <div className="eyebrow">新建工作流</div>
                <h3>启动一次编排</h3>
              </div>
            </div>
            <form className="agents-form" onSubmit={(event) => void submit(event)}>
              <label>
                <span>目标</span>
                <input
                  value={goal}
                  onChange={(event) => setGoal(event.target.value)}
                  placeholder="例如：基于现有事实编制本项目技术标"
                  disabled={readOnly || creating}
                />
              </label>
              <label>
                <span>工作流类型</span>
                <select
                  aria-label="工作流类型"
                  value={workflowType}
                  onChange={(event) => setWorkflowType(event.target.value)}
                  disabled={readOnly || creating}
                >
                  {WORKFLOW_TYPES.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </select>
                <p className="field-hint">仅支持标准 / 全流程 / 投标三种内置编排；自定义类型需通过 API 提供任务列表。</p>
              </label>
              <Button
                type="submit"
                variant="primary"
                size="sm"
                icon={<Play size={14} />}
                loading={creating}
                disabled={readOnly || creating || !goal.trim()}
              >
                启动
              </Button>
            </form>
          </section>

          <section className="panel agents-card">
            <div className="panel-head">
              <div>
                <div className="eyebrow">运行实例</div>
                <h3>工作流列表</h3>
              </div>
            </div>
            {workflowsQuery.loading ? (
              <Skeleton height={90} />
            ) : workflowsQuery.error ? (
              <ErrorState message={workflowsQuery.error} onRetry={workflowsQuery.refresh} />
            ) : workflows.length === 0 ? (
              <EmptyState icon={Play} title="暂无工作流" description="启动一次编排后将在此显示。" />
            ) : (
              <div className="agents-runs">
                {workflows.map((run) => {
                  const meta = statusMeta(run.status)
                  const summary = statLine(run)
                  return (
                    <button
                      key={run.id}
                      type="button"
                      className={className('agents-run', selectedId === run.id && 'is-active')}
                      onClick={() => setSelectedId(run.id)}
                    >
                      <span className="agents-run-main">
                        <strong>{run.goal || run.workflow_type}</strong>
                        <small>{summary || run.created_at || run.id}</small>
                      </span>
                      <Badge tone={meta.tone} subtle>{meta.label}</Badge>
                    </button>
                  )
                })}
              </div>
            )}
          </section>
        </div>

        <div className="agents-column">
          <section className="panel agents-card">
            <div className="panel-head">
              <div>
                <div className="eyebrow">人工审批</div>
                <h3>待处理闸门</h3>
              </div>
            </div>
            {approvalsQuery.loading ? (
              <Skeleton height={60} />
            ) : approvals.length === 0 ? (
              <EmptyState icon={CheckCircle2} title="无待审批项" description="工作流到达审批闸门时会出现在这里。" />
            ) : (
              <div className="approval-list">
                {[...pendingApprovals, ...processedApprovals].map((approval) => (
                  <div key={approval.id} className="approval-item">
                    <div className="approval-main">
                      <strong>{approvalTypeLabel(approval.approval_type)}</strong>
                      <small>{approval.run_id || ''}</small>
                      <small>提交人：{approval.submitted_by || '—'}</small>
                      {approval.status !== 'pending' && (
                        <small>
                          审批人：{approval.reviewed_by || '—'}
                          {approval.review_note ? ` · 意见：${approval.review_note}` : ''}
                        </small>
                      )}
                    </div>
                    {approval.status === 'pending' ? (
                      <div className="approval-actions">
                        <input
                          className="input input--sm"
                          aria-label={`审批意见：${approvalTypeLabel(approval.approval_type)}`}
                          placeholder="审批意见（可选）"
                          value={reviewNotes[approval.id] || ''}
                          onChange={(event) =>
                            setReviewNotes((current) => ({ ...current, [approval.id]: event.target.value }))
                          }
                          disabled={readOnly}
                        />
                        <Button
                          variant="primary"
                          size="sm"
                          icon={<CheckCircle2 size={13} />}
                          disabled={readOnly}
                          onClick={() => void review(approval, 'approve')}
                        >
                          通过
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<XCircle size={13} />}
                          disabled={readOnly}
                          onClick={() => void review(approval, 'reject')}
                        >
                          驳回
                        </Button>
                      </div>
                    ) : (
                      <Badge tone={approval.status === 'approved' ? 'success' : 'danger'} subtle>
                        {approval.status === 'approved' ? '已通过' : '已驳回'}
                      </Badge>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel agents-card">
            <div className="panel-head">
              <div>
                <div className="eyebrow">运行详情</div>
                <h3>{selected ? selected.goal || selected.id : '未选择工作流'}</h3>
              </div>
              {selected && (
                <div className="agents-detail-actions">
                  {RESUMABLE.has(selected.status) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={readOnly || (selected.status === 'waiting_approval' && pendingApprovals.length > 0)}
                      title={selected.status === 'waiting_approval' && pendingApprovals.length > 0
                        ? '请先处理待审批项后再恢复'
                        : undefined}
                      onClick={() => void act(selected.id, 'resume')}
                    >
                      恢复
                    </Button>
                  )}
                  {selected.status !== 'done' && selected.status !== 'cancelled' && (
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<CircleSlash size={13} />}
                      disabled={readOnly}
                      onClick={() => void act(selected.id, 'cancel')}
                    >
                      取消
                    </Button>
                  )}
                </div>
              )}
            </div>
            {!selected ? (
              <EmptyState icon={Play} title="选择左侧工作流" description="点击一个运行实例查看任务时间线。" />
            ) : detailError ? (
              <ErrorState
                message={`运行详情加载失败：${detailError}`}
                onRetry={() => setDetailNonce((value) => value + 1)}
              />
            ) : detail === null ? (
              <Skeleton height={160} />
            ) : (
              <>
                <div className="agents-detail-meta">
                  <Badge tone={statusMeta(detail.status).tone} subtle>{statusMeta(detail.status).label}</Badge>
                  {statLine(detail) && <span>{statLine(detail)}</span>}
                  {detail.completed_at && <span>完成于 {formatRelative(detail.completed_at)}</span>}
                </div>
                <ol className="agents-timeline">
                  {tasks.length === 0 ? (
                    <li className="agents-timeline-empty">暂无任务记录</li>
                  ) : (
                    tasks.map((task) => {
                      const tm = TASK_STATUS_META[task.status] || { label: task.status, tone: 'neutral' as Tone }
                      return (
                        <li key={task.id} className="agents-timeline-item">
                          <span className="agents-task-seq">{task.seq}</span>
                          <div className="agents-task-main">
                            <strong>{task.task_key || task.agent_type}</strong>
                            <small>{task.agent_type}{task.attempts ? ` · 第 ${task.attempts} 次` : ''}</small>
                            {task.error && <span className="agents-task-error">{task.error}</span>}
                            {!!task.tool_calls?.length && (() => {
                              const toggleKey = `${task.id}:trace`
                              const open = expandedTools[toggleKey] === true
                              return (
                                <div className="agents-tool-traces">
                                  <button
                                    type="button"
                                    className="agents-tool-toggle"
                                    aria-label={`工具调用轨迹：${task.task_key || task.agent_type}`}
                                    aria-expanded={open}
                                    onClick={() =>
                                      setExpandedTools((current) => ({
                                        ...current,
                                        [toggleKey]: !current[toggleKey],
                                      }))
                                    }
                                  >
                                    {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                                    工具轨迹 ({task.tool_calls.length})
                                  </button>
                                  {open && (
                                    <ul className="agents-tool-trace-list">
                                      {task.tool_calls.map((call, index) => {
                                        const state = verificationState(call)
                                        const meta = VERIFICATION_META[state]
                                        return (
                                          <li
                                            key={`${call.name}-${index}-${call.audit_id || ''}`}
                                            className={className(
                                              'agents-tool-trace',
                                              state === 'unverified' && 'is-unverified',
                                              (state === 'failed' || call.is_error === true) && 'is-error',
                                            )}
                                          >
                                            <div className="agents-tool-trace-head">
                                              <strong>{call.name}</strong>
                                              <Badge tone={meta.tone} subtle>{meta.label}</Badge>
                                              {call.is_error !== null && (
                                                <small>is_error={String(call.is_error)}</small>
                                              )}
                                              {call.ms !== null && <small>{call.ms} ms</small>}
                                            </div>
                                            <dl className="agents-tool-trace-meta">
                                              <dt>args</dt>
                                              <dd><code>{traceText(call.args)}</code></dd>
                                              <dt>result</dt>
                                              <dd><code>{truncate(traceText(call.result))}</code></dd>
                                              <dt>audit_id</dt>
                                              <dd>{call.audit_id || '—'}</dd>
                                              <dt>ts</dt>
                                              <dd>{call.ts || '—'}</dd>
                                            </dl>
                                          </li>
                                        )
                                      })}
                                    </ul>
                                  )}
                                </div>
                              )
                            })()}
                          </div>
                          <Badge tone={tm.tone} subtle>{tm.label}</Badge>
                        </li>
                      )
                    })
                  )}
                </ol>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
