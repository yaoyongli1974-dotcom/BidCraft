import { ApiError, asArray, jsonBody, notifyUnauthorized, readError, request } from '../lib/http'
import type { Plugin, PluginCatalogEntry, PluginInput, PluginTestResult } from '../lib/types'
import { filenameFromDisposition } from './generation'
import { asBool, asNumber, asStringList, asText, normalizePluginArgs, normalizePluginCatalogEntry, normalizePluginEnv, normalizePluginLimit, normalizePluginTools, pluginItems } from './shared'

// ---------------------------------------------------------------- plugins domain
export const PLUGIN_MEM_LIMIT = { min: 64, max: 4096, default: 512 } as const

export const PLUGIN_CPU_SECONDS = { min: 1, max: 120, default: 10 } as const

/** 数字输入可能带字符串或 null：非法值回退缺省，其余四舍五入后 clamp 进区间。 */

export function normalizePlugin(value: unknown): Plugin {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const command = asText(raw.command)
  const url = asText(raw.url)
  const declared = asText(raw.transport).toLowerCase()
  // 旧后端或手写数据可能漏掉 transport：有 url 无 command 时按 HTTP 处理更安全。
  const transport: Plugin['transport'] =
    declared === 'http' || declared === 'stdio'
      ? declared
      : url && !command
        ? 'http'
        : 'stdio'
  return {
    id: asText(raw.id),
    name: asText(raw.name),
    transport,
    command,
    args: normalizePluginArgs(raw.args),
    env: normalizePluginEnv(raw.env),
    url,
    enabled: raw.enabled === undefined || raw.enabled === null ? true : asBool(raw.enabled),
    tool_allowlist: asStringList(raw.tool_allowlist),
    mem_limit_mb: normalizePluginLimit(
      raw.mem_limit_mb,
      PLUGIN_MEM_LIMIT.default,
      PLUGIN_MEM_LIMIT.min,
      PLUGIN_MEM_LIMIT.max,
    ),
    cpu_seconds: normalizePluginLimit(
      raw.cpu_seconds,
      PLUGIN_CPU_SECONDS.default,
      PLUGIN_CPU_SECONDS.min,
      PLUGIN_CPU_SECONDS.max,
    ),
    last_test_at: typeof raw.last_test_at === 'string' ? raw.last_test_at : null,
    last_test_ok:
      typeof raw.last_test_ok === 'boolean'
        ? raw.last_test_ok
        : raw.last_test_ok === undefined || raw.last_test_ok === null
          ? null
          : asBool(raw.last_test_ok),
    last_test_message: asText(raw.last_test_message),
    fail_streak: asNumber(raw.fail_streak, 0),
    auto_disabled: asBool(raw.auto_disabled),
    tools: normalizePluginTools(raw.tools),
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  }
}

export async function getPlugins(): Promise<Plugin[]> {
  let data: unknown
  try {
    data = await request<unknown>('/admin/plugins')
  } catch (err) {
    // 旧后端的 SPA 兜底会把未知路径返回 index.html（200 + HTML），
    // JSON 解析失败时按「接口未就绪」处理，而不是把英文解析错误抛给界面。
    if (err instanceof ApiError) throw err
    throw new ApiError(404, '插件接口未就绪', '当前后端没有插件接口，请升级后重试。')
  }
  return pluginItems(data)
    .map(normalizePlugin)
    .filter((item) => item.id && item.name)
}

export async function createPlugin(input: PluginInput): Promise<Plugin> {
  const data = await request<unknown>('/admin/plugins', jsonBody(input))
  return normalizePlugin(data)
}

export async function updatePlugin(
  id: string,
  patch: Partial<PluginInput>,
): Promise<Plugin> {
  const data = await request<unknown>(`/admin/plugins/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
  return normalizePlugin(data)
}

export const deletePlugin = (id: string) =>
  request<{ deleted?: boolean; id?: string }>(`/admin/plugins/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

/** 真实握手一次：失败也返回 200（ok=false + 中文 message），不抛异常。 */

export async function testPlugin(id: string): Promise<PluginTestResult> {
  const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const data = await request<unknown>(`/admin/plugins/${encodeURIComponent(id)}/test`, {
    method: 'POST',
  })
  const elapsed = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt
  const raw = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  const latency = raw.latency_ms ?? raw.elapsed_ms ?? raw.latency
  return {
    ok: raw.ok === undefined || raw.ok === null ? false : asBool(raw.ok),
    message: asText(raw.message) || asText(raw.detail),
    tools: normalizePluginTools(raw.tools),
    // 契约未强制返回耗时：后端没有时用一次往返时间兜底，界面始终能给出反馈。
    latency_ms:
      typeof latency === 'number' && Number.isFinite(latency)
        ? Math.max(0, latency)
        : Math.max(0, Math.round(elapsed)),
  }
}

/** 同名插件已存在时的处理方式：覆盖升级或直接跳过。 */

export type PluginImportConflict = 'replace' | 'skip'

export interface PluginImportResult {
  plugin: Plugin
  /** 后端 201 表示新建，200 表示升级；用于给出「已创建/已升级」提示。 */
  created: boolean
}

/** 下载单个插件包：后端返回 zip 附件，文件名以 Content-Disposition 为准。 */

export async function exportPluginPackage(
  id: string,
  name?: string,
): Promise<{ blob: Blob; filename: string }> {
  const path = `/admin/plugins/${encodeURIComponent(id)}/package`
  let response: Response
  try {
    response = await fetch(path, { credentials: 'same-origin', headers: { Accept: '*/*' } })
  } catch {
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(path)
    throw await readError(response)
  }
  const blob = await response.blob()
  // 插件名可能带路径分隔符或引号：兜底文件名只保留安全字符。
  const safeName = (name || '').trim().replace(/[\\/:*?"<>|]+/g, '_')
  const filename =
    filenameFromDisposition(response.headers.get('Content-Disposition')) ||
    `${safeName || 'plugin'}.bidplugin.zip`
  return { blob, filename }
}

/**
 * 导入 .bidplugin 插件包（multipart 字段 `file`）。用 fetch 而不是 uploadForm，
 * 因为需要读取 201/200 区分「新建/升级」，文件本身受后端 5MB 限制、无需上传进度。
 */

export async function importPluginPackage(
  file: File,
  onConflict: PluginImportConflict = 'replace',
): Promise<PluginImportResult> {
  const path = `/admin/plugins/import?on_conflict=${encodeURIComponent(onConflict)}`
  const form = new FormData()
  form.append('file', file, file.name)
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      body: form,
    })
  } catch {
    throw new ApiError(
      0,
      null,
      '无法连接本地服务。请确认 BidCraft 后端已启动，且当前页面由它托管。',
    )
  }
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(path)
    // readError 会取出后端的中文 detail（包非法 400 / 同名 skip 409）。
    throw await readError(response)
  }
  const created = response.status === 201
  const data = (await response.json()) as unknown
  return { plugin: normalizePlugin(data), created }
}

export async function getPluginCatalog(): Promise<PluginCatalogEntry[]> {
  let data: unknown
  try {
    data = await request<unknown>('/admin/plugins/catalog')
  } catch (err) {
    if (err instanceof ApiError) throw err
    throw new ApiError(404, '预设目录未就绪', '当前后端没有插件预设目录，请升级后重试。')
  }
  const list =
    ['catalog', 'presets', 'items']
      .map((key) => asArray<unknown>(data, key))
      .find((items) => items.length > 0) ?? []
  return list
    .map(normalizePluginCatalogEntry)
    .filter((item): item is PluginCatalogEntry => item !== null)
}
