import { describe, expect, it } from 'vitest'
import { buildPluginPayload, type PluginFormValues } from './AdminPluginModal.payload'
import type { Plugin } from '../../lib/types'

const values: PluginFormValues = {
  name: '  private-mcp  ',
  transport: 'http',
  enabled: true,
  tool_allowlist: [],
  mem_limit_mb: 512,
  cpu_seconds: 10,
  scope: 'owner',
  owner_id: 'user-7',
  allow_private_net: true,
  command: '',
  args: [],
  env: {},
  url: ' http://127.0.0.1:8000/mcp ',
}

const ownerPlugin: Plugin = {
  id: 'p1',
  name: 'private-mcp',
  transport: 'http',
  command: '',
  args: [],
  env: {},
  url: 'http://127.0.0.1:8000/mcp',
  enabled: true,
  tool_allowlist: [],
  mem_limit_mb: 512,
  cpu_seconds: 10,
  scope: 'owner',
  owner_id: 'user-7',
  allow_private_net: true,
}

describe('buildPluginPayload', () => {
  it('新建时提交 scope / owner_id / allow_private_net', () => {
    expect(buildPluginPayload(values)).toEqual({
      name: 'private-mcp',
      transport: 'http',
      enabled: true,
      tool_allowlist: [],
      mem_limit_mb: 512,
      cpu_seconds: 10,
      scope: 'owner',
      owner_id: 'user-7',
      allow_private_net: true,
      url: 'http://127.0.0.1:8000/mcp',
    })
  })

  it('编辑 owner 插件且作用域未变时不发送 scope，保留归属', () => {
    const payload = buildPluginPayload(values, ownerPlugin)
    expect(payload.scope).toBeUndefined()
    expect(payload.owner_id).toBeUndefined()
    expect(payload.allow_private_net).toBeUndefined()
  })

  it('归属用户变更时只发送 owner_id', () => {
    const payload = buildPluginPayload({ ...values, owner_id: 'user-8' }, ownerPlugin)
    expect(payload.scope).toBeUndefined()
    expect(payload.owner_id).toBe('user-8')
  })

  it('从 owner 切到 global 时显式清空 owner_id', () => {
    const payload = buildPluginPayload({ ...values, scope: 'global' }, ownerPlugin)
    expect(payload.scope).toBe('global')
    expect(payload.owner_id).toBeNull()
  })

  it('stdio 插件只提交命令相关字段，不提交私网开关', () => {
    const payload = buildPluginPayload(
      {
        ...values,
        transport: 'stdio',
        command: ' npx ',
        args: ['-y'],
        env: { TOKEN: 'x' },
        allow_private_net: true,
      },
      { ...ownerPlugin, transport: 'stdio', command: 'npx' },
    )
    expect(payload.command).toBe('npx')
    expect(payload.args).toEqual(['-y'])
    expect(payload.env).toEqual({ TOKEN: 'x' })
    expect(payload.url).toBeUndefined()
    expect(payload.allow_private_net).toBeUndefined()
  })
})
