import { useCallback, useMemo } from 'react'
import {
  confirmChapterPlan,
  generateChapterPlan,
  getChapterPlan,
} from '../api/generation'
import { useQuery } from '../lib/hooks'
import {
  isConfirmedPlan,
  isPendingConfirmPlan,
  type ChapterPlanMode,
} from '../lib/chapterPlan'
import { messageOf } from '../lib/http'
import type { ChapterPlan } from '../lib/types'

export interface ChapterPlanEntry {
  node_id: string
  plan: ChapterPlan | null
  mode: ChapterPlanMode
}

/** 逐章读取规划；后端对未生成的章节返回 plan: null，这里不补造规划。 */
export async function loadChapterPlans(
  ref: string,
  nodeIds: string[],
): Promise<ChapterPlanEntry[]> {
  const results = await Promise.allSettled(
    nodeIds.map(async (nodeId) => {
      const response = await getChapterPlan(ref, nodeId)
      return { node_id: nodeId, plan: response.plan, mode: response.mode }
    }),
  )
  const entries = results
    .filter((result): result is PromiseFulfilledResult<ChapterPlanEntry> => result.status === 'fulfilled')
    .map((result) => result.value)
  if (entries.length === 0 && results.length > 0) {
    const firstFailure = results.find((result) => result.status === 'rejected')
    throw firstFailure?.status === 'rejected' ? firstFailure.reason : new Error('章节规划查询全部失败')
  }
  return entries
}

/**
 * 大纲页/正文页共用：一次拿到所有章节的规划，供规划面板与正文生成门禁使用。
 * 缓存 key 只由 ref + 章节 ID 组成，编辑标题不会触发重复请求。
 */
export function useChapterPlans(ref: string, nodeIds: string[]) {
  const key = `chapter-plans:${ref}:${nodeIds.join(',')}`
  const query = useQuery<ChapterPlanEntry[]>(
    key,
    () => loadChapterPlans(ref, nodeIds),
    { enabled: nodeIds.length > 0 },
  )
  const entries = useMemo(() => query.data ?? [], [query.data])
  const plans = useMemo(() => {
    const map = new Map<string, ChapterPlan | null>()
    for (const entry of entries) map.set(entry.node_id, entry.plan)
    return map
  }, [entries])
  const mode = entries[0]?.mode ?? 'auto'

  const setQueryData = query.setData
  const setPlan = useCallback(
    (nodeId: string, plan: ChapterPlan) => {
      setQueryData((current) => {
        const list = current ?? []
        const fallbackMode = list[0]?.mode ?? 'auto'
        if (!list.some((entry) => entry.node_id === nodeId)) {
          return [...list, { node_id: nodeId, plan, mode: fallbackMode }]
        }
        return list.map((entry) =>
          entry.node_id === nodeId ? { ...entry, plan } : entry,
        )
      })
    },
    [setQueryData],
  )

  return {
    entries,
    plans,
    mode,
    loading: query.loading,
    error: query.error,
    refresh: query.refresh,
    setPlan,
  }
}

// ------------------------------------------------------------ 批量操作（复用单章 API）

interface ChapterLike {
  id?: string
}

type PlanLookup = ReadonlyArray<Pick<ChapterPlanEntry, 'node_id' | 'plan'>>

function planMap(entries: PlanLookup): Map<string, ChapterPlan | null> {
  return new Map(entries.map((entry) => [entry.node_id, entry.plan]))
}

/**
 * 批量生成的候选：真实章节 ID 且当前没有可用规划。
 * 已确认 / 已用于生成的规划一律跳过，重新生成必须走单章的显式「重新生成」。
 */
export function missingPlanNodeIds(
  chapters: ReadonlyArray<ChapterLike>,
  entries: PlanLookup,
): string[] {
  const plans = planMap(entries)
  return chapters
    .filter((chapter) => {
      if (!chapter.id) return false
      const plan = plans.get(chapter.id)
      return !plan && !isConfirmedPlan(plan)
    })
    .map((chapter) => chapter.id as string)
}

/** 批量确认只处理草稿 / 已更新 / 待复核，其他状态（含已确认）不动。 */
export function pendingConfirmNodeIds(
  chapters: ReadonlyArray<ChapterLike>,
  entries: PlanLookup,
): string[] {
  const plans = planMap(entries)
  return chapters
    .filter((chapter) => Boolean(chapter.id) && isPendingConfirmPlan(plans.get(chapter.id ?? '')))
    .map((chapter) => chapter.id as string)
}

export interface PlanBatchOutcome {
  total: number
  succeeded: string[]
  failed: Array<{ nodeId: string; message: string }>
}

export type PlanBatchProgress = (done: number, total: number, nodeId: string) => void

/** 顺序调用单章 API：单章失败只记录结果，不中断剩余章节。 */
async function runPlanBatch(
  nodeIds: string[],
  task: (nodeId: string) => Promise<ChapterPlan>,
  onPlanChange: (nodeId: string, plan: ChapterPlan) => void,
  onProgress?: PlanBatchProgress,
  isSuccess: (plan: ChapterPlan) => boolean = () => true,
): Promise<PlanBatchOutcome> {
  const outcome: PlanBatchOutcome = { total: nodeIds.length, succeeded: [], failed: [] }
  for (let index = 0; index < nodeIds.length; index += 1) {
    const nodeId = nodeIds[index]
    onProgress?.(index, nodeIds.length, nodeId)
    try {
      const plan = await task(nodeId)
      onPlanChange(nodeId, plan)
      if (isSuccess(plan)) {
        outcome.succeeded.push(nodeId)
      } else {
        outcome.failed.push({ nodeId, message: '确认后规划尚未确认' })
      }
    } catch (error) {
      outcome.failed.push({ nodeId, message: messageOf(error) })
    }
    onProgress?.(index + 1, nodeIds.length, nodeId)
  }
  return outcome
}

export function batchGeneratePlans(
  ref: string,
  nodeIds: string[],
  onPlanChange: (nodeId: string, plan: ChapterPlan) => void,
  onProgress?: PlanBatchProgress,
): Promise<PlanBatchOutcome> {
  return runPlanBatch(
    nodeIds,
    (nodeId) => generateChapterPlan(ref, nodeId),
    onPlanChange,
    onProgress,
  )
}

export function batchConfirmPlans(
  ref: string,
  nodeIds: string[],
  note: string,
  onPlanChange: (nodeId: string, plan: ChapterPlan) => void,
  onProgress?: PlanBatchProgress,
): Promise<PlanBatchOutcome> {
  const trimmed = note.trim()
  return runPlanBatch(
    nodeIds,
    (nodeId) =>
      trimmed
        ? confirmChapterPlan(ref, nodeId, { note: trimmed })
        : confirmChapterPlan(ref, nodeId),
    onPlanChange,
    onProgress,
    isConfirmedPlan,
  )
}
