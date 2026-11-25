import { ApiError, asArray, jsonBody, request } from '../lib/http'
import type {
  AgentInfo,
  AgentTool,
  PendingWorkflowApproval,
  ToolCallTrace,
  WorkflowApproval,
  WorkflowApprovalReviewInput,
  WorkflowCreateInput,
  WorkflowRun,
  WorkflowTask,
} from '../lib/types'
import {
  asNumber,
  asStringList,
  asText,
  isoOrNull,
  normalizePendingWorkflowApproval,
} from './shared'

// ---------------------------------------------------------------- agent orchestration

const projectPath = (ref: string, suffix: string) =>
  `/projects/${encodeURIComponent(ref)}${suffix}`

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

/** Accept both a bare list and `{ workflows | runs: [...] }`. */
function listFrom(value: unknown, keys: string[]): unknown[] {
  if (Array.isArray(value)) return value
  for (const key of keys) {
    const list = asArray<unknown>(value, key)
    if (list.length) return list
  }
  return []
}

/** Unwrap `{ worklfow }` / `{ run }` / `{ approval }` envelopes. */
function unwrap(value: unknown, keys: string[]): unknown {
  const record = asRecord(value)
  if (!record) return value
  for (const key of keys) {
    if (record[key] !== undefined) return record[key]
  }
  return value
}

/**
 * 归一化一条工具调用轨迹。只保留后端真实字段，缺失值一律 null，
 * 绝不把缺省 verification 推断成 verified，也不合成 ok/success 标记。
 */
export function normalizeToolCallTrace(value: unknown): ToolCallTrace | null {
  const raw = asRecord(value)
  if (!raw) return null
  const name = asText(raw.name ?? raw.tool ?? raw.tool_name).trim()
  if (!name) return null
  const verification = raw.verification === undefined || raw.verification === null
    ? null
    : asText(raw.verification) || null
  const auditId = raw.audit_id === undefined || raw.audit_id === null
    ? null
    : asText(raw.audit_id) || null
  const ms = typeof raw.ms === 'number' && Number.isFinite(raw.ms) ? raw.ms : null
  return {
    name,
    args: asRecord(raw.args ?? raw.arguments),
    result: raw.result === undefined ? null : raw.result,
    verification,
    audit_id: auditId,
    ts: isoOrNull(raw.ts ?? raw.timestamp),
    is_error: typeof raw.is_error === 'boolean' ? raw.is_error : null,
    ms,
  }
}

/** 轨迹可能嵌套在 output/result 中；只接受数组，脏值按“无轨迹”处理。 */
export function normalizeToolCalls(value: unknown): ToolCallTrace[] {
  if (!Array.isArray(value)) return []
  return value
    .map(normalizeToolCallTrace)
    .filter((item): item is ToolCallTrace => item !== null)
}

export function normalizeAgentTool(value: unknown): AgentTool | null {
  const raw = asRecord(value)
  if (!raw) return null
  const name = asText(raw.name).trim()
  if (!name) return null
  return {
    name,
    description: asText(raw.description),
    input_schema: asRecord(raw.input_schema ?? raw.inputSchema) ?? {},
    side_effect: asText(raw.side_effect) || 'write',
    readonly: raw.readonly === true,
  }
}

export function normalizeAgent(value: unknown): AgentInfo | null {
  const raw = asRecord(value)
  if (!raw) return null
  const agentType = asText(raw.agent_type ?? raw.type ?? raw.id ?? raw.key).trim()
  if (!agentType) return null
  const enabledRaw = raw.enabled
  return {
    ...raw,
    agent_type: agentType,
    name: asText(raw.name ?? raw.label ?? raw.title) || agentType,
    description: asText(raw.description ?? raw.summary ?? raw.purpose),
    ...(raw.role !== undefined ? { role: asText(raw.role) } : {}),
    capabilities: asStringList(raw.capabilities ?? raw.skills),
    tools: asStringList(raw.tools ?? raw.tool_names),
    inputs: asStringList(raw.inputs),
    outputs: asStringList(raw.outputs),
    ...(enabledRaw !== undefined ? { enabled: enabledRaw === true || enabledRaw === 'true' } : {}),
  }
}

export function normalizeWorkflowRun(value: unknown): WorkflowRun | null {
  const raw = asRecord(value)
  if (!raw) return null
  const id = asText(raw.id ?? raw.run_id ?? raw.workflow_id).trim()
  if (!id) return null
  return {
    ...raw,
    id,
    project_id: asText(raw.project_id) || undefined,
    workflow_type: asText(raw.workflow_type ?? raw.type) || 'standard',
    status: asText(raw.status, 'queued') || 'queued',
    goal: asText(raw.goal ?? raw.objective ?? raw.title),
    result: asRecord(raw.result),
    tool_calls: normalizeToolCalls(
      raw.tool_calls ?? asRecord(raw.result)?.tool_calls ?? asRecord(raw.output)?.tool_calls,
    ),
    stats: asRecord(raw.stats),
    budget: asRecord(raw.budget),
    created_at: isoOrNull(raw.created_at),
    completed_at: isoOrNull(raw.completed_at),
  }
}

export function normalizeWorkflowTask(value: unknown): WorkflowTask | null {
  const raw = asRecord(value)
  if (!raw) return null
  const seq = Math.max(0, Math.round(asNumber(raw.seq, 0)))
  const taskKey = asText(raw.task_key ?? raw.key ?? raw.name).trim()
  const agentType = asText(raw.agent_type ?? raw.agent).trim()
  const id = asText(raw.id ?? raw.task_id).trim() || `${taskKey || agentType || 'task'}-${seq}`
  if (!taskKey && !agentType && !asText(raw.id)) return null
  return {
    ...raw,
    id,
    seq,
    task_key: taskKey,
    agent_type: agentType,
    status: asText(raw.status, 'pending') || 'pending',
    error: asText(raw.error),
    tool_calls: normalizeToolCalls(raw.tool_calls ?? asRecord(raw.output)?.tool_calls),
    attempts: Math.max(0, Math.round(asNumber(raw.attempts, 0))),
    started_at: isoOrNull(raw.started_at),
    completed_at: isoOrNull(raw.completed_at),
  }
}

export function normalizeWorkflowApproval(value: unknown): WorkflowApproval | null {
  const raw = asRecord(value)
  if (!raw) return null
  const id = asText(raw.id ?? raw.approval_id).trim()
  if (!id) return null
  return {
    ...raw,
    id,
    approval_type: asText(raw.approval_type ?? raw.type) || 'approval',
    status: asText(raw.status, 'pending') || 'pending',
    review_note: asText(raw.review_note ?? raw.note),
    run_id: asText(raw.run_id) || undefined,
    task_id: raw.task_id ? asText(raw.task_id) : null,
    submitted_by: asText(raw.submitted_by),
    reviewed_by: asText(raw.reviewed_by),
    submitted_at: isoOrNull(raw.submitted_at),
    reviewed_at: isoOrNull(raw.reviewed_at),
  }
}

function mapList<T>(value: unknown, keys: string[], normalizer: (item: unknown) => T | null): T[] {
  return listFrom(value, keys)
    .map(normalizer)
    .filter((item): item is T => item !== null)
}

export async function listAgents(signal?: AbortSignal): Promise<AgentInfo[]> {
  const data = await request<unknown>('/agents', undefined, signal)
  return mapList(data, ['agents', 'items'], normalizeAgent)
}

/**
 * 读取某个 Agent 的工具目录（只读）。两种调用方式都支持：
 * `getAgentTools(agentType, signal)` 或 `getAgentTools(ref, agentType, signal)`。
 * 传 ref 时走项目上下文，只读成员也能查看；不传 ref 时后端要求全局写权限。
 */
export function getAgentTools(agentType: string, signal?: AbortSignal): Promise<AgentTool[]>
export function getAgentTools(
  ref: string | null | undefined,
  agentType: string,
  signal?: AbortSignal,
): Promise<AgentTool[]>
export async function getAgentTools(
  refOrAgentType: string | null | undefined,
  agentTypeOrSignal?: string | AbortSignal,
  signal?: AbortSignal,
): Promise<AgentTool[]> {
  const twoArgs = typeof agentTypeOrSignal === 'string'
  const ref = twoArgs ? refOrAgentType : undefined
  const agentType = twoArgs ? agentTypeOrSignal : refOrAgentType
  const effectiveSignal = twoArgs ? signal : (agentTypeOrSignal as AbortSignal | undefined)
  const query = ref ? `?ref=${encodeURIComponent(ref)}` : ''
  const data = await request<unknown>(
    `/agents/${encodeURIComponent(String(agentType || ''))}/tools${query}`,
    undefined,
    effectiveSignal,
  )
  return mapList(data, ['tools', 'items'], normalizeAgentTool)
}

export async function listWorkflows(ref: string, signal?: AbortSignal): Promise<WorkflowRun[]> {
  const data = await request<unknown>(projectPath(ref, '/workflows'), undefined, signal)
  return mapList(data, ['workflows', 'runs', 'items'], normalizeWorkflowRun)
}

export async function createWorkflow(
  ref: string,
  input: WorkflowCreateInput,
): Promise<WorkflowRun> {
  const data = await request<unknown>(
    projectPath(ref, '/workflows'),
    jsonBody({
      workflow_type: input.workflow_type || 'standard',
      goal: input.goal,
      input: input.input || {},
      budget: input.budget || {},
    }),
  )
  const run = normalizeWorkflowRun(unwrap(data, ['workflow', 'run']))
  if (!run) {
    throw new ApiError(500, null, '工作流已创建，但服务端未返回可识别的运行记录。')
  }
  return run
}

export async function getWorkflow(
  ref: string,
  runId: string,
  signal?: AbortSignal,
): Promise<WorkflowRun> {
  const data = await request<unknown>(
    projectPath(ref, `/workflows/${encodeURIComponent(runId)}`),
    undefined,
    signal,
  )
  const run = normalizeWorkflowRun(unwrap(data, ['workflow', 'run']))
  if (!run) {
    throw new ApiError(500, null, '服务端返回的工作流信息无法识别，请刷新后重试。')
  }
  return run
}

async function workflowAction(
  ref: string,
  runId: string,
  action: 'cancel' | 'resume',
): Promise<WorkflowRun> {
  const data = await request<unknown>(
    projectPath(ref, `/workflows/${encodeURIComponent(runId)}/${action}`),
    { method: 'POST' },
  )
  const run = normalizeWorkflowRun(unwrap(data, ['workflow', 'run']))
  // 204 / `{ok: true}` 响应没有运行详情，回读一次最新状态。
  return run || getWorkflow(ref, runId)
}

export const cancelWorkflow = (ref: string, runId: string) =>
  workflowAction(ref, runId, 'cancel')

export const resumeWorkflow = (ref: string, runId: string) =>
  workflowAction(ref, runId, 'resume')

export async function listWorkflowTasks(
  ref: string,
  runId: string,
  signal?: AbortSignal,
): Promise<WorkflowTask[]> {
  const data = await request<unknown>(
    projectPath(ref, `/workflows/${encodeURIComponent(runId)}/tasks`),
    undefined,
    signal,
  )
  return mapList(data, ['tasks', 'items'], normalizeWorkflowTask)
}

export async function listWorkflowApprovals(
  ref: string,
  signal?: AbortSignal,
): Promise<WorkflowApproval[]> {
  const data = await request<unknown>(projectPath(ref, '/workflow-approvals'), undefined, signal)
  return mapList(data, ['approvals', 'items'], normalizeWorkflowApproval)
}

export async function reviewWorkflowApproval(
  ref: string,
  approvalId: string,
  input: WorkflowApprovalReviewInput,
): Promise<WorkflowApproval> {
  const note = (input.note || '').trim()
  const data = await request<unknown>(
    projectPath(
      ref,
      `/workflow-approvals/${encodeURIComponent(approvalId)}/review`,
    ),
    jsonBody({ decision: input.decision, ...(note ? { note } : {}) }),
  )
  const approval = normalizeWorkflowApproval(unwrap(data, ['approval']))
  if (approval) return approval
  // 早期后端只返回 `{ok: true}`：回读审批列表拿到真实状态。
  const approvals = await listWorkflowApprovals(ref)
  const found = approvals.find((item) => item.id === approvalId)
  if (found) return found
  throw new ApiError(500, null, '审批已提交，但服务端未返回可识别的审批记录。')
}

// ---------------------------------------------------------------- cross-project approval inbox

/**
 * 跨项目工作流待审闸门（平铺数组）。与报价/商务标待办分开暴露，互不改变响应形状。
 * 旧后端没有该接口（404）或 SPA 兜底返回 HTML（JSON 解析失败）时按「无待办」降级。
 */
export async function getPendingWorkflowApprovals(
  signal?: AbortSignal,
): Promise<PendingWorkflowApproval[]> {
  let data: unknown
  try {
    data = await request<unknown>('/approvals/workflows/pending', undefined, signal)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return []
    if (error instanceof SyntaxError) return []
    throw error
  }
  return mapList(data, ['items'], normalizePendingWorkflowApproval)
}
