/**
 * 统一的后台任务观察器：SSE 实时流 + 轮询兜底 + 断线自动降级。
 *
 * 这是全站处理后台任务的唯一传输层。各业务页面只提供「拿到进度后做什么」
 * 的回调，不再各自实现 EventSource / setInterval / 重连 / 错误计数。
 *
 * - `stream: true`：先连 SSE（`GET /jobs/{id}/events`），收到速率限制/断线时
 *   自动切到轮询（调用方把 streamFallback 置 true 后重新调用本函数即可）。
 * - `stream: false`：只轮询。用于进度粒度粗、不需要 token 级预览的任务
 *   （配图批次、知识库重建等）。
 * - 终态（done/failed）只回调一次，由内部 doneHandled 守卫，SSE 与轮询同时
 *   到达也不会重复触发副作用。
 */
import { getJob } from '../api/generation'
import { messageOf } from '../lib/http'
import type { Job } from '../lib/types'

export interface JobEventPayload extends Partial<Job> {
  id?: string
  heading?: string
  index?: number
  total?: number
  target_words?: number
  progress?: number
  done?: number
  words?: number
  source?: string
  failed?: boolean
  text?: string
  error?: string
}

export interface WatchJobOptions {
  /** true=SSE + 轮询兜底；false=仅轮询。默认 false。 */
  stream?: boolean
  /** 轮询间隔毫秒，默认 1600。 */
  pollInterval?: number
  /** 连续读取失败达到该次数后放弃，默认 3。 */
  maxPollErrors?: number
  /** 每次进度更新（SSE 事件或轮询结果）都会调用。 */
  onUpdate?: (data: Job | JobEventPayload) => void
  /** SSE 领域事件（chapter_start / delta / chapter 等），供页面做专属展示。 */
  onEvent?: (kind: string, data: JobEventPayload) => void
  /** 任务成功完成，保证只回调一次。 */
  onDone?: (job: Job) => void
  /** 任务失败，保证只回调一次。 */
  onFailed?: (job: Job) => void
  /** SSE 断线时调用；页面据此切到轮询模式。 */
  onSseFallback?: () => void
  /** 轮询连续失败达到上限时调用（`message` 已格式化）。 */
  onPollError?: (message: string, errors: number) => void
  /** 轮询成功的每次 tick 也可拿到完整 Job（用于页面刷新关联数据）。 */
  onPollTick?: (job: Job) => void
}

const SSE_EVENTS = [
  'snapshot', 'queued', 'status',
  'chapter_start', 'delta', 'chapter',
  'done', 'failed',
] as const

export function jobEventUrl(jobId: string): string {
  return `/jobs/${encodeURIComponent(jobId)}/events`
}

function isTerminal(status: string | undefined): boolean {
  return status === 'done' || status === 'failed'
}

function asJob(data: JobEventPayload, fallbackId: string): Job {
  return { ...(data as Job), id: data.id || fallbackId }
}

export function watchJob(jobId: string, options: WatchJobOptions): () => void {
  const {
    stream = false,
    pollInterval = 1600,
    maxPollErrors = 3,
    onUpdate,
    onEvent,
    onDone,
    onFailed,
    onSseFallback,
    onPollError,
    onPollTick,
  } = options

  let cancelled = false
  let handled = false
  let source: EventSource | null = null
  let timer: number | undefined
  let errors = 0

  const settle = (data: JobEventPayload) => {
    if (handled) return
    handled = true
    source?.close()
    if (timer) window.clearInterval(timer)
    const job = asJob(data, jobId)
    if (job.status === 'failed') {
      onFailed?.(job)
    } else {
      onDone?.(job)
    }
  }

  const poll = async () => {
    try {
      const next = await getJob(jobId)
      if (cancelled || handled) return
      errors = 0
      onUpdate?.(next)
      onPollTick?.(next)
      if (isTerminal(next.status)) {
        settle(next)
      }
    } catch (error) {
      if (cancelled || handled) return
      errors += 1
      if (errors >= maxPollErrors) {
        if (timer) window.clearInterval(timer)
        const message = messageOf(error)
        onPollError?.(message, errors)
      }
    }
  }

  const startPolling = () => {
    if (timer) window.clearInterval(timer)
    timer = window.setInterval(() => void poll(), pollInterval)
    void poll()
  }

  if (stream && typeof EventSource !== 'undefined') {
    source = new EventSource(jobEventUrl(jobId))
    const listen = (kind: string, handler: (data: JobEventPayload) => void) => {
      source?.addEventListener(kind, (event) => {
        try {
          handler(JSON.parse((event as MessageEvent).data || '{}') as JobEventPayload)
        } catch {
          // 畸形帧不应让整条流失效。
        }
      })
    }
    for (const kind of SSE_EVENTS) {
      listen(kind, (data) => {
        if (cancelled || handled) return
        if (kind !== 'done' && kind !== 'failed') onEvent?.(kind, data)
        if (kind === 'snapshot' || kind === 'queued' || kind === 'status') {
          onUpdate?.(data)
        }
        if (kind === 'done' || kind === 'failed') settle(data)
      })
    }
    source.onerror = () => {
      if (cancelled || handled) return
      source?.close()
      onSseFallback?.()
    }
  } else {
    startPolling()
  }

  return () => {
    cancelled = true
    source?.close()
    if (timer) window.clearInterval(timer)
  }
}
