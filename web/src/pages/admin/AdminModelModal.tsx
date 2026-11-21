import { useEffect, useState } from 'react'
import { AlertTriangle, KeyRound } from 'lucide-react'
import type { ModelConfig, ModelInput } from '../../lib/types'
import { MODEL_KINDS, MODEL_PROVIDERS, modelKindMeta } from '../../lib/format'
import { createModel, updateModel } from '../../api/compliance'
import { messageOf } from '../../lib/http'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'
import { Badge } from '../../components/Badge'

export type ModelModalState =
  | { mode: 'create'; kind: string }
  | { mode: 'edit'; model: ModelConfig }

interface ModelModalProps {
  open: boolean
  mode: 'create' | 'edit'
  model?: ModelConfig
  initialKind?: string
  onClose: () => void
  onSaved: (model: ModelConfig, created: boolean) => void
}

const PROVIDERS = Object.entries(MODEL_PROVIDERS)

export function AdminModelModal({
  open,
  mode,
  model,
  initialKind = 'chat',
  onClose,
  onSaved,
}: ModelModalProps) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState(initialKind)
  const [provider, setProvider] = useState('openai')
  const [baseUrl, setBaseUrl] = useState('')
  const [modelId, setModelId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [temperature, setTemperature] = useState(0.4)
  const [maxTokens, setMaxTokens] = useState(4000)
  const [optionsText, setOptionsText] = useState('{}')
  const [enabled, setEnabled] = useState(true)
  const [isDefault, setIsDefault] = useState(false)
  const [clearKey, setClearKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setTouched(false)
    if (mode === 'edit' && model) {
      setName(model.name)
      setKind(model.kind)
      setProvider(model.provider)
      setBaseUrl(model.base_url || '')
      setModelId(model.model || '')
      setApiKey('')
      setTemperature(model.temperature ?? 0.4)
      setMaxTokens(model.max_tokens ?? 4000)
      setOptionsText(JSON.stringify(model.options || {}, null, 2))
      setEnabled(Boolean(model.enabled))
      setIsDefault(Boolean(model.is_default))
      setClearKey(false)
    } else {
      setName('')
      setKind(initialKind)
      setProvider('openai')
      setBaseUrl('')
      setModelId('')
      setApiKey('')
      setTemperature(0.4)
      setMaxTokens(4000)
      setOptionsText('{}')
      setEnabled(true)
      setIsDefault(false)
      setClearKey(false)
    }
  }, [open, mode, model, initialKind])

  const allowedProviders = kind === 'chat' ? PROVIDERS : PROVIDERS.filter(([id]) => id !== 'ollama')

  useEffect(() => {
    if (kind !== 'chat' && provider === 'ollama') setProvider('openai')
  }, [kind, provider])

  const kindMeta = modelKindMeta(kind)

  const submit = async () => {
    setTouched(true)
    if (!name.trim()) return
    let options: Record<string, unknown> = {}
    if (optionsText.trim()) {
      try {
        const parsed = JSON.parse(optionsText)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setError('高级参数必须是 JSON 对象，例如 {"timeout": 120}。')
          return
        }
        options = parsed as Record<string, unknown>
      } catch {
        setError('高级参数不是合法的 JSON。')
        return
      }
    }
    setSaving(true)
    setError(null)
    const payload: ModelInput = {
      name: name.trim(),
      kind,
      provider,
      base_url: baseUrl.trim(),
      model: modelId.trim(),
      temperature: Number(temperature),
      max_tokens: Math.round(Number(maxTokens)),
      options,
      enabled,
      is_default: isDefault,
    }
    if (apiKey.trim()) payload.api_key = apiKey.trim()
    if (clearKey) payload.clear_api_key = true

    try {
      const saved =
        mode === 'edit' && model
          ? await updateModel(model.id, payload)
          : await createModel(payload)
      onSaved(saved, mode === 'create')
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
      title={mode === 'edit' ? '编辑模型配置' : '添加模型配置'}
      eyebrow={mode === 'edit' ? '更新端点与默认策略' : '连接一个生成或检索端点'}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="model-config-form"
            loading={saving}
          >
            {mode === 'edit' ? '保存修改' : '创建模型'}
          </Button>
        </>
      }
    >
      <form
        id="model-config-form"
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="admin-form-note">
          <Badge tone={kindMeta.tone} subtle>
            {kindMeta.label}
          </Badge>
          <span>
            {kind === 'image'
              ? '图片模型用于正文配图生成；保存后可在正文页的图片占位中触发生成。'
              : kind === 'embedding'
                ? '向量模型用于检索能力；不会直接生成正文。'
                : '对话模型用于大纲与正文生成；建议配置一个默认模型。'}
          </span>
        </div>

        <div className="admin-form-grid">
          <div className="form-field">
            <label htmlFor="model-name">
              配置名称 <span className="required-mark">必填</span>
            </label>
            <input
              id="model-name"
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：本地 Qwen 生成模型"
              maxLength={120}
              data-autofocus
              disabled={saving}
            />
            {touched && !name.trim() && <span className="field-error">请填写配置名称。</span>}
          </div>

          <div className="form-field">
            <label htmlFor="model-kind">模型类型</label>
            <select
              id="model-kind"
              className="select"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              disabled={saving}
            >
              {MODEL_KINDS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="model-provider">服务提供方</label>
            <select
              id="model-provider"
              className="select"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              disabled={saving}
            >
              {allowedProviders.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
            <p className="field-hint">
              Ollama 目前仅支持对话模型；OpenAI 兼容端点可连接本地网关、vLLM 或云服务。
            </p>
          </div>

          <div className="form-field">
            <label htmlFor="model-id">模型 ID</label>
            <input
              id="model-id"
              className="input mono"
              value={modelId}
              onChange={(event) => setModelId(event.target.value)}
              placeholder="例如：qwen3:32b 或 gpt-4o-mini"
              disabled={saving}
            />
          </div>

          <div className="form-field admin-form-span">
            <label htmlFor="model-base-url">Base URL</label>
            <input
              id="model-base-url"
              className="input mono"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="例如：http://127.0.0.1:11434/v1"
              disabled={saving}
            />
            <p className="field-hint">
              填写 OpenAI 兼容端点的根地址；不需要带 /chat/completions 或 /models。
            </p>
          </div>

          <div className="form-field admin-form-span">
            <label htmlFor="model-api-key">
              API 密钥 {mode === 'edit' && <span className="field-inline-status">留空保持不变</span>}
            </label>
            <div className="admin-key-input">
              <KeyRound size={15} aria-hidden />
              <input
                id="model-api-key"
                className="input mono"
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={
                  mode === 'edit' && model?.has_api_key
                    ? '已配置，留空不改；填写则覆盖'
                    : '可选；Ollama 本地端点通常无需密钥'
                }
                autoComplete="new-password"
                disabled={saving || clearKey}
              />
              {model?.has_api_key && mode === 'edit' && (
                <Badge tone="success" subtle>
                  已配置
                </Badge>
              )}
            </div>
            {mode === 'edit' && model?.has_api_key && (
              <label className="admin-check admin-check--inline">
                <input
                  type="checkbox"
                  checked={clearKey}
                  onChange={(event) => setClearKey(event.target.checked)}
                  disabled={saving}
                />
                <span>清除已保存的密钥</span>
              </label>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="model-temperature">温度</label>
            <input
              id="model-temperature"
              className="input input--number"
              type="number"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(event) => setTemperature(Number(event.target.value))}
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="model-max-tokens">最大输出 tokens</label>
            <input
              id="model-max-tokens"
              className="input input--number"
              type="number"
              min={1}
              max={200000}
              step={1}
              value={maxTokens}
              onChange={(event) => setMaxTokens(Number(event.target.value))}
              disabled={saving}
            />
          </div>

          <div className="admin-check-row admin-form-span">
            <label className="admin-check">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => {
                  setEnabled(event.target.checked)
                  if (!event.target.checked) setIsDefault(false)
                }}
                disabled={saving}
              />
              <span>
                <strong>启用此模型</strong>
                <small>停用后不会参与生成或检索。</small>
              </span>
            </label>
            <label className="admin-check">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(event) => {
                  setIsDefault(event.target.checked)
                  if (event.target.checked) setEnabled(true)
                }}
                disabled={saving}
              />
              <span>
                <strong>设为该类型的默认模型</strong>
                <small>同一类型只保留一个默认；默认模型必须启用。</small>
              </span>
            </label>
          </div>
        </div>

        <details className="admin-advanced">
          <summary>高级参数（JSON）</summary>
          <p className="field-hint">
            可选，写入 options 字段，例如超时时间、上下文长度或供应商专属参数。
          </p>
          <textarea
            className="textarea mono"
            rows={4}
            value={optionsText}
            onChange={(event) => setOptionsText(event.target.value)}
            disabled={saving}
            spellCheck={false}
          />
        </details>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}

        {kind === 'image' && (
          <div className="admin-form-warning">
            <AlertTriangle size={15} />
            图片模型用于正文配图；测试按钮只验证端点连通性，不会实际生成图片。
          </div>
        )}
      </form>
    </Modal>
  )
}
