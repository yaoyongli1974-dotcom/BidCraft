import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Plug } from 'lucide-react'
import type { Plugin, PluginCatalogEntry, PluginInput } from '../../lib/types'
import { createPlugin, messageOf, updatePlugin } from '../../lib/api'
import { className } from '../../lib/format'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'
import { Badge } from '../../components/Badge'
import { AdminSegment } from './AdminShared'

export type PluginModalState = { mode: 'create' } | { mode: 'edit'; plugin: Plugin }

interface AdminPluginModalProps {
  open: boolean
  state: PluginModalState
  catalog: PluginCatalogEntry[]
  onClose: () => void
  onSaved: (plugin: Plugin, created: boolean) => void
}

const TRANSPORT_ITEMS = [
  { id: 'stdio', label: '本机命令 · stdio' },
  { id: 'http', label: 'HTTP 端点' },
]

const FIELD_MAX = { name: 60, command: 500, url: 500 } as const

/** 参数框接受两种写法：JSON 数组，或一行一个参数。空行忽略。 */
function parseArgsText(text: string): { args: string[]; error: string | null } {
  const trimmed = text.trim()
  if (!trimmed) return { args: [], error: null }
  if (trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed) as unknown
      if (!Array.isArray(parsed)) return { args: [], error: '参数需要是 JSON 数组。' }
      if (parsed.some((item) => typeof item !== 'string')) {
        return { args: [], error: 'JSON 数组里的每一项都应是字符串。' }
      }
      return {
        args: (parsed as string[]).map((item) => item.trim()).filter(Boolean),
        error: null,
      }
    } catch {
      return { args: [], error: '参数不是合法的 JSON：请检查引号与逗号。' }
    }
  }
  return {
    args: trimmed
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean),
    error: null,
  }
}

/** 环境变量必须是 JSON 对象；数字/布尔会转成字符串，避免保存时报类型错。 */
function parseEnvText(text: string): { env: Record<string, string>; error: string | null } {
  const trimmed = text.trim()
  if (!trimmed) return { env: {}, error: null }
  try {
    const parsed = JSON.parse(trimmed) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { env: {}, error: '环境变量需要是 JSON 对象，例如 {"TOKEN": "…"}。' }
    }
    const env: Record<string, string> = {}
    for (const [rawKey, value] of Object.entries(parsed as Record<string, unknown>)) {
      const key = rawKey.trim()
      if (!key) continue
      if (typeof value === 'string') env[key] = value
      else if (typeof value === 'number' || typeof value === 'boolean') env[key] = String(value)
      else return { env: {}, error: `环境变量 ${key} 的值需要是字符串。` }
    }
    return { env, error: null }
  } catch {
    return { env: {}, error: '环境变量不是合法的 JSON：请检查花括号与引号。' }
  }
}

function splitAllowlist(text: string): string[] {
  return text
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function uniqueList(items: string[]): string[] {
  return Array.from(new Set(items))
}

/** 新建 / 编辑一个 MCP 插件。transport 决定显示 command+args+env 还是 url。 */
export function AdminPluginModal({
  open,
  state,
  catalog,
  onClose,
  onSaved,
}: AdminPluginModalProps) {
  const [presetName, setPresetName] = useState('')
  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'stdio' | 'http'>('stdio')
  const [command, setCommand] = useState('')
  const [argsText, setArgsText] = useState('')
  const [envText, setEnvText] = useState('')
  const [url, setUrl] = useState('')
  const [allowlistText, setAllowlistText] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)
  const [argsDirty, setArgsDirty] = useState(false)
  const [envDirty, setEnvDirty] = useState(false)

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setTouched(false)
    setArgsDirty(false)
    setEnvDirty(false)
    setPresetName('')
    if (state.mode === 'edit' && state.plugin) {
      const plugin = state.plugin
      setName(plugin.name)
      setTransport(plugin.transport === 'http' ? 'http' : 'stdio')
      setCommand(plugin.command)
      setArgsText(plugin.args.length ? JSON.stringify(plugin.args, null, 2) : '')
      setEnvText(
        Object.keys(plugin.env).length ? JSON.stringify(plugin.env, null, 2) : '',
      )
      setUrl(plugin.url)
      setAllowlistText(plugin.tool_allowlist.join(', '))
      setEnabled(plugin.enabled)
    } else {
      setName('')
      setTransport('stdio')
      setCommand('')
      setArgsText('')
      setEnvText('')
      setUrl('')
      setAllowlistText('')
      setEnabled(true)
    }
  }, [open, state])

  const parsedArgs = useMemo(() => parseArgsText(argsText), [argsText])
  const parsedEnv = useMemo(() => parseEnvText(envText), [envText])
  const allowlist = useMemo(() => uniqueList(splitAllowlist(allowlistText)), [allowlistText])

  const nameMissing = !name.trim()
  const commandMissing = transport === 'stdio' && !command.trim()
  const urlMissing = transport === 'http' && !url.trim()
  const urlInvalid =
    transport === 'http' &&
    url.trim() !== '' &&
    !/^https?:\/\//i.test(url.trim())
  const argsInvalid = transport === 'stdio' && Boolean(parsedArgs.error)
  const envInvalid = transport === 'stdio' && Boolean(parsedEnv.error)
  const canSubmit =
    !nameMissing &&
    !commandMissing &&
    !urlMissing &&
    !urlInvalid &&
    !argsInvalid &&
    !envInvalid

  const placeholders = useMemo(
    () =>
      parsedArgs.args.filter(
        (item) => /<[^>\s]+>/.test(item) || /^\/path\/to\//i.test(item),
      ),
    [parsedArgs.args],
  )

  const showArgsError = argsDirty || touched
  const showEnvError = envDirty || touched

  const applyPreset = (preset: PluginCatalogEntry) => {
    // 只有名称为空、或仍是上一个预设自动填的名字时才覆盖，避免抹掉用户自己起的名字。
    setName((current) => {
      const trimmed = current.trim()
      return !trimmed || trimmed === presetName ? preset.name : current
    })
    setPresetName(preset.name)
    setTransport(preset.transport === 'http' ? 'http' : 'stdio')
    setCommand(preset.command)
    setUrl(preset.url)
    setArgsText(preset.args.length ? JSON.stringify(preset.args, null, 2) : '')
    setArgsDirty(false)
    setError(null)
  }

  const submit = async () => {
    setTouched(true)
    if (nameMissing) return
    if (commandMissing) return
    if (urlMissing) return
    if (urlInvalid) {
      setError('端点地址需要以 http:// 或 https:// 开头。')
      return
    }
    if (argsInvalid) {
      setError(parsedArgs.error)
      return
    }
    if (envInvalid) {
      setError(parsedEnv.error)
      return
    }

    const payload: PluginInput = {
      name: name.trim(),
      transport,
      enabled,
      tool_allowlist: allowlist,
    }
    if (transport === 'stdio') {
      payload.command = command.trim()
      payload.args = parsedArgs.args
      payload.env = parsedEnv.env
    } else {
      payload.url = url.trim()
    }

    setSaving(true)
    setError(null)
    try {
      const saved =
        state.mode === 'edit' && state.plugin
          ? await updatePlugin(state.plugin.id, payload)
          : await createPlugin(payload)
      onSaved(saved, state.mode === 'create')
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={saving ? () => undefined : onClose}
      title={state.mode === 'edit' ? '编辑插件' : '新增插件'}
      eyebrow="MCP 客户端"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="plugin-form"
            loading={saving}
            disabled={!canSubmit && touched}
          >
            {state.mode === 'edit' ? '保存修改' : '新增插件'}
          </Button>
        </>
      }
    >
      <form
        id="plugin-form"
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="admin-form-note">
          <Plug size={15} aria-hidden />
          <span>
            插件以本机子进程（stdio）或 HTTP 端点运行，命令与参数都会在本机执行，请只添加你信任的来源。
            保存后可在列表里点「测试」确认握手与工具清单。
          </span>
        </div>

        {state.mode === 'create' && catalog.length > 0 && (
          <div className="form-field admin-form-span">
            <div className="field-label-row">
              <span className="plugin-form-label">从预设开始</span>
              <span className="field-hint">预设只填参数，不安装任何依赖</span>
            </div>
            <div className="plugin-presets" role="group" aria-label="内置插件预设">
              <button
                type="button"
                className={className('plugin-preset', !presetName && 'is-active')}
                aria-pressed={!presetName}
                onClick={() => setPresetName('')}
                disabled={saving}
              >
                <span className="plugin-preset-name">自定义</span>
                <span className="plugin-preset-desc">从空表单开始，自己填写命令或端点。</span>
              </button>
              {catalog.map((preset) => (
                <button
                  key={preset.name}
                  type="button"
                  className={className(
                    'plugin-preset',
                    presetName === preset.name && 'is-active',
                  )}
                  aria-pressed={presetName === preset.name}
                  onClick={() => applyPreset(preset)}
                  disabled={saving}
                >
                  <span className="plugin-preset-name">
                    <span className="mono">{preset.name}</span>
                    <span className="plugin-preset-transport">
                      {preset.transport === 'http' ? 'HTTP' : 'stdio'}
                    </span>
                  </span>
                  <span className="plugin-preset-desc">
                    {preset.description || '内置预设，填入后可继续修改。'}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {presetName && (
          <div className="plugin-preset-note">
            <AlertTriangle size={14} aria-hidden />
            <span>
              {transport === 'stdio' ? (
                <>
                  已填入「{presetName}」预设参数，<strong>仍需本机已安装对应命令</strong>
                  （例如 npx 来自 Node.js、uvx 来自 uv）。
                  {placeholders.length > 0 ? (
                    <>
                      {' '}
                      参数里的占位符{' '}
                      {placeholders.map((item) => (
                        <code key={item}>{item}</code>
                      ))}{' '}
                      请替换成本机真实值。
                    </>
                  ) : (
                    ' 保存前请确认路径与参数符合本机环境。'
                  )}
                </>
              ) : (
                <>已填入「{presetName}」预设参数，仍需确认该端点可访问并完成 MCP 握手。</>
              )}
            </span>
          </div>
        )}

        <div className="admin-form-grid">
          <div className="form-field admin-form-span">
            <label htmlFor="plugin-name">
              插件名称 <span className="required-mark">必填</span>
            </label>
            <input
              id="plugin-name"
              className={className(
                'input',
                'input--mono',
                touched && nameMissing && 'input--error',
              )}
              value={name}
              onChange={(event) => {
                setName(event.target.value)
                if (error) setError(null)
              }}
              placeholder="例如：filesystem"
              maxLength={FIELD_MAX.name}
              autoComplete="off"
              data-autofocus
              disabled={saving}
            />
            <p className="field-hint">
              对话里显示为「名称__工具名」的前缀；名称不能与已有插件重复。
            </p>
            {touched && nameMissing && <span className="field-error">请填写插件名称。</span>}
          </div>

          <div className="form-field admin-form-span">
            <span className="plugin-form-label">运行方式</span>
            <AdminSegment
              label="选择插件的运行方式"
              value={transport}
              onChange={(value) => {
                setTransport(value === 'http' ? 'http' : 'stdio')
                if (error) setError(null)
              }}
              items={TRANSPORT_ITEMS}
            />
            <p className="field-hint">
              stdio 会为每次调用启动一个本机子进程；HTTP 则向固定地址发送 JSON-RPC 请求。
            </p>
          </div>

          {transport === 'stdio' ? (
            <>
              <div className="form-field admin-form-span">
                <label htmlFor="plugin-command">
                  启动命令 <span className="required-mark">必填</span>
                </label>
                <input
                  id="plugin-command"
                  className={className(
                    'input',
                    'input--mono',
                    touched && commandMissing && 'input--error',
                  )}
                  value={command}
                  onChange={(event) => setCommand(event.target.value)}
                  placeholder="npx / uvx / /usr/local/bin/my-mcp-server"
                  maxLength={FIELD_MAX.command}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={saving}
                />
                <p className="field-hint">
                  只写可执行文件名或绝对路径，不要带参数；PATH 与下面的环境变量会传给子进程。
                </p>
                {touched && commandMissing && (
                  <span className="field-error">请填写启动命令。</span>
                )}
              </div>

              <div className="form-field admin-form-span">
                <label htmlFor="plugin-args">
                  参数 <span className="optional-mark">可选</span>
                </label>
                <textarea
                  id="plugin-args"
                  className={className(
                    'textarea',
                    'plugin-json-input',
                    showArgsError && argsInvalid && 'input--error',
                  )}
                  value={argsText}
                  onChange={(event) => {
                    setArgsText(event.target.value)
                    if (error) setError(null)
                  }}
                  onBlur={() => setArgsDirty(true)}
                  placeholder={'[\n  "-y",\n  "@modelcontextprotocol/server-filesystem",\n  "/data/documents"\n]'}
                  spellCheck={false}
                  disabled={saving}
                />
                <p className="field-hint">
                  JSON 字符串数组，也可以一行写一个参数。留空表示不带参数。
                </p>
                {showArgsError && parsedArgs.error && (
                  <span className="field-error">{parsedArgs.error}</span>
                )}
                {!parsedArgs.error && parsedArgs.args.length > 0 && (
                  <p className="plugin-json-preview mono">
                    {parsedArgs.args.length} 个参数 · {parsedArgs.args.join(' ')}
                  </p>
                )}
              </div>

              <div className="form-field admin-form-span">
                <label htmlFor="plugin-env">
                  环境变量 <span className="optional-mark">可选</span>
                </label>
                <textarea
                  id="plugin-env"
                  className={className(
                    'textarea',
                    'plugin-json-input',
                    showEnvError && envInvalid && 'input--error',
                  )}
                  value={envText}
                  onChange={(event) => {
                    setEnvText(event.target.value)
                    if (error) setError(null)
                  }}
                  onBlur={() => setEnvDirty(true)}
                  placeholder={'{\n  "API_TOKEN": "…"\n}'}
                  spellCheck={false}
                  disabled={saving}
                />
                <p className="field-hint">
                  JSON 对象，只传给该插件的子进程；留空表示只继承最小的 PATH。
                </p>
                {showEnvError && parsedEnv.error && (
                  <span className="field-error">{parsedEnv.error}</span>
                )}
                {!parsedEnv.error && Object.keys(parsedEnv.env).length > 0 && (
                  <p className="plugin-json-preview mono">
                    {Object.keys(parsedEnv.env).length} 个变量 ·{' '}
                    {Object.keys(parsedEnv.env).join(', ')}
                  </p>
                )}
              </div>
            </>
          ) : (
            <div className="form-field admin-form-span">
              <label htmlFor="plugin-url">
                端点地址 <span className="required-mark">必填</span>
              </label>
              <input
                id="plugin-url"
                className={className(
                  'input',
                  'input--mono',
                  touched && (urlMissing || urlInvalid) && 'input--error',
                )}
                value={url}
                onChange={(event) => {
                  setUrl(event.target.value)
                  if (error) setError(null)
                }}
                placeholder="https://example.com/mcp"
                maxLength={FIELD_MAX.url}
                autoComplete="off"
                spellCheck={false}
                disabled={saving}
              />
              <p className="field-hint">
                POST JSON-RPC 到该地址；服务不可达或握手失败会在测试结果里显示原因。
              </p>
              {touched && urlMissing && <span className="field-error">请填写端点地址。</span>}
              {touched && urlInvalid && (
                <span className="field-error">需要以 http:// 或 https:// 开头。</span>
              )}
            </div>
          )}

          <div className="form-field admin-form-span">
            <label htmlFor="plugin-allowlist">
              工具白名单 <span className="optional-mark">可选</span>
            </label>
            <input
              id="plugin-allowlist"
              className="input input--mono"
              value={allowlistText}
              onChange={(event) => setAllowlistText(event.target.value)}
              placeholder="read_file, list_directory"
              autoComplete="off"
              spellCheck={false}
              disabled={saving}
            />
            <p className="field-hint">
              逗号分隔的工具名；留空表示允许该插件的全部工具。只影响对话里的暴露范围，不影响测试结果。
            </p>
            {allowlist.length > 0 ? (
              <div className="plugin-allow-preview">
                {allowlist.map((item) => (
                  <span key={item} className="plugin-allow-chip mono">
                    {item}
                  </span>
                ))}
              </div>
            ) : (
              <p className="plugin-allow-empty">
                当前不限制：该插件的所有工具都会以 <span className="mono">名称__工具名</span> 出现。
              </p>
            )}
          </div>

          <label className="admin-check admin-form-span">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
              disabled={saving}
            />
            <span>
              <strong>保存后立即启用</strong>
              <small>
                启用后，该插件的工具会以 <span className="mono">名称__工具名</span> 出现在对话的工具清单里。
              </small>
            </span>
          </label>
        </div>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}

        <div className="plugin-form-foot">
          <Badge tone="neutral" subtle>
            仅存本机
          </Badge>
          <span>
            插件运行在本机；readonly 模式的 bidcraft mcp 会拒绝所有插件工具。
          </span>
        </div>
      </form>
    </Modal>
  )
}
