import { ApiError, asArray, jsonBody, request } from '../lib/http'
import type {
  ChapterAssignment,
  ChapterAssignmentInput,
  ChapterAssignmentUpdateInput,
  RestoreRevisionInput,
  RevisionDiffRow,
  SectionDiff,
  SectionLock,
  SectionRevision,
  SectionRevisionDetail,
} from '../lib/types'
import { asNumber, asText, isoOrNull } from './shared'

// ---------------------------------------------------------------- collaboration versions

const projectPath = (ref: string, suffix: string) =>
  `/projects/${encodeURIComponent(ref)}${suffix}`

const chapterPath = (ref: string, nodeId: string, suffix: string) =>
  projectPath(ref, `/chapters/${encodeURIComponent(nodeId)}${suffix}`)

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function listFrom(value: unknown, keys: string[]): unknown[] {
  if (Array.isArray(value)) return value
  for (const key of keys) {
    const list = asArray<unknown>(value, key)
    if (list.length) return list
  }
  return []
}

function unwrap(value: unknown, keys: string[]): unknown {
  const record = asRecord(value)
  if (!record) return value
  for (const key of keys) {
    if (record[key] !== undefined) return record[key]
  }
  return value
}

function readText(record: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    if (typeof record[key] === 'string') return record[key] as string
  }
  return undefined
}

function readNumber(record: Record<string, unknown>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
      return Number(value)
    }
  }
  return undefined
}

// ---------------------------------------------------------------- lock

export function normalizeSectionLock(value: unknown): SectionLock | null {
  const raw = asRecord(value)
  if (!raw) return null
  const nodeId = asText(raw.node_id ?? raw.nodeId).trim()
  const userId = asText(raw.user_id ?? raw.userId).trim()
  const token = asText(raw.lock_token ?? raw.lockToken).trim()
  if (!nodeId && !userId && !token) return null
  return {
    ...raw,
    node_id: nodeId,
    user_id: userId,
    lock_token: token,
    expires_at: isoOrNull(raw.expires_at ?? raw.expiresAt),
    display_name: asText(raw.display_name ?? raw.displayName) || undefined,
    username: asText(raw.username) || undefined,
  }
}

/** 没有锁时后端可能返回 404，也可能返回 `{lock: null}`；两者都归一化为 null。 */
export async function getChapterLock(
  ref: string,
  nodeId: string,
  signal?: AbortSignal,
): Promise<SectionLock | null> {
  let data: unknown
  try {
    data = await request<unknown>(chapterPath(ref, nodeId, '/lock'), undefined, signal)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
  return normalizeSectionLock(unwrap(data, ['lock']))
}

export async function acquireChapterLock(ref: string, nodeId: string): Promise<SectionLock> {
  const data = await request<unknown>(chapterPath(ref, nodeId, '/lock'), { method: 'POST' })
  const lock = normalizeSectionLock(unwrap(data, ['lock']))
  if (!lock) {
    throw new ApiError(500, null, '编辑锁已请求，但服务端未返回锁信息，请刷新后重试。')
  }
  return lock
}

export async function renewChapterLock(
  ref: string,
  nodeId: string,
  lockToken: string,
): Promise<SectionLock> {
  const data = await request<unknown>(chapterPath(ref, nodeId, '/lock'), {
    method: 'PUT',
    body: JSON.stringify({ lock_token: lockToken }),
  })
  const lock = normalizeSectionLock(unwrap(data, ['lock']))
  if (lock) return lock
  const fresh = await getChapterLock(ref, nodeId)
  if (fresh) return fresh
  throw new ApiError(500, null, '续期后未读取到编辑锁，请刷新后重试。')
}

export async function releaseChapterLock(ref: string, nodeId: string): Promise<void> {
  try {
    await request<void>(chapterPath(ref, nodeId, '/lock'), { method: 'DELETE' })
  } catch (error) {
    // 锁已过期/已被释放时，释放动作本身已经达成目的。
    if (error instanceof ApiError && error.status === 404) return
    throw error
  }
}

// ---------------------------------------------------------------- revisions

export function normalizeSectionRevision(value: unknown): SectionRevision | null {
  const raw = asRecord(value)
  if (!raw) return null
  const id = asText(raw.id ?? raw.revision_id).trim()
  const nodeId = asText(raw.node_id).trim()
  const revision = Math.round(asNumber(raw.revision ?? raw.version, 0))
  if (!id && !revision) return null
  return {
    ...raw,
    id,
    node_id: nodeId,
    revision,
    body_hash: asText(raw.body_hash) || undefined,
    changed_by: asText(raw.changed_by ?? raw.actor ?? raw.created_by) || undefined,
    change_type: asText(raw.change_type, 'edit') || 'edit',
    change_note: asText(raw.change_note ?? raw.note),
    created_at: isoOrNull(raw.created_at),
  }
}

export function normalizeSectionRevisionDetail(
  value: unknown,
): SectionRevisionDetail | null {
  const base = normalizeSectionRevision(value)
  const raw = asRecord(value)
  if (!base || !raw) return null
  return { ...base, body: asText(raw.body ?? raw.content) }
}

export async function listChapterRevisions(
  ref: string,
  nodeId: string,
  signal?: AbortSignal,
): Promise<SectionRevision[]> {
  const data = await request<unknown>(
    chapterPath(ref, nodeId, '/revisions'),
    undefined,
    signal,
  )
  return listFrom(data, ['revisions', 'items'])
    .map(normalizeSectionRevision)
    .filter((item): item is SectionRevision => item !== null)
}

export async function getChapterRevision(
  ref: string,
  nodeId: string,
  revision: number,
  signal?: AbortSignal,
): Promise<SectionRevisionDetail> {
  const data = await request<unknown>(
    chapterPath(ref, nodeId, `/revisions/${encodeURIComponent(String(revision))}`),
    undefined,
    signal,
  )
  const detail = normalizeSectionRevisionDetail(unwrap(data, ['revision']))
  if (!detail) {
    throw new ApiError(500, null, '服务端返回的修订内容无法识别，请刷新后重试。')
  }
  return detail
}

export async function restoreChapterRevision(
  ref: string,
  nodeId: string,
  input: RestoreRevisionInput,
): Promise<SectionRevisionDetail | null> {
  const note = (input.note || '').trim()
  const data = await request<unknown>(
    chapterPath(ref, nodeId, '/restore'),
    jsonBody({ revision: input.revision, note }),
  )
  return normalizeSectionRevisionDetail(unwrap(data, ['revision']))
}

// ---------------------------------------------------------------- diff

function parseUnifiedDiff(text: string): RevisionDiffRow[] {
  const rows: RevisionDiffRow[] = []
  for (const line of text.split(/\r?\n/)) {
    if (
      line.startsWith('@@') ||
      line.startsWith('diff ') ||
      line.startsWith('index ') ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ')
    ) {
      continue
    }
    if (line.startsWith('+')) rows.push({ kind: 'add', right: line.slice(1) })
    else if (line.startsWith('-')) rows.push({ kind: 'remove', left: line.slice(1) })
    else if (line.startsWith(' ')) rows.push({ kind: 'equal', left: line.slice(1), right: line.slice(1) })
    else if (line.trim()) rows.push({ kind: 'equal', left: line, right: line })
  }
  return rows
}

function normalizeDiffRow(value: unknown): RevisionDiffRow | null {
  if (typeof value === 'string') {
    if (value.startsWith('+')) return { kind: 'add', right: value.slice(1) }
    if (value.startsWith('-')) return { kind: 'remove', left: value.slice(1) }
    return {
      kind: 'equal',
      left: value.startsWith(' ') ? value.slice(1) : value,
      right: value.startsWith(' ') ? value.slice(1) : value,
    }
  }
  const raw = asRecord(value)
  if (!raw) return null
  const kindRaw = asText(raw.kind ?? raw.op ?? raw.type ?? raw.change ?? raw.status)
    .trim()
    .toLowerCase()
  const left = readText(raw, ['left', 'old', 'before', 'removed', 'from'])
  const right = readText(raw, ['right', 'new', 'after', 'added', 'to'])
  const text = readText(raw, ['text', 'content', 'value', 'line'])
  if (kindRaw === 'add' || kindRaw === 'insert' || kindRaw === 'inserted' || kindRaw === '+') {
    return { kind: 'add', right: right ?? text ?? '' }
  }
  if (
    kindRaw === 'remove' ||
    kindRaw === 'del' ||
    kindRaw === 'delete' ||
    kindRaw === 'deleted' ||
    kindRaw === '-'
  ) {
    return { kind: 'remove', left: left ?? text ?? '' }
  }
  if (kindRaw === 'change' || kindRaw === 'replace' || kindRaw === 'modified' || kindRaw === '~') {
    return { kind: 'change', left: left ?? text ?? '', right: right ?? text ?? '' }
  }
  return {
    kind: 'equal',
    left: left ?? text ?? '',
    right: right ?? text ?? '',
  }
}

/** LCS line diff used only when the server returns bodies but no structured diff. */
function lineDiff(before: string, after: string): RevisionDiffRow[] {
  const left = before.split(/\r?\n/)
  const right = after.split(/\r?\n/)
  if (left.length * right.length > 360_000) {
    const rows: RevisionDiffRow[] = []
    const max = Math.max(left.length, right.length)
    for (let index = 0; index < max; index += 1) {
      const a = left[index]
      const b = right[index]
      if (a === b && a !== undefined) rows.push({ kind: 'equal', left: a, right: a })
      else rows.push({ kind: 'change', left: a ?? '', right: b ?? '' })
    }
    return rows
  }
  const table: number[][] = Array.from({ length: left.length + 1 }, () =>
    new Array<number>(right.length + 1).fill(0),
  )
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        left[i] === right[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }
  const rows: RevisionDiffRow[] = []
  let i = 0
  let j = 0
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      rows.push({ kind: 'equal', left: left[i], right: right[j] })
      i += 1
      j += 1
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      rows.push({ kind: 'remove', left: left[i] })
      i += 1
    } else {
      rows.push({ kind: 'add', right: right[j] })
      j += 1
    }
  }
  while (i < left.length) {
    rows.push({ kind: 'remove', left: left[i] })
    i += 1
  }
  while (j < right.length) {
    rows.push({ kind: 'add', right: right[j] })
    j += 1
  }
  return rows
}

export function normalizeSectionDiff(value: unknown): SectionDiff {
  const raw = asRecord(value)
  const diffText = typeof value === 'string' ? value : raw ? readText(raw, ['diff', 'unified', 'patch', 'text']) : undefined
  const fromBody = raw ? readText(raw, ['from_body', 'old_body', 'before', 'old']) : undefined
  const toBody = raw ? readText(raw, ['to_body', 'new_body', 'after', 'new']) : undefined

  let rows: RevisionDiffRow[] = []
  const structured = raw
    ? (raw.rows ?? raw.lines ?? raw.changes ?? raw.hunks ?? undefined)
    : undefined
  if (Array.isArray(structured)) {
    for (const item of structured) {
      const hunk = asRecord(item)
      if (hunk && Array.isArray(hunk.lines)) {
        for (const line of hunk.lines) {
          const row = normalizeDiffRow(line)
          if (row) rows.push(row)
        }
      } else {
        const row = normalizeDiffRow(item)
        if (row) rows.push(row)
      }
    }
  }
  if (!rows.length && typeof diffText === 'string' && diffText.trim()) {
    rows = parseUnifiedDiff(diffText)
  }
  if (!rows.length && fromBody !== undefined && toBody !== undefined) {
    rows = lineDiff(fromBody, toBody)
  }

  const added =
    raw && readNumber(raw, ['added', 'additions', 'insertions']) !== undefined
      ? Math.max(0, Math.round(readNumber(raw, ['added', 'additions', 'insertions']) as number))
      : rows.filter((row) => row.kind === 'add').length
  const removed =
    raw && readNumber(raw, ['removed', 'deletions']) !== undefined
      ? Math.max(0, Math.round(readNumber(raw, ['removed', 'deletions']) as number))
      : rows.filter((row) => row.kind === 'remove').length

  return {
    from: raw ? readNumber(raw, ['from', 'from_revision', 'old_revision']) ?? null : null,
    to: raw ? readNumber(raw, ['to', 'to_revision', 'new_revision']) ?? null : null,
    from_body: fromBody,
    to_body: toBody,
    rows,
    added,
    removed,
    ...(rows.length ? {} : diffText ? { text: diffText } : {}),
  }
}

export async function getChapterDiff(
  ref: string,
  nodeId: string,
  from: number,
  to: number,
  signal?: AbortSignal,
): Promise<SectionDiff> {
  const data = await request<unknown>(
    `${chapterPath(ref, nodeId, '/diff')}?from=${encodeURIComponent(String(from))}&to=${encodeURIComponent(String(to))}`,
    undefined,
    signal,
  )
  return normalizeSectionDiff(unwrap(data, ['diff']))
}

// ---------------------------------------------------------------- assignments

export function normalizeChapterAssignment(value: unknown): ChapterAssignment | null {
  const raw = asRecord(value)
  if (!raw) return null
  const id = asText(raw.id ?? raw.assignment_id).trim()
  if (!id) return null
  return {
    ...raw,
    id,
    node_id: asText(raw.node_id).trim(),
    assignee_id: asText(raw.assignee_id ?? raw.user_id).trim(),
    status: asText(raw.status, 'assigned') || 'assigned',
    deadline: isoOrNull(raw.deadline),
    note: asText(raw.note),
    assigned_by: asText(raw.assigned_by) || undefined,
    created_at: isoOrNull(raw.created_at),
    updated_at: isoOrNull(raw.updated_at),
  }
}

export async function listAssignments(
  ref: string,
  signal?: AbortSignal,
): Promise<ChapterAssignment[]> {
  const data = await request<unknown>(projectPath(ref, '/assignments'), undefined, signal)
  return listFrom(data, ['assignments', 'items'])
    .map(normalizeChapterAssignment)
    .filter((item): item is ChapterAssignment => item !== null)
}

export async function createAssignment(
  ref: string,
  input: ChapterAssignmentInput,
): Promise<ChapterAssignment> {
  const note = (input.note || '').trim()
  const data = await request<unknown>(
    projectPath(ref, '/assignments'),
    jsonBody({
      node_id: input.node_id,
      assignee_id: input.assignee_id,
      ...(input.deadline ? { deadline: input.deadline } : {}),
      ...(note ? { note } : {}),
    }),
  )
  const assignment = normalizeChapterAssignment(unwrap(data, ['assignment']))
  if (!assignment) {
    throw new ApiError(500, null, '分工已提交，但服务端未返回可识别的分工记录。')
  }
  return assignment
}

export async function updateAssignment(
  ref: string,
  assignmentId: string,
  input: ChapterAssignmentUpdateInput,
): Promise<ChapterAssignment> {
  const note = (input.note || '').trim()
  const data = await request<unknown>(
    projectPath(ref, `/assignments/${encodeURIComponent(assignmentId)}`),
    {
      method: 'PUT',
      body: JSON.stringify({ status: input.status, ...(note ? { note } : {}) }),
    },
  )
  const assignment = normalizeChapterAssignment(unwrap(data, ['assignment']))
  if (assignment) return assignment
  const assignments = await listAssignments(ref)
  const found = assignments.find((item) => item.id === assignmentId)
  if (found) return found
  throw new ApiError(500, null, '分工状态已提交，但服务端未返回可识别的分工记录。')
}
