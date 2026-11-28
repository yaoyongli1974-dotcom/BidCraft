import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getJobMock = vi.fn()
vi.mock('../api/generation', () => ({ getJob: (...args: unknown[]) => getJobMock(...args) }))

import { watchJob, type JobEventPayload } from './watchJob'
import type { Job } from '../lib/types'

function job(status: string, extra: Partial<Job> = {}): Job {
  return { id: 'j1', status, progress: 0, payload: {}, ...extra } as Job
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  listeners = new Map<string, Array<(e: { data: string }) => void>>()
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }
  addEventListener(kind: string, handler: (e: { data: string }) => void) {
    const list = this.listeners.get(kind) || []
    list.push(handler)
    this.listeners.set(kind, list)
  }
  emit(kind: string, data: JobEventPayload) {
    for (const h of this.listeners.get(kind) || []) h({ data: JSON.stringify(data) })
  }
  close() { this.closed = true }
}

describe('watchJob', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    getJobMock.mockReset()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('poll-only 拿到终态只回调一次，并转发每次进度', async () => {
    getJobMock.mockResolvedValue(job('done', { result: { written: 3 } }))
    const onUpdate = vi.fn(), onDone = vi.fn()
    const stop = watchJob('j1', { onUpdate, onDone })
    await vi.waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ id: 'j1' }))
    // 后续 tick 不应重复触发终态
    await vi.advanceTimersByTimeAsync(3200)
    expect(onDone).toHaveBeenCalledTimes(1)
    stop()
  })

  it('poll-only 的 failed 终态触发 onFailed', async () => {
    getJobMock.mockResolvedValue(job('failed', { error: '模型不可用' }))
    const onFailed = vi.fn()
    const stop = watchJob('j1', { onFailed })
    await vi.waitFor(() => expect(onFailed).toHaveBeenCalledWith(expect.objectContaining({ error: '模型不可用' })))
    stop()
  })

  it('连续读取失败达到上限后上报并停止', async () => {
    getJobMock.mockRejectedValue(new Error('网络断了'))
    const onPollError = vi.fn()
    const stop = watchJob('j1', { onPollError, maxPollErrors: 3, pollInterval: 100 })
    await vi.advanceTimersByTimeAsync(250)
    expect(onPollError).toHaveBeenCalledTimes(1)
    expect(onPollError.mock.calls[0][0]).toContain('网络断了')
    stop()
  })

  it('stream 模式用事件驱动进度，done 与 failed 只结算一次', () => {
    const onEvent = vi.fn(), onUpdate = vi.fn(), onDone = vi.fn(), onFailed = vi.fn()
    const stop = watchJob('j1', { stream: true, onEvent, onUpdate, onDone, onFailed })
    const source = FakeEventSource.instances[0]
    expect(source.url).toBe('/jobs/j1/events')

    source.emit('chapter_start', { id: 'j1', heading: '第一章' })
    expect(onEvent).toHaveBeenCalledWith('chapter_start', expect.objectContaining({ heading: '第一章' }))
    source.emit('status', { id: 'j1', status: 'running', progress: 20 })
    expect(onUpdate).toHaveBeenCalled()

    source.emit('done', { id: 'j1', status: 'done', result: { written: 1 } })
    source.emit('failed', { id: 'j1', status: 'failed' })
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(onFailed).not.toHaveBeenCalled()
    stop()
  })

  it('SSE 断线关闭连接并通知页面切到轮询；cleanup 也会关闭', () => {
    const onSseFallback = vi.fn()
    const stop = watchJob('j1', { stream: true, onSseFallback })
    const source = FakeEventSource.instances[0]
    source.onerror?.()
    expect(onSseFallback).toHaveBeenCalledTimes(1)
    expect(source.closed).toBe(true)
    stop()
  })

  it('cleanup 后轮询不再触发回调', async () => {
    getJobMock.mockResolvedValue(job('running'))
    const onUpdate = vi.fn()
    const stop = watchJob('j1', { onUpdate, pollInterval: 50 })
    await vi.waitFor(() => expect(onUpdate).toHaveBeenCalled())
    stop()
    const calls = onUpdate.mock.calls.length
    await vi.advanceTimersByTimeAsync(300)
    expect(onUpdate.mock.calls.length).toBe(calls)
  })

  // 后端契约：bidcraft/jobs.py TERMINAL_STATUSES = {done, failed, cancelled}。
  // 这里只 mock getJob 的返回值；不声称后端已正确产生这些状态。
  it('poll-only 收到 cancelled 终态后停止轮询并暴露完整终态快照', async () => {
    const snapshot = job('cancelled', { message: '用户取消', error: 'cancelled by user' })
    getJobMock.mockResolvedValue(snapshot)
    const onUpdate = vi.fn()
    const onTerminal = vi.fn()
    const onDone = vi.fn()
    const onFailed = vi.fn()
    const stop = watchJob('j1', {
      onUpdate, onTerminal, onDone, onFailed, pollInterval: 100,
    })

    await vi.advanceTimersByTimeAsync(0)
    expect(getJobMock).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith(snapshot)
    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenCalledWith(snapshot)
    // 取消不是成功完成：不能触发 onDone；用 onFailed 让页面停止等待。
    expect(onDone).not.toHaveBeenCalled()
    expect(onFailed).toHaveBeenCalledWith(snapshot)
    expect(stop.terminalSnapshot?.()).toEqual(snapshot)

    await vi.advanceTimersByTimeAsync(600)
    expect(getJobMock).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenCalledTimes(1)
    stop()
  })

  it.each(['done', 'failed'])('poll-only 的 %s 终态只结算一次并保留快照', async (status) => {
    const snapshot = job(status, { result: { written: 3 }, error: status === 'failed' ? '模型不可用' : '' })
    getJobMock.mockResolvedValue(snapshot)
    const onTerminal = vi.fn()
    const onDone = vi.fn()
    const onFailed = vi.fn()
    const stop = watchJob('j1', { onTerminal, onDone, onFailed, pollInterval: 100 })

    await vi.advanceTimersByTimeAsync(0)
    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenCalledWith(snapshot)
    if (status === 'done') {
      expect(onDone).toHaveBeenCalledWith(snapshot)
      expect(onFailed).not.toHaveBeenCalled()
    } else {
      expect(onFailed).toHaveBeenCalledWith(snapshot)
      expect(onDone).not.toHaveBeenCalled()
    }
    expect(stop.terminalSnapshot?.()).toEqual(snapshot)

    await vi.advanceTimersByTimeAsync(600)
    expect(getJobMock).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenCalledTimes(1)
    stop()
  })

  it('stream 模式收到 cancelled 事件后关闭连接且不启动轮询', async () => {
    const onTerminal = vi.fn()
    const onDone = vi.fn()
    const onFailed = vi.fn()
    const stop = watchJob('j1', { stream: true, onTerminal, onDone, onFailed })
    const source = FakeEventSource.instances[0]

    source.emit('cancelled', { id: 'j1', status: 'cancelled', message: '已取消' })
    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenCalledWith(expect.objectContaining({ status: 'cancelled' }))
    expect(onDone).not.toHaveBeenCalled()
    expect(onFailed).toHaveBeenCalledTimes(1)
    expect(source.closed).toBe(true)
    expect(stop.terminalSnapshot?.()?.status).toBe('cancelled')

    await vi.advanceTimersByTimeAsync(400)
    expect(getJobMock).not.toHaveBeenCalled()
    stop()
  })
})
