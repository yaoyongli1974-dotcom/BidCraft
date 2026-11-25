import { useCallback, useMemo } from 'react'
import { getChapterPlan } from '../api/generation'
import { useQuery } from '../lib/hooks'
import type { ChapterPlanMode } from '../lib/chapterPlan'
import type { ChapterPlan, ChapterPlanResponse } from '../lib/types'

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
  return Promise.all(
    nodeIds.map(async (nodeId) => {
      const response: ChapterPlanResponse = await getChapterPlan(ref, nodeId)
      return { node_id: nodeId, plan: response.plan, mode: response.mode }
    }),
  )
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
