import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { CalendarClock, ClipboardList, UserCheck } from 'lucide-react'
import type { ChapterAssignment, ChapterAssignmentStatus, Member } from '../lib/types'
import {
  createAssignment,
  listAssignments,
  updateAssignment,
} from '../api/collaboration'
import { getOutline } from '../api/generation'
import { messageOf } from '../lib/http'
import { className, formatDate, type Tone } from '../lib/format'
import { useQuery } from '../lib/hooks'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { EmptyState, ErrorState, Skeleton } from '../components/Feedback'
import { useToast } from '../context/useToast'

const STATUS_OPTIONS: Array<{ value: ChapterAssignmentStatus; label: string }> = [
  { value: 'assigned', label: '已分配' },
  { value: 'in_progress', label: '进行中' },
  { value: 'done', label: '已完成' },
]

function statusMeta(status: string): { label: string; tone: Tone } {
  const found = STATUS_OPTIONS.find((option) => option.value === status)
  if (!found) return { label: status || '未知', tone: 'neutral' }
  return {
    label: found.label,
    tone: status === 'done' ? 'success' : status === 'in_progress' ? 'info' : 'neutral',
  }
}

/**
 * 章节分工：列出责任人、截止日期与状态，支持新建与状态流转。
 * 章节选项来自大纲接口，成员选项由协作页的成员列表提供。
 */
export function AssignmentPanel({
  projectRef,
  members,
  canWrite,
}: {
  projectRef: string
  members: Member[]
  canWrite: boolean
}) {
  const toast = useToast()
  const assignmentsQuery = useQuery(`assignments:${projectRef}`, () => listAssignments(projectRef))
  const outlineQuery = useQuery(`outline:${projectRef}`, () => getOutline(projectRef))
  const chapters = useMemo(() => outlineQuery.data || [], [outlineQuery.data])
  const assignments = assignmentsQuery.data || []

  const [chapterId, setChapterId] = useState('')
  const [assigneeId, setAssigneeId] = useState('')
  const [deadline, setDeadline] = useState('')
  const [note, setNote] = useState('')
  const [creating, setCreating] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const formChapter = chapterId || chapters[0]?.id || ''
  const formAssignee = assigneeId || members[0]?.user_id || ''
  const chapterMap = useMemo(() => {
    const map = new Map<string, string>()
    chapters.forEach((chapter) => map.set(chapter.id, chapter.heading))
    return map
  }, [chapters])
  const memberMap = useMemo(() => {
    const map = new Map<string, string>()
    members.forEach((member) =>
      map.set(member.user_id, member.display_name || member.username || member.user_id),
    )
    return map
  }, [members])

  // 大纲/成员晚于第一次渲染时，让表单默认值跟随可用选项。
  useEffect(() => {
    if (!chapterId && chapters.length) setChapterId(chapters[0].id)
  }, [chapters, chapterId])
  useEffect(() => {
    if (!assigneeId && members.length) setAssigneeId(members[0].user_id)
  }, [members, assigneeId])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canWrite || !formChapter || !formAssignee || creating) return
    setCreating(true)
    setActionError(null)
    try {
      const created = await createAssignment(projectRef, {
        node_id: formChapter,
        assignee_id: formAssignee,
        deadline: deadline || null,
        note,
      })
      assignmentsQuery.setData((current) => [...(current || []), created])
      setNote('')
      setDeadline('')
      toast.success('章节已分配', chapterMap.get(formChapter) || '当前章节')
      assignmentsQuery.refresh()
    } catch (error) {
      const message = messageOf(error)
      setActionError(message)
      toast.error('分配章节失败', message)
    } finally {
      setCreating(false)
    }
  }

  const changeStatus = async (assignment: ChapterAssignment, status: string) => {
    if (!canWrite || busyId || assignment.status === status) return
    const previous = assignment.status
    assignmentsQuery.setData((current) =>
      current?.map((item) => (item.id === assignment.id ? { ...item, status } : item)),
    )
    setBusyId(assignment.id)
    setActionError(null)
    try {
      const updated = await updateAssignment(projectRef, assignment.id, {
        status,
        note: assignment.note || '',
      })
      assignmentsQuery.setData((current) =>
        current?.map((item) => (item.id === assignment.id ? updated : item)),
      )
      toast.success('分工状态已更新', statusMeta(status).label)
    } catch (error) {
      assignmentsQuery.setData((current) =>
        current?.map((item) =>
          item.id === assignment.id ? { ...item, status: previous } : item,
        ),
      )
      const message = messageOf(error)
      setActionError(message)
      toast.error('更新分工状态失败', message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="panel collab-card assignment-card" aria-labelledby="collab-assignments">
      <div className="panel-head">
        <div>
          <div className="eyebrow">章节分工</div>
          <h3 id="collab-assignments">谁负责哪一章</h3>
          <p className="panel-sub">
            把章节分给项目成员并跟踪进度；截止日期与备注只在本机保存。
          </p>
        </div>
        <Badge tone="neutral" subtle>
          <ClipboardList size={11} /> {assignments.length} 项
        </Badge>
      </div>

      {actionError && (
        <p className="collab-inline-error" role="alert">
          {actionError}
        </p>
      )}

      {canWrite && (
        <form className="assignment-form" onSubmit={(event) => void submit(event)}>
          <label className="assignment-field">
            <span>章节</span>
            <select
              className="select select--sm"
              value={formChapter}
              onChange={(event) => setChapterId(event.target.value)}
              disabled={!chapters.length}
              aria-label="分工章节"
            >
              {!chapters.length && <option value="">暂无章节</option>}
              {chapters.map((chapter) => (
                <option key={chapter.id} value={chapter.id}>
                  {chapter.heading}
                </option>
              ))}
            </select>
          </label>
          <label className="assignment-field">
            <span>负责人</span>
            <select
              className="select select--sm"
              value={formAssignee}
              onChange={(event) => setAssigneeId(event.target.value)}
              disabled={!members.length}
              aria-label="负责人"
            >
              {!members.length && <option value="">暂无成员</option>}
              {members.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.display_name || member.username}
                </option>
              ))}
            </select>
          </label>
          <label className="assignment-field">
            <span>截止日期</span>
            <input
              type="date"
              className="input input--sm"
              value={deadline}
              onChange={(event) => setDeadline(event.target.value)}
              aria-label="截止日期"
            />
          </label>
          <label className="assignment-field assignment-field--note">
            <span>备注</span>
            <input
              className="input input--sm"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="可选，例如交付要求"
              aria-label="分工备注"
            />
          </label>
          <Button
            type="submit"
            variant="secondary"
            size="sm"
            icon={<UserCheck size={14} />}
            loading={creating}
            disabled={!formChapter || !formAssignee}
          >
            分配章节
          </Button>
        </form>
      )}

      {assignmentsQuery.error && !assignments.length ? (
        <div className="collab-card-body">
          <ErrorState
            message={assignmentsQuery.error}
            onRetry={assignmentsQuery.refresh}
            compact
          />
        </div>
      ) : assignmentsQuery.loading && !assignments.length ? (
        <div className="collab-loading" aria-busy="true">
          {Array.from({ length: 2 }).map((_, index) => (
            <div key={index} className="collab-skeleton-row">
              <Skeleton width={34} height={34} radius={7} />
              <div>
                <Skeleton width="46%" height={12} />
                <Skeleton width="64%" height={10} />
              </div>
              <Skeleton width={72} height={22} radius={999} />
            </div>
          ))}
        </div>
      ) : assignments.length === 0 ? (
        <EmptyState
          icon={ClipboardList}
          compact
          title="还没有章节分工"
          description={
            canWrite
              ? '选择一个章节和负责人，明确谁在什么时候交付。'
              : '项目负责人还没有给章节分配责任人。'
          }
        />
      ) : (
        <ul className="assignment-list">
          {assignments.map((assignment) => {
            const meta = statusMeta(assignment.status)
            const heading =
              chapterMap.get(assignment.node_id) || assignment.node_id || '未命名章节'
            const busy = busyId === assignment.id
            return (
              <li key={assignment.id} className="assignment-row">
                <span className="assignment-avatar" aria-hidden>
                  {(memberMap.get(assignment.assignee_id) || '员').trim().slice(0, 1)}
                </span>
                <div className="assignment-copy">
                  <span className="assignment-chapter">{heading}</span>
                  <span className="assignment-person">
                    {memberMap.get(assignment.assignee_id) || assignment.assignee_id || '未指定'}
                    {assignment.note ? ` · ${assignment.note}` : ''}
                  </span>
                </div>
                <span className="assignment-deadline">
                  <CalendarClock size={12} aria-hidden />
                  {assignment.deadline
                    ? formatDate(assignment.deadline, false)
                    : '未设截止'}
                </span>
                <div className="assignment-status">
                  {canWrite ? (
                    <select
                      className="select select--sm assignment-status-select"
                      value={assignment.status}
                      aria-label={`更新 ${heading} 的分工状态`}
                      disabled={Boolean(busyId)}
                      onChange={(event) =>
                        void changeStatus(assignment, event.target.value)
                      }
                    >
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Badge tone={meta.tone} subtle>
                      {meta.label}
                    </Badge>
                  )}
                  <span className={className('assignment-hint', busy && 'is-busy')}>
                    {busy ? '保存中…' : ''}
                  </span>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
