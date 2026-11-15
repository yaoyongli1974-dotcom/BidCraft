import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'
import {
  BookOpen,
  Cpu,
  Database,
  HardDrive,
  Keyboard,
  Network,
  ShieldCheck,
  WifiOff,
} from 'lucide-react'
import { Modal } from '../components/Modal'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { useHealth } from './HealthContext'
import { className } from '../lib/format'

const HelpContext = createContext<{ openHelp: () => void } | null>(null)

const WORKFLOW = [
  ['01', '导入招标文件', '支持 PDF、DOCX、TXT、XLSX；扫描 PDF 需先做 OCR。'],
  ['02', '解析与校订事实', '自动抽取项目、商务、资格、技术事实，补齐必填项后才会开放生成。'],
  ['03', '编排目录大纲', '按规则包、评分办法或招标格式生成章节，并分配目标字数。'],
  ['04', '生成与改写正文', '逐章生成，所有数字与承诺必须能在事实台账中找到依据。'],
  ['05', '核对与导出', '运行合规核对，按阻断/严重/主要分级修正后导出 DOCX、PDF、XLSX。'],
] as const

export function HelpProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const { health, error, loading, refresh } = useHealth()
  const openHelp = useCallback(() => setOpen(true), [])

  const serviceTone = error ? 'danger' : health ? 'success' : 'neutral'
  const serviceText = loading
    ? '正在检查'
    : error
      ? '无法连接'
      : health
        ? '运行中'
        : '未知'

  return (
    <HelpContext.Provider value={{ openHelp }}>
      {children}
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        variant="drawer"
        title="设置与帮助"
        eyebrow="本机工作区"
        size="md"
      >
        <div className="help-content">
          <section className="help-section">
            <h3 className="help-heading">
              <ShieldCheck size={16} /> 服务状态
            </h3>
            <dl className="kv-list">
              <div>
                <dt>本地服务</dt>
                <dd>
                  <Badge tone={serviceTone} dot>
                    {serviceText}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt>版本</dt>
                <dd className="mono">{health?.version || '—'}</dd>
              </div>
              <div>
                <dt>模型</dt>
                <dd>
                  {health ? (
                    health.offline ? (
                      <span className="inline-note">离线模式，使用确定性草稿</span>
                    ) : health.model_configured ? (
                      <span className="inline-note">已配置 OpenAI 兼容端点</span>
                    ) : (
                      <span className="inline-note">未配置，使用确定性草稿</span>
                    )
                  ) : (
                    '—'
                  )}
                </dd>
              </div>
              <div>
                <dt>数据目录</dt>
                <dd className="mono break-all">{health?.home || '—'}</dd>
              </div>
            </dl>
            {error && (
              <div className="help-callout">
                <WifiOff size={16} />
                <div>
                  <strong>本地服务未连接</strong>
                  <p>{error}</p>
                  <Button size="sm" variant="secondary" onClick={refresh}>
                    重新检查
                  </Button>
                </div>
              </div>
            )}
          </section>

          <section className="help-section">
            <h3 className="help-heading">
              <Network size={16} /> 局域网部署
            </h3>
            <p className="help-text">
              前端是静态文件，由 BidCraft 本地服务同源托管。项目数据、源文件和导出件都留在
              BidCraft 数据目录，不会上传到外部服务；系统也不发送遥测。
            </p>
            <div className="help-chips">
              <span>
                <Cpu size={14} /> 模型调用仅限显式配置的端点
              </span>
              <span>
                <HardDrive size={14} /> SQLite + 原件 + 导出件
              </span>
              <span>
                <Database size={14} /> 无云端同步
              </span>
            </div>
          </section>

          <section className="help-section">
            <h3 className="help-heading">
              <BookOpen size={16} /> 工作流
            </h3>
            <ol className="help-workflow">
              {WORKFLOW.map(([number, title, description]) => (
                <li key={number}>
                  <span className="help-step-no">{number}</span>
                  <div>
                    <strong>{title}</strong>
                    <p>{description}</p>
                  </div>
                </li>
              ))}
            </ol>
          </section>

          <section className="help-section">
            <h3 className="help-heading">
              <Keyboard size={16} /> 快捷键
            </h3>
            <dl className="shortcut-list">
              <div>
                <dt>保存正文 / 大纲</dt>
                <dd>
                  <kbd>Ctrl</kbd>
                  <span>+</span>
                  <kbd>S</kbd>
                </dd>
              </div>
              <div>
                <dt>关闭弹窗 / 抽屉</dt>
                <dd>
                  <kbd>Esc</kbd>
                </dd>
              </div>
              <div>
                <dt>在事实台账中搜索</dt>
                <dd>
                  <kbd>/</kbd>
                </dd>
              </div>
            </dl>
          </section>

          <div className={className('help-footer')}>
            <span>BidCraft · 弱电投标文件工作台</span>
            <span className="mono">v{health?.version || '—'}</span>
          </div>
        </div>
      </Modal>
    </HelpContext.Provider>
  )
}

export function useHelp() {
  const context = useContext(HelpContext)
  if (!context) throw new Error('useHelp 必须在 HelpProvider 内使用')
  return context
}
