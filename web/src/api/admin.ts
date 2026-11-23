import { request } from '../lib/http'
import type { AdminHealth } from '../lib/types'

/** 超级管理员运行状态；公开 `/health` 刻意保持最小化，这里读取完整口径。 */
export const getAdminHealth = (signal?: AbortSignal) =>
  request<AdminHealth>('/admin/health', undefined, signal)
