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
  ShieldCheck,
  WifiOff,
  type LucideIcon,
} from 'lucide-react'
import { useWorkspace } from '../WorkspacePage'
import { downloadBlob, exportProject, messageOf } from '../../lib/api'
import type { ExportFormat, ExportRecord } from '../../lib/types'
import {
  className,
  formatDate,
  formatRelative,
  outlineCount,
  projectStats,
} from '../../lib/format'
import { useHealth } from '../../context/HealthContext'
import { useLocalStorageState } from '../../lib/hooks'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { ProgressBar } from '../../components/Feedback'
import { StatTile, TabHeading } from '../../components/Workbench'
import { useToast } from '../../context/ToastContext'

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
  const stats = projectStats(project)
  const outline = outlineCount(project)
  const [exporting, setExporting] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [records, setRecords] = useLocalStorageState<Record<string, ExportRecord[]>>(
    'bidcraft:exports',
    {},
  )
  const projectRecords = records[ref] || []
  const hasBody = stats.written > 0
  const missingRequired = stats.missing_required
  const issues = stats.issues_open

  const runExport = async (format: ExportFormat) => {
    if (!canWrite || exporting) return
    if (format.id !== 'xlsx' && !hasBody) {
      toast.warning('还没有正文', '先在大纲页生成章节，或在正文页书写后再导出。')
      return
    }
    setExporting(format.id)
    setErrors((current) => ({ ...current, [format.id]: '' }))
    try {
      const { blob, filename } = await exportProject(ref, format.id)
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
      toast.success('导出完成', `已开始下载 ${filename}`)
      try {
        await reloadProject()
      } catch {
        // The download succeeded; status refresh is a secondary concern.
      }
    } catch (error) {
      const message = messageOf(error)
      setErrors((current) => ({ ...current, [format.id]: message }))
      toast.error(`导出 ${format.title} 失败`, message)
    } finally {
      setExporting(null)
    }
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
      label: issues > 0 ? `${issues} 项未关闭核对问题` : '没有未关闭的核对问题',
      ok: issues === 0,
      to: `${basePath}/check`,
      action: issues > 0 ? '去核对' : '查看核对',
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
              const disabled =
                !canWrite || (format.id !== 'xlsx' && !hasBody) || Boolean(exporting)
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
                  <div className="format-foot">
                    <span className="format-availability">
                      {format.id === 'xlsx'
                        ? '台账就绪即可导出'
                        : hasBody
                          ? `${stats.written} 章正文`
                          : '尚无可导出正文'}
                    </span>
                    <Button
                      size="sm"
                      variant={format.recommended ? 'primary' : 'secondary'}
                      icon={<Download size={13} />}
                      loading={exporting === format.id}
                      disabled={disabled}
                      title={!canWrite ? '只读成员不能导出文件' : undefined}
                      onClick={() => void runExport(format)}
                    >
                      {format.id === 'xlsx' ? '导出台账' : '导出'}
                    </Button>
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
              label="未关闭问题"
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
    </div>
  )
}
