import { Bot, Wrench } from 'lucide-react'
import type { AgentInfo } from '../lib/types'
import { className } from '../lib/format'
import { Badge } from './Badge'

/**
 * Agent 注册表中的一张卡片。只展示注册表提供的信息；缺失的能力/工具列表
 * 不补默认值，避免让占位内容看起来像真实能力。
 */
export function AgentCard({ agent }: { agent: AgentInfo }) {
  const capabilities = agent.capabilities || []
  const tools = agent.tools || []
  const shownCapabilities = capabilities.slice(0, 4)
  return (
    <article className="agent-card" aria-label={agent.name}>
      <header className="agent-card-head">
        <span className="agent-card-icon" aria-hidden>
          <Bot size={17} />
        </span>
        <div className="agent-card-title">
          <strong>{agent.name}</strong>
          <code className="agent-card-type mono">{agent.agent_type}</code>
        </div>
        {agent.enabled === false ? (
          <Badge tone="neutral" subtle>
            未启用
          </Badge>
        ) : agent.enabled === true ? (
          <Badge tone="success" subtle dot>
            已启用
          </Badge>
        ) : null}
      </header>
      <p className={className('agent-card-desc', !agent.description && 'is-empty')}>
        {agent.description || '注册表未提供职责说明。'}
      </p>
      <div className="agent-card-tags">
        {shownCapabilities.map((capability) => (
          <span key={capability} className="agent-chip">
            {capability}
          </span>
        ))}
        {capabilities.length > shownCapabilities.length && (
          <span className="agent-chip agent-chip--more">
            +{capabilities.length - shownCapabilities.length}
          </span>
        )}
        {!capabilities.length && (
          <span className="agent-chip agent-chip--muted">未声明能力</span>
        )}
      </div>
      <footer className="agent-card-foot">
        <span title="可调用的授权工具数量">
          <Wrench size={12} aria-hidden /> {tools.length ? `${tools.length} 个工具` : '未声明工具'}
        </span>
        {agent.role && <span className="mono">{agent.role}</span>}
      </footer>
    </article>
  )
}
