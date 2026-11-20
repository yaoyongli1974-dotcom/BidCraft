import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Cpu,
  Download,
  FileDown,
  FileSpreadsheet,
  FileText,
  FileType2,
  HardDrive,
  ShieldAlert,
  ShieldCheck,
  WifiOff,
  type LucideIcon,
} from 'lucide-react'
import { useWorkspace } from '../useWorkspace'
import {
  ApiError,
  downloadBlob,
  exportProject,
  getRiskOverview,
  messageOf,
  type ExportForceOptions,
} from '../../lib/api'
import type { ExportFormat, ExportRecord, RiskOverview } from '../../lib/types'
import {
  className,
  formatDate,
  formatRelative,
  outlineCount,
  projectStats,
} from '../../lib/format'
import { useHealth } from '../../context/useHealth'
import { useLocalStorageState, useQuery } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { ProgressBar } from '../../components/Feedback'
import { Modal } from '../../components/Modal'
import { StatTile, TabHeading } from '../../components/Workbench'
import { useToast } from '../../context/useToast'

const FORMATS: Array<
  ExportFormat & { icon: LucideIcon; recommended?: boolean; note?: string }
> = [
  {
    id: 'docx',
    title: 'Word 标书',
    extension: '.docx',
    description: '标准交付格式。保留封面、目录域、页码与宋体/黑体排版，待填占位会标红。',
    icon: FileText,
    recommended: true,
  },
  {
    id: 'pdf',
    title: 'PDF 成品',
    extension: '.pdf',
    description: '用于打印或投标平台上传。服务端调用 LibreOffice 转换并刷新目录页码。',
    icon: FileDown,
    note: '需要服务端安装 LibreOffice',
  },
  {
    id: 'xlsx',
    title: '台账工作簿',
    extension: '.xlsx',
    description: '导出事实、材料、技术参数、评分办法与废标风险五张表，便于复核与归档。',
    icon: FileSpreadsheet,
    note: '不依赖正文',
  },
  {
    id: 'md',
    title: 'Markdown 归档',
    extension: '.md',
    description: '保留章节结构与占位符，适合版本管理、二次编辑或导入其他工具。',
    icon: FileType2,
  },
]

export function ExportTab() {
  const { ref, basePath, project, reloadProject, canWrite } = useWorkspace()
  const toast = useToast()
  const { health, error: healthError } = useHealth()
  const riskQuery = useQuery(`risk-overview:${ref}`, () => getRiskOverview(ref))
  const riskOverview: RiskOverview | undefined = riskQuery.data
  const stats = projectStats(project)
  const outline = outlineCount(project)
  const [exporting, setExporting] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [forceTarget, setForceTarget] = useState<(ExportFormat & { title: string }) | null>(null)
  const [forceReason, setForceReason] = useState('')
  const [forceError, setForceError] = useState('')
  const [records, setRecords] = useLocalStorageState<Record<string, ExportRecord[]>>(
    'bidcraft:exports',
    {},
  )
  const projectRecords = records[ref] || []
  const hasBody = stats.written > 0
  const missingRequired = stats.missing_required
  const issues = stats.issues_open

  const gateBlocked = Boolean(riskOverview?.gate?.blocked)
  const blockingOpen = riskOverview?.gate?.blocking_open ?? 0
  const gateReason =
    riskOverview?.gate?.reason?.trim() ||
    `存在 ${blockingOpen} 项未处置的废标/阻断风险，请先在风险总览中处置。`
  const canForce = project.my_role === 'owner' || project.my_role === 'admin'
  const forceReasonValid = forceReason.trim().length >= 4

  const runExport = async (format: ExportFormat, force?: ExportForceOptions) => {
    if (!canWrite || exporting) return
    if (format.id !== 'xlsx' && !hasBody) {
      toast.warning('还没有正文', '先在大纲页生成章节，或在正文页书写后再导出。')
      return
    }
    setExporting(format.id)
    setErrors((current) => ({ ...current, [format.id]: '' }))
    try {
      const { blob, filename } = await exportProject(ref, format.id, force)
      downloadBlob(blob, filename)
      const record: ExportRecord = {
        filename,
        format: format.id,
        at: new Date().toISOString(),
      }
      setRecords((current) => ({
        ...current,
        [ref]: [record, ...(current[ref] || [])].slice(0, 8),
      }))
      toast.success(
        force?.force ? '已强制导出' : '导出完成',
        `已开始下载 ${filename}`,
      )
      try {
        await reloadProject()
      } catch {
        // The download succeeded; status refresh is a secondary concern.
      }
    } catch (error) {
      const message = messageOf(error)
      // 409 说明门禁在我们展示后又被触发（例如别处新增了阻断项）：刷新总览并回填原因。
      if (error instanceof ApiError && error.status === 409) {
        riskQuery.refresh()
      }
      setErrors((current) => ({ ...current, [format.id]: message }))
      toast.error(`导出 ${format.title} 失败`, message)
    } finally {
      setExporting(null)
    }
  }

  const openForce = (format: ExportFormat & { title: string }) => {
    if (!canForce) return
    setForceTarget(format)
    setForceReason('')
    setForceError('')
  }

  const submitForce = async () => {
    if (!forceTarget) return
    if (!forceReasonValid) {
      setForceError('请填写至少 4 个字的原因，说明为何确需跳过交付门禁。')
      return
    }
    const target = forceTarget
    setForceTarget(null)
    await runExport(target, { force: true, force_reason: forceReason.trim() })
  }

  const readiness = [
    {
      key: 'body',
      label: hasBody ? `已有 ${stats.written} / ${outline} 章正文` : '尚无可导出正文',
      ok: hasBody,
      to: `${basePath}/draft`,
      action: hasBody ? '查看正文' : '去写作',
    },
    {
      key: 'facts',
      label:
        missingRequired > 0 ? `仍有 ${missingRequired} 项必填事实缺失` : '必填事实已齐全',
      ok: missingRequired === 0,
      to: `${basePath}/ledger`,
      action: missingRequired > 0 ? '去补齐' : '查看台账',
    },
    {
      key: 'issues',
      label: issues > 0 ? `${issues} 项未处置核对问题` : '没有未处置的核对问题',
      ok: issues === 0,
      to: `${basePath}/check`,
      action: issues > 0 ? '去核对' : '查看核对',
    },
    {
      key: 'gate',
      label: gateBlocked
        ? `${blockingOpen} 项未处置的废标/阻断风险`
        : riskOverview
          ? '交付门禁未拦截'
          : '门禁状态未读取',
      ok: !gateBlocked,
      to: `${basePath}/risk`,
      action: gateBlocked ? '去风险总览处置' : '查看风险总览',
    },
  ]

  const modelNote = health?.offline
    ? '离线模式已开启：模型调用被禁用，正文可能来自确定性草稿。导出前请人工润色。'
    : health && !health.model_configured
      ? '未配置模型：当前正文可能由确定性草稿生成。可以导出，但建议先人工润色。'
      : health
        ? '模型已配置。核对与导出仍在本机完成，不会上传项目数据。'
        : healthError
          ? `无法读取服务状态：${healthError}`
          : '正在读取服务状态…'

  return (
    <div className="tab-content export-tab">
      <TabHeading
        eyebrow="导出交付"
        title="导出交付文件"
        description="选择格式生成下载。文件由本机后端生成，源文件与项目数据不会离开当前部署。"
        actions={
          <span className="export-status">
            <HardDrive size={14} />
            本机生成
          </span>
        }
      />

      <div className="export-grid">
        <div className="export-formats">
          <div className="export-formats-head">
            <div>
              <div className="eyebrow">导出格式</div>
              <h3>选择导出格式</h3>
            </div>
            <span className="subtle">下载即保存到浏览器默认位置</span>
          </div>

          <div className="format-grid">
            {FORMATS.map((format) => {
              const gateLocked = gateBlocked && format.id !== 'xlsx'
              const disabled =
                !canWrite ||
                (format.id !== 'xlsx' && !hasBody) ||
                Boolean(exporting) ||
                gateLocked
              const Icon = format.icon
              const error = errors[format.id]
              return (
                <article
                  key={format.id}
                  className={className(
                    'format-card',
                    `format-card--${format.id}`,
                    format.recommended && 'is-recommended',
                    disabled && 'is-disabled',
                    gateLocked && 'is-gate-locked',
                  )}
                >
                  <div className="format-card-head">
                    <span className="format-icon" aria-hidden>
                      <Icon size={22} strokeWidth={1.6} />
                    </span>
                    <div className="format-name">
                      <strong>{format.title}</strong>
                      <span className="mono">{format.extension}</span>
                    </div>
                    {format.recommended && <Badge tone="accent" subtle>推荐</Badge>}
                    {format.note && <span className="format-note">{format.note}</span>}
                  </div>
                  <p className="format-desc">{format.description}</p>
                  {gateLocked && (
                    <div className="format-gate-note" role="note">
                      <ShieldAlert size={13} />
                      <span>
                        门禁未通过：{blockingOpen} 项废标/阻断风险未处置。
                        <Link to={`${basePath}/risk`} className="text-link">
                          去风险总览处置 <ArrowRight size={11} />
                        </Link>
                      </span>
                    </div>
                  )}
                  <div className="format-foot">
                    <span className="format-availability">
                      {format.id === 'xlsx'
                        ? '台账就绪即可导出'
                        : gateLocked
                          ? '阻断风险未处置'
                          : hasBody
                            ? `${stats.written} 章正文`
                            : '尚无可导出正文'}
                    </span>
                    <span className="format-actions">
                      {gateLocked && canForce && hasBody && (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<ShieldAlert size={13} />}
                          disabled={Boolean(exporting)}
                          title="跳过交付门禁并导出，需填写原因"
                          onClick={() => openForce(format)}
                        >
                          强制导出
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant={format.recommended ? 'primary' : 'secondary'}
                        icon={<Download size={13} />}
                        loading={exporting === format.id}
                        disabled={disabled}
                        title={
                          !canWrite
                            ? '只读成员不能导出文件'
                            : gateLocked
                              ? '存在未处置的废标/阻断风险，请先处置后再导出'
                              : undefined
                        }
                        onClick={() => void runExport(format)}
                      >
                        {format.id === 'xlsx' ? '导出台账' : '导出'}
                      </Button>
                    </span>
                  </div>
                  {error && (
                    <div className="export-error" role="alert">
                      <AlertTriangle size={13} />
                      <span>{error}</span>
                    </div>
                  )}
                </article>
              )
            })}
          </div>

          {gateBlocked ? (
            <div className="export-gate" role="alert">
              <span className="export-gate-icon" aria-hidden>
                <ShieldAlert size={20} />
              </span>
              <div className="export-gate-copy">
                <strong>导出门禁未通过 · {blockingOpen} 项废标/阻断风险未处置</strong>
                <p>{gateReason}</p>
                <p className="export-gate-hint">
                  docx / pdf / md 暂不可导出；台账工作簿（xlsx）不受影响。
                  {canForce
                    ? '如确需先导出，可填写原因后强制导出。'
                    : '如需强制导出，请联系项目负责人。'}
                </p>
              </div>
              <Link to={`${basePath}/risk`} className="text-link">
                去风险总览 <ArrowRight size={11} />
              </Link>
            </div>
          ) : riskOverview && riskOverview.checked_at ? (
            <div className="export-gate export-gate--ok">
              <span className="export-gate-icon" aria-hidden>
                <ShieldCheck size={20} />
              </span>
              <div className="export-gate-copy">
                <strong>导出门禁通过</strong>
                <p>
                  没有未处置的废标/阻断风险 · 上次核对{' '}
                  {formatRelative(riskOverview.checked_at)}
                </p>
              </div>
              <Link to={`${basePath}/risk`} className="text-link">
                查看风险总览 <ArrowRight size={11} />
              </Link>
            </div>
          ) : null}

          <div className="export-callout">
            <FileDown size={17} />
            <div>
              <strong>关于 PDF</strong>
              <p>
                PDF 由后端调用 LibreOffice 转换，首次导出可能较慢。若服务端未安装
                LibreOffice，接口会返回错误，Word 与台账导出不受影响。
              </p>
            </div>
          </div>
        </div>

        <aside className="export-side">
          <section className="panel export-readiness">
            <div className="panel-head">
              <div>
                <div className="eyebrow">交付检查</div>
                <h3>交付前检查</h3>
              </div>
            </div>
            <div className="readiness-list">
              {readiness.map((item) => (
                <div key={item.key} className={className('readiness-item', item.ok && 'is-ok')}>
                  {item.ok ? (
                    <CheckCircle2 size={17} className="success-icon" />
                  ) : (
                    <AlertTriangle size={17} className="warn-icon" />
                  )}
                  <div>
                    <span>{item.label}</span>
                    <Link to={item.to} className="text-link">
                      {item.action} <ArrowRight size={11} />
                    </Link>
                  </div>
                </div>
              ))}
            </div>
            <div className="readiness-progress">
              <div>
                <span>章节完成度</span>
                <span className="mono">
                  {stats.written}/{outline}
                </span>
              </div>
              <ProgressBar
                value={outline ? Math.round((stats.written / outline) * 100) : 0}
                thin
                tone={hasBody ? 'success' : 'accent'}
                label="章节完成度"
              />
            </div>
            {missingRequired > 0 && (
              <div className="readiness-warning">
                缺失必填不会阻止导出已有正文，但文件中可能包含{' '}
                <code>{'{{待填:...}}'}</code> 占位。
              </div>
            )}
          </section>

          <div className="export-stats">
            <StatTile
              label="正文章节"
              value={`${stats.written}/${outline}`}
              tone={hasBody ? 'success' : 'neutral'}
              detail="已写 / 大纲"
            />
            <StatTile
              label="未处置问题"
              value={issues}
              tone={issues > 0 ? 'warn' : 'success'}
              detail="核对后更新"
            />
          </div>

          <section className={`model-card ${health?.offline ? 'is-offline' : ''}`}>
            <div className="model-card-head">
              {health?.offline ? <WifiOff size={17} /> : <Cpu size={17} />}
              <strong>生成环境</strong>
              <Badge
                tone={
                  health?.offline
                    ? 'warn'
                    : health?.model_configured
                      ? 'success'
                      : 'info'
                }
                subtle
              >
                {health?.offline
                  ? '离线'
                  : health?.model_configured
                    ? '模型已配置'
                    : health
                      ? '确定性草稿'
                      : '状态未知'}
              </Badge>
            </div>
            <p>{modelNote}</p>
            <div className="model-home">
              <ShieldCheck size={13} />
              本机服务端
            </div>
          </section>

          <section className="panel export-history">
            <div className="panel-head">
              <div>
                <div className="eyebrow">导出记录</div>
                <h3>最近导出</h3>
              </div>
              <span className="subtle">本机记录</span>
            </div>
            {projectRecords.length === 0 ? (
              <div className="history-empty">
                <FileDown size={20} />
                <p>还没有导出记录。成功下载后会显示在这里。</p>
              </div>
            ) : (
              <ul className="history-list">
                {projectRecords.map((record, index) => {
                  const meta = FORMATS.find((item) => item.id === record.format)
                  return (
                    <li key={`${record.at}-${index}`}>
                      <span className="history-icon" aria-hidden>
                        {record.format === 'xlsx' ? (
                          <FileSpreadsheet size={15} />
                        ) : (
                          <FileText size={15} />
                        )}
                      </span>
                      <div>
                        <span className="history-name" title={record.filename}>
                          {record.filename}
                        </span>
                        <span className="history-meta">
                          {meta?.title || record.format.toUpperCase()} ·{' '}
                          {formatRelative(record.at)}
                        </span>
                      </div>
                      <span className="history-time mono" title={formatDate(record.at)}>
                        {formatDate(record.at, false)}
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
        </aside>
      </div>

      <div className="export-footnote">
        <ShieldCheck size={15} />
        <span>
          导出文件保存在浏览器下载目录，后端不复制到外部位置。敏感招标文件请仅在受控设备上处理。
        </span>
      </div>

      <Modal
        open={Boolean(forceTarget)}
        onClose={() => setForceTarget(null)}
        size="sm"
        eyebrow="强制导出"
        title={forceTarget ? `强制导出 ${forceTarget.title}` : '强制导出'}
        footer={
          <>
            <Button variant="ghost" onClick={() => setForceTarget(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              icon={<ShieldAlert size={14} />}
              disabled={!forceReasonValid || Boolean(exporting)}
              onClick={() => void submitForce()}
            >
              确认强制导出
            </Button>
          </>
        }
      >
        <div className="export-force-body">
          <div className="export-force-alert">
            <ShieldAlert size={16} aria-hidden />
            <p>
              仍有 {blockingOpen} 项未处置的废标/阻断风险。强制导出的文件可能无法通过评审，
              该操作会记入项目审计。仅项目负责人或管理员可以执行。
            </p>
          </div>
          <label className="risk-ignore-field">
            <span>
              强制导出原因 <em>至少 4 个字，必填</em>
            </span>
            <textarea
              className="textarea"
              rows={3}
              value={forceReason}
              placeholder="例如：业主临时要求先行提交，阻断项将在澄清阶段补充。"
              onChange={(event) => {
                setForceReason(event.target.value)
                if (forceError) setForceError('')
              }}
              aria-label="强制导出原因"
            />
          </label>
          {forceError && (
            <p className="field-error" role="alert">
              {forceError}
            </p>
          )}
          <p className="export-force-hint">
            原因会随导出记录写入审计日志，便于交付后追溯。
          </p>
        </div>
      </Modal>
    </div>
  )
}
