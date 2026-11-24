import type { Plugin, PluginInput } from '../../lib/types'

export interface PluginFormValues {
  name: string
  transport: 'stdio' | 'http'
  enabled: boolean
  tool_allowlist: string[]
  mem_limit_mb: number
  cpu_seconds: number
  scope: 'global' | 'owner'
  owner_id: string
  allow_private_net: boolean
  command: string
  args: string[]
  env: Record<string, string>
  url: string
}

/**
 * 由表单状态生成 create/patch 载荷。编辑时只发送变更过的 scope 相关字段：
 * 后端 PATCH 会保留未提供的字段，因此未改动的 owner 归属不会被抹掉或降级成 global。
 */
export function buildPluginPayload(values: PluginFormValues, original?: Plugin | null): PluginInput {
  const payload: PluginInput = {
    name: values.name.trim(),
    transport: values.transport,
    enabled: values.enabled,
    tool_allowlist: values.tool_allowlist,
    mem_limit_mb: values.mem_limit_mb,
    cpu_seconds: values.cpu_seconds,
  }
  const originalScope = original?.scope === 'owner' ? 'owner' : 'global'
  const originalOwnerId = original?.owner_id || ''
  const scopeChanged = !original || values.scope !== originalScope
  const ownerChanged = !original || values.owner_id !== originalOwnerId
  if (scopeChanged) payload.scope = values.scope
  if (values.scope === 'owner') {
    if (ownerChanged) payload.owner_id = values.owner_id.trim()
  } else if (originalScope === 'owner' && scopeChanged) {
    // 切回全局时显式清空归属，避免后端保留旧的 owner_id。
    payload.owner_id = null
  }
  if (values.transport === 'http') {
    const privateNetChanged =
      !original || Boolean(values.allow_private_net) !== Boolean(original.allow_private_net)
    if (privateNetChanged) payload.allow_private_net = values.allow_private_net
  }
  if (values.transport === 'stdio') {
    payload.command = values.command.trim()
    payload.args = values.args
    payload.env = values.env
  } else {
    payload.url = values.url.trim()
  }
  return payload
}
