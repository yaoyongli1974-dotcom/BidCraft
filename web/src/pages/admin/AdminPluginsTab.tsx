import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X,
  XCircle,
} from 'lucide-react'
import type { Plugin, PluginCatalogEntry, PluginTestResult } from '../../lib/types'
import { downloadBlob } from '../../api/generation'
import { deletePlugin, exportPluginPackage, getPluginCatalog, getPlugins, importPluginPackage, testPlugin, updatePlugin } from '../../api/plugins'
import { ApiError, messageOf } from '../../lib/http'
import { className, formatLatency, formatRelative } from '../../lib/format'
import { useConfirm } from '../../context/useConfirm'
import { useToast } from '../../context/useToast'
import { Badge } from '../../components/Badge'
import { Button, IconButton } from '../../components/Button'
import { EmptyState, ErrorState } from '../../components/Feedback'
import {
  AdminActionError,
  AdminSectionLabel,
  AdminSegment,
  AdminSkeletonRows,
  LastTestStatus,
} from './AdminShared'
import { AdminPluginModal, type PluginModalState } from './AdminPluginModal'

type PluginsFilter = 'all' | 'enabled' | 'disabled'

function endpointOf(plugin: Plugin): string {
  if (plugin.transport === 'http') return plugin.url
  return [plugin.command, ...plugin.args].filter(Boolean).join(' ')
}

function isMissingRoute(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

/**
 * 工具数没有独立字段：优先用本次测试结果，其次用后端 payload 的 `tools`，
 * 最后从 last_test_message（「连接成功，发现 N 个工具」）里读取上一次测试的数量。
 */
function toolCountOf(plugin: Plugin, result?: PluginTestResult): number | null {
  if (result) return result.ok ? result.tools.length : null
  if (plugin.tools && plugin.tools.length > 0) return plugin.tools.length
  const matched = /发现\s*(\d+)\s*个工具/.exec(plugin.last_test_message || '')
  return matched ? Number(matched[1]) : null
}

/**
 * 插件市场页签。列表与预设目录一起读取；旧后端返回 404 时降级成一条说明，
 * 而不是把整个控制台显示成错误。
 */
export function AdminPluginsTab() {
  const toast = useToast()
  const confirm = useConfirm()

  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [catalog, setCatalog] = useState<PluginCatalogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [unsupported, setUnsupported] = useState(false)
  const [filter, setFilter] = useState<PluginsFilter>('all')
  const [modal, setModal] = useState<PluginModalState | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, PluginTestResult>>({})
  const [actionError, setActionError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [exportingId, setExportingId] = useState<string | null>(null)
  const importInputRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async (silent: boolean) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    const [pluginsResult, catalogResult] = await Promise.allSettled([
      getPlugins(),
      getPluginCatalog(),
    ])
    setLoading(false)
    setRefreshing(false)

    if (pluginsResult.status === 'fulfilled') {
      setPlugins(pluginsResult.value)
      setError(null)
      setUnsupported(false)
    } else if (isMissingRoute(pluginsResult.reason)) {
      setPlugins([])
      setError(null)
      setUnsupported(true)
    } else {
      setError(messageOf(pluginsResult.reason))
    }

    // 预设目录缺失只影响「从预设开始」，不阻断手动填写。
    if (catalogResult.status === 'fulfilled') setCatalog(catalogResult.value)
    else if (isMissingRoute(catalogResult.reason)) setCatalog([])
  }, [])

  useEffect(() => {
    void load(false)
  }, [load])

  const refresh = useCallback(() => {
    void load(!loading)
  }, [load, loading])

  const enabledCount = plugins.filter((plugin) => plugin.enabled).length
  const rows = useMemo(() => {
    if (filter === 'enabled') return plugins.filter((plugin) => plugin.enabled)
    if (filter === 'disabled') return plugins.filter((plugin) => !plugin.enabled)
    return plugins
  }, [plugins, filter])

  const runTest = async (plugin: Plugin) => {
    if (testingId) return
    setTestingId(plugin.id)
    setActionError(null)
    setTestResults((current) => {
      const next = { ...current }
      delete next[plugin.id]
      return next
    })
    try {
      const result = await testPlugin(plugin.id)
      setTestResults((current) => ({ ...current, [plugin.id]: result }))
      if (result.ok) {
        const latency =
          result.latency_ms !== undefined ? ` · ${formatLatency(result.latency_ms)}` : ''
        toast.success('插件握手成功', `${plugin.name} · ${result.tools.length} 个工具${latency}`)
      } else {
        toast.warning('插件测试未通过', result.message || '服务没有返回具体原因')
      }
      // 后端会写入 last_test_*，重新读取让列表与测试结果保持一致。
      void load(true)
    } catch (err) {
      const message = messageOf(err)
      setTestResults((current) => ({
        ...current,
        [plugin.id]: { ok: false, message, tools: [] },
      }))
      setActionError(message)
      toast.error('测试请求失败', message)
    } finally {
      setTestingId(null)
    }
  }

  const toggleEnabled = async (plugin: Plugin, enabled: boolean) => {
    if (togglingId) return
    setTogglingId(plugin.id)
    setActionError(null)
    setPlugins((current) =>
      current.map((item) => (item.id === plugin.id ? { ...item, enabled } : item)),
    )
    try {
      await updatePlugin(plugin.id, { enabled })
      toast.success(
        enabled ? '插件已启用' : '插件已停用',
        enabled
          ? `${plugin.name} 的工具会出现在对话里`
          : `${plugin.name} 暂时不参与工具聚合`,
      )
      void load(true)
    } catch (err) {
      setPlugins((current) =>
        current.map((item) =>
          item.id === plugin.id ? { ...item, enabled: plugin.enabled } : item,
        ),
      )
      const message = messageOf(err)
      setActionError(message)
      toast.error(enabled ? '启用插件失败' : '停用插件失败', message)
    } finally {
      setTogglingId(null)
    }
  }

  const remove = async (plugin: Plugin) => {
    if (deletingId) return
    const accepted = await confirm({
      title: `删除插件「${plugin.name}」？`,
      description:
        '删除后该插件的工具会立即从对话中消失；本机安装的命令或依赖不会被卸载。',
      note: '删除后不可撤销。如果只是暂时不用，可以改用左侧开关停用。',
      confirmLabel: '删除插件',
      tone: 'danger',
    })
    if (!accepted) return
    setDeletingId(plugin.id)
    setActionError(null)
    try {
      await deletePlugin(plugin.id)
      setPlugins((current) => current.filter((item) => item.id !== plugin.id))
      setTestResults((current) => {
        const next = { ...current }
        delete next[plugin.id]
        return next
      })
      toast.success('插件已删除', plugin.name)
      void load(true)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('删除插件失败', message)
    } finally {
      setDeletingId(null)
    }
  }

  const onSaved = (plugin: Plugin, created: boolean) => {
    setModal(null)
    // 参数可能改了，旧的工具清单不再可信。
    setTestResults((current) => {
      const next = { ...current }
      delete next[plugin.id]
      return next
    })
    void load(true)
    toast.success(created ? '插件已新增' : '插件已保存', plugin.name)
  }

  const dismissResult = (id: string) => {
    setTestResults((current) => {
      const next = { ...current }
      delete next[id]
      return next
    })
  }

  const importPackage = async (file: File) => {
    if (importing) return
    setImporting(true)
    setActionError(null)
    try {
      // 同名默认按 replace 覆盖升级；skip 语义由后端以 409 返回。
      const { plugin, created } = await importPluginPackage(file)
      toast.success(
        created ? '插件已创建' : '插件已升级',
        `${plugin.name} · 来自 ${file.name}`,
      )
      // 导入后配置可能变化，旧的工具数与测试结果不再可信。
      setTestResults({})
      void load(true)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('导入插件包失败', message)
    } finally {
      setImporting(false)
    }
  }

  const onImportSelected = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    // 清空选择，保证同一个文件失败后可以再次选中。
    event.target.value = ''
    if (!file || importing) return
    if (!/\.zip$/i.test(file.name)) {
      const message = '请选择 .zip 格式的插件包（.bidplugin.zip）。'
      setActionError(message)
      toast.error('导入插件包失败', message)
      return
    }
    void importPackage(file)
  }

  const exportPackage = async (plugin: Plugin) => {
    if (exportingId) return
    setExportingId(plugin.id)
    setActionError(null)
    try {
      const { blob, filename } = await exportPluginPackage(plugin.id, plugin.name)
      downloadBlob(blob, filename)
      toast.success('插件包已导出', `${plugin.name} · ${filename}`)
    } catch (err) {
      const message = messageOf(err)
      setActionError(message)
      toast.error('导出插件包失败', message)
    } finally {
      setExportingId(null)
    }
  }

  return (
    <div className="admin-plugins">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="扩展能力"
          title="插件市场"
          description="接入在本机运行的 MCP 服务，把文件读写、网页抓取、长期记忆等工具交给模型按需调用。列表只登记配置，不会自动安装任何依赖。"
          aside={
            unsupported ? undefined : (
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={() => setModal({ mode: 'create' })}
              >
                添加插件
              </Button>
            )
          }
        />
      </div>

      {actionError && (
        <AdminActionError message={actionError} onClose={() => setActionError(null)} />
      )}

      {unsupported ? (
        <div className="plugin-unsupported">
          <div className="plugin-unsupported-icon" aria-hidden>
            <AlertTriangle size={22} strokeWidth={1.6} />
          </div>
          <div className="plugin-unsupported-body">
            <div className="eyebrow">接口未就绪</div>
            <h3>当前后端还不支持插件市场</h3>
            <p>
              页面请求了 <span className="mono">/admin/plugins</span>，当前服务没有提供该接口。
              插件功能需要配套的后端版本；升级并重启服务后点击「重新读取」即可。
            </p>
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshCw size={13} />}
              loading={refreshing}
              onClick={refresh}
            >
              重新读取
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="admin-callout admin-callout--ok">
            <ShieldCheck size={18} />
            <div>
              <strong>
                {loading
                  ? '正在读取插件列表…'
                  : `${plugins.length} 个插件 · ${enabledCount} 个已启用`}
              </strong>
              <p>
                插件以本机子进程（stdio）或 HTTP 端点运行，命令、参数与环境变量都由本机执行，
                请只添加可信来源。readonly 模式的 <span className="mono">bidcraft mcp</span>{' '}
                会拒绝所有插件工具。
              </p>
            </div>
          </div>

          <div className="admin-list-toolbar">
            <AdminSegment
              label="按启用状态筛选插件"
              value={filter}
              onChange={(value) => setFilter(value as PluginsFilter)}
              items={[
                { id: 'all', label: '全部', count: plugins.length },
                { id: 'enabled', label: '已启用', count: enabledCount },
                { id: 'disabled', label: '已停用', count: plugins.length - enabledCount },
              ]}
            />
            <div className="plugin-toolbar-right">
              <span className="admin-list-caption">
                对话中显示为 <span className="mono">插件名__工具名</span>
              </span>
              <input
                ref={importInputRef}
                type="file"
                accept=".zip,application/zip,application/x-zip-compressed"
                className="sr-only"
                aria-label="选择插件包文件"
                onChange={onImportSelected}
              />
              <Button
                variant="secondary"
                size="sm"
                icon={<Upload size={13} />}
                loading={importing}
                disabled={deletingId !== null}
                onClick={() => importInputRef.current?.click()}
              >
                {importing ? '正在导入…' : '导入插件包'}
              </Button>
              <button
                type="button"
                className="text-btn"
                onClick={refresh}
                disabled={refreshing}
              >
                <RefreshCw size={13} className={refreshing ? 'spin' : undefined} />
                刷新
              </button>
            </div>
          </div>

          {error ? (
            <div className="admin-panel-inner">
              <ErrorState message={error} onRetry={refresh} />
            </div>
          ) : loading ? (
            <AdminSkeletonRows rows={3} />
          ) : plugins.length === 0 ? (
            <div className="admin-panel-inner">
              <EmptyState
                icon={Plug}
                title="还没有接入 MCP 插件"
                description="插件能把本机的文件读写、网页抓取、长期记忆等能力接进对话。可以从内置预设开始填参数，也可以手写本机命令或 HTTP 端点；命令需要先在本机安装好。"
                actions={
                  <Button
                    variant="primary"
                    icon={<Plus size={15} />}
                    onClick={() => setModal({ mode: 'create' })}
                  >
                    添加第一个插件
                  </Button>
                }
              />
              <div className="admin-empty-note">
                预设包含 filesystem、fetch、memory、time；预设只填入参数，不会自动安装 npm 包或命令。
              </div>
            </div>
          ) : rows.length === 0 ? (
            <div className="admin-panel-inner">
              <EmptyState
                icon={Plug}
                compact
                title={filter === 'enabled' ? '没有已启用的插件' : '没有已停用的插件'}
                description="切换上方的筛选可以看到全部插件。"
                actions={
                  <Button variant="secondary" onClick={() => setFilter('all')}>
                    显示全部
                  </Button>
                }
              />
            </div>
          ) : (
            <div className="plugin-list">
              {rows.map((plugin) => {
                const result = testResults[plugin.id]
                const endpoint = endpointOf(plugin)
                const toolCount = toolCountOf(plugin, result)
                const envCount = Object.keys(plugin.env).length
                return (
                  <article
                    key={plugin.id}
                    className={className('plugin-row', !plugin.enabled && 'is-disabled')}
                  >
                    <div className="plugin-main">
                      <div className="plugin-title-line">
                        <h4>{plugin.name}</h4>
                        <span
                          className={className(
                            'plugin-transport',
                            plugin.transport === 'http' && 'plugin-transport--http',
                          )}
                        >
                          {plugin.transport === 'http' ? 'HTTP' : 'stdio'}
                        </span>
                        {plugin.tool_allowlist.length > 0 ? (
                          <Badge tone="accent" subtle>
                            白名单 {plugin.tool_allowlist.length}
                          </Badge>
                        ) : (
                          <Badge tone="neutral" subtle>
                            全部工具
                          </Badge>
                        )}
                        {(plugin.fail_streak ?? 0) > 0 && (
                          <Badge tone="warn" subtle>
                            连续失败 {plugin.fail_streak}
                          </Badge>
                        )}
                        {plugin.auto_disabled && (
                          <Badge tone="danger" subtle>
                            自动停用
                          </Badge>
                        )}
                      </div>
                      <div className="plugin-endpoint mono" title={endpoint || undefined}>
                        {endpoint || (
                          <span className="subtle">
                            未填写{plugin.transport === 'http' ? '端点地址' : '启动命令'}
                          </span>
                        )}
                      </div>
                      <div className="plugin-meta">
                        <span>
                          <span className="admin-meta-label">工具</span>
                          {toolCount !== null
                            ? `${toolCount} 个`
                            : plugin.last_test_ok === false
                              ? '上次测试失败'
                              : '测试后显示'}
                        </span>
                        {envCount > 0 && (
                          <span>
                            <span className="admin-meta-label">环境变量</span>
                            {envCount} 项
                          </span>
                        )}
                        <span title="stdio 子进程的资源上限">
                          <span className="admin-meta-label">资源</span>
                          {plugin.mem_limit_mb} MB · {plugin.cpu_seconds} 秒
                        </span>
                        <span>
                          <span className="admin-meta-label">更新</span>
                          {formatRelative(plugin.updated_at)}
                        </span>
                      </div>
                    </div>

                    <div className="plugin-status">
                      <label
                        className={className(
                          'quota-switch',
                          togglingId === plugin.id && 'is-disabled',
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={plugin.enabled}
                          disabled={togglingId === plugin.id}
                          onChange={(event) =>
                            void toggleEnabled(plugin, event.target.checked)
                          }
                          aria-label={`${plugin.enabled ? '停用' : '启用'}插件 ${plugin.name}`}
                        />
                        <span className="quota-switch-track" aria-hidden>
                          <span />
                        </span>
                        <span className="plugin-switch-text">
                          {plugin.enabled ? '已启用' : '已停用'}
                        </span>
                      </label>
                      {plugin.auto_disabled && (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={togglingId === plugin.id}
                          onClick={() => void toggleEnabled(plugin, true)}
                        >
                          重新启用
                        </Button>
                      )}
                      {/* 展开的测试面板已经展示了结果，避免同一行重复两遍说明。 */}
                      {!result && (
                        <>
                          <LastTestStatus
                            ok={plugin.last_test_ok}
                            at={plugin.last_test_at}
                            message={plugin.last_test_message}
                          />
                          {plugin.last_test_message && (
                            <p
                              className="admin-last-test-message"
                              title={plugin.last_test_message}
                            >
                              {plugin.last_test_message}
                            </p>
                          )}
                        </>
                      )}
                    </div>

                    <div className="admin-row-actions">
                      <Button
                        variant="secondary"
                        size="sm"
                        icon={<RefreshCw size={12} />}
                        loading={testingId === plugin.id}
                        disabled={Boolean(testingId) || deletingId !== null}
                        onClick={() => void runTest(plugin)}
                      >
                        测试
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        icon={<Pencil size={12} />}
                        disabled={Boolean(testingId) || deletingId !== null}
                        onClick={() => setModal({ mode: 'edit', plugin })}
                      >
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        icon={<Download size={12} />}
                        loading={exportingId === plugin.id}
                        disabled={Boolean(testingId) || deletingId !== null}
                        onClick={() => void exportPackage(plugin)}
                        title={`导出 ${plugin.name}.bidplugin.zip`}
                      >
                        导出包
                      </Button>
                      <IconButton
                        label={`删除插件 ${plugin.name}`}
                        tone="danger"
                        disabled={deletingId !== null}
                        onClick={() => void remove(plugin)}
                      >
                        {deletingId === plugin.id ? (
                          <Loader2 size={14} className="spin" />
                        ) : (
                          <Trash2 size={14} />
                        )}
                      </IconButton>
                    </div>

                    {result && (
                      <PluginTestPanel
                        plugin={plugin}
                        result={result}
                        onClose={() => dismissResult(plugin.id)}
                      />
                    )}
                  </article>
                )
              })}
            </div>
          )}

          <div className="admin-footnote">
            测试会真实启动一次子进程或请求该端点，超时与握手失败会以服务端返回的中文原因显示在行内。
            插件配置只写入本机数据库；停用的插件不会参与对话的工具聚合，但测试仍可正常使用。
            「导入插件包」读取 <span className="mono">.bidplugin.zip</span>，同名插件默认按包内配置覆盖升级；
            导出的包含命令、参数与环境变量，分享前请确认其中没有密钥。
          </div>
        </>
      )}

      {modal && (
        <AdminPluginModal
          open
          state={modal}
          catalog={catalog}
          onClose={() => setModal(null)}
          onSaved={onSaved}
        />
      )}
    </div>
  )
}

/** 测试结果用独立面板展开：成功列工具名，失败突出服务端的中文原因。 */
function PluginTestPanel({
  plugin,
  result,
  onClose,
}: {
  plugin: Plugin
  result: PluginTestResult
  onClose: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const latency = result.latency_ms !== undefined ? formatLatency(result.latency_ms) : ''

  // 结果面板可能在折叠区域下方：只在不完整可见时滚到最近位置，避免打断正在阅读的用户。
  useEffect(() => {
    const node = panelRef.current
    if (!node) return
    const rect = node.getBoundingClientRect()
    if (rect.top >= 0 && rect.bottom <= window.innerHeight) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    node.scrollIntoView({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' })
  }, [])

  return (
    <div
      ref={panelRef}
      className={className('plugin-test-panel', result.ok ? 'is-ok' : 'is-fail')}
      role="region"
      aria-label={`插件 ${plugin.name} 的测试结果`}
    >
      <div className="plugin-test-head">
        {result.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
        <strong>{result.ok ? '握手成功' : '测试未通过'}</strong>
        {result.ok && (
          <span className="plugin-test-count">{result.tools.length} 个工具</span>
        )}
        {latency && <span className="plugin-test-latency mono">{latency}</span>}
        <button
          type="button"
          className="plugin-test-close"
          onClick={onClose}
          aria-label="收起测试结果"
        >
          <X size={13} />
        </button>
      </div>
      <p className="plugin-test-message">
        {result.message ||
          (result.ok ? '服务已返回工具清单。' : '服务没有返回失败原因，请检查命令、参数与网络。')}
      </p>
      {result.ok &&
        (result.tools.length > 0 ? (
          <>
            <div className="plugin-test-label">
              {plugin.tool_allowlist.length > 0
                ? `tools/list 全部 ${result.tools.length} 项 · 白名单 ${plugin.tool_allowlist.length} 项，只影响对话里的暴露范围`
                : `tools/list 返回的全部 ${result.tools.length} 项工具`}
            </div>
            <ul className="plugin-tools">
              {result.tools.map((tool) => (
                <li key={tool} className="mono">
                  {tool}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="plugin-tools-none">
            该服务没有声明任何工具；接入对话后也不会出现可调用的方法。
          </p>
        ))}
    </div>
  )
}
