import { useEffect, useState } from 'react'
import { Database, FolderOpen, KeyRound } from 'lucide-react'
import type { KnowledgeBase, KnowledgeBaseInput } from '../../lib/types'
import { KB_PROVIDERS, kbKindMeta, kbProviderLabel } from '../../lib/format'
import { createKnowledgeBase, updateKnowledgeBase } from '../../api/knowledge'
import { messageOf } from '../../lib/http'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'
import { Badge } from '../../components/Badge'

export type KnowledgeModalState =
  | { mode: 'create'; kind: 'local' | 'api' }
  | { mode: 'edit'; kb: KnowledgeBase }

interface KnowledgeModalProps {
  open: boolean
  mode: 'create' | 'edit'
  kb?: KnowledgeBase
  initialKind?: 'local' | 'api'
  onClose: () => void
  onSaved: (kb: KnowledgeBase, created: boolean) => void
}

export function AdminKnowledgeModal({
  open,
  mode,
  kb,
  initialKind = 'local',
  onClose,
  onSaved,
}: KnowledgeModalProps) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'local' | 'api'>(initialKind)
  const [provider, setProvider] = useState('local')
  const [location, setLocation] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [datasetId, setDatasetId] = useState('')
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
    if (mode === 'edit' && kb) {
      setName(kb.name)
      setKind(kb.kind === 'api' ? 'api' : 'local')
      setProvider(kb.provider || 'local')
      setLocation(kb.location || '')
      setApiKey('')
      setDatasetId(kb.dataset_id || '')
      setOptionsText(JSON.stringify(kb.options || {}, null, 2))
      setEnabled(Boolean(kb.enabled))
      setIsDefault(Boolean(kb.is_default))
      setClearKey(false)
    } else {
      setName('')
      setKind(initialKind)
      setProvider(initialKind === 'local' ? 'local' : 'generic')
      setLocation('')
      setApiKey('')
      setDatasetId('')
      setOptionsText('{}')
      setEnabled(true)
      setIsDefault(false)
      setClearKey(false)
    }
  }, [open, mode, kb, initialKind])

  useEffect(() => {
    if (kind === 'local') {
      setProvider('local')
      setDatasetId('')
    } else if (provider === 'local') {
      setProvider('generic')
    }
  }, [kind, provider])

  const apiProviders = Object.entries(KB_PROVIDERS).filter(([id]) => id !== 'local')
  const needsDataset = kind === 'api' && ['dify', 'ragflow'].includes(provider)
  const kindMeta = kbKindMeta(kind)

  const submit = async () => {
    setTouched(true)
    if (!name.trim()) return
    if (!location.trim()) return
    if (kind === 'api' && needsDataset && !datasetId.trim()) {
      setError('Dify / RAGFlow 需要填写数据集 ID。')
      return
    }
    let options: Record<string, unknown> = {}
    if (optionsText.trim()) {
      try {
        const parsed = JSON.parse(optionsText)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setError('高级参数必须是 JSON 对象。')
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
    const payload: KnowledgeBaseInput = {
      name: name.trim(),
      kind,
      provider,
      location: location.trim(),
      dataset_id: datasetId.trim(),
      options,
      enabled,
      is_default: isDefault,
    }
    if (apiKey.trim()) payload.api_key = apiKey.trim()
    if (clearKey) payload.clear_api_key = true

    try {
      const saved =
        mode === 'edit' && kb
          ? await updateKnowledgeBase(kb.id, payload)
          : await createKnowledgeBase(payload)
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
      title={mode === 'edit' ? '编辑知识库' : '添加知识库'}
      eyebrow={kind === 'local' ? '索引服务器目录' : '连接第三方检索接口'}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="knowledge-form"
            loading={saving}
          >
            {mode === 'edit' ? '保存修改' : '创建知识库'}
          </Button>
        </>
      }
    >
      <form
        id="knowledge-form"
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
            {kind === 'local'
              ? '索引服务器目录中的 PDF、DOCX、TXT、MD、XLSX；扫描件不会自动 OCR。'
              : '第三方检索接口返回的片段只作参考，事实台账始终优先。'}
          </span>
        </div>

        <div className="admin-form-grid">
          <div className="form-field">
            <label htmlFor="kb-name">
              知识库名称 <span className="required-mark">必填</span>
            </label>
            <input
              id="kb-name"
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：历史智能化投标方案"
              maxLength={120}
              data-autofocus
              disabled={saving}
            />
            {touched && !name.trim() && <span className="field-error">请填写知识库名称。</span>}
          </div>

          <div className="form-field">
            <label htmlFor="kb-kind">类型</label>
            <select
              id="kb-kind"
              className="select"
              value={kind}
              onChange={(event) => setKind(event.target.value === 'api' ? 'api' : 'local')}
              disabled={saving}
            >
              <option value="local">本地目录</option>
              <option value="api">第三方接口</option>
            </select>
          </div>

          {kind === 'api' && (
            <div className="form-field">
              <label htmlFor="kb-provider">接口类型</label>
              <select
                id="kb-provider"
                className="select"
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
                disabled={saving}
              >
                {apiProviders.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
              <p className="field-hint">
                通用 API 需要返回 JSON 片段；Dify 与 RAGFlow 需要数据集 ID。
              </p>
            </div>
          )}

          <div className="form-field admin-form-span">
            <label htmlFor="kb-location">
              {kind === 'local' ? '服务器目录路径' : 'API Base URL'}{' '}
              <span className="required-mark">必填</span>
            </label>
            <div className="admin-location-input">
              {kind === 'local' ? <FolderOpen size={15} /> : <Database size={15} />}
              <input
                id="kb-location"
                className="input mono"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                placeholder={
                  kind === 'local'
                    ? '/srv/bidcraft/knowledge/tender-archive'
                    : 'https://api.example.com/v1'
                }
                disabled={saving}
              />
            </div>
            <p className="field-hint">
              {kind === 'local'
                ? '填写服务器上的绝对路径；索引只读取该目录下受支持的文件。'
                : '填写接口根地址，不要包含具体的检索路径。'}
            </p>
            {touched && !location.trim() && (
              <span className="field-error">
                请填写{kind === 'local' ? '目录路径' : 'API 地址'}。
              </span>
            )}
          </div>

          {kind === 'api' && (
            <div className="form-field admin-form-span">
              <label htmlFor="kb-api-key">
                API 密钥
                {mode === 'edit' && <span className="field-inline-status">留空保持不变</span>}
              </label>
              <div className="admin-key-input">
                <KeyRound size={15} aria-hidden />
                <input
                  id="kb-api-key"
                  className="input mono"
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={
                    mode === 'edit' && kb?.has_api_key
                      ? '已配置，留空不改；填写则覆盖'
                      : '按接口要求填写；本地接口可留空'
                  }
                  autoComplete="new-password"
                  disabled={saving || clearKey}
                />
                {kb?.has_api_key && mode === 'edit' && (
                  <Badge tone="success" subtle>
                    已配置
                  </Badge>
                )}
              </div>
              {mode === 'edit' && kb?.has_api_key && (
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
          )}

          {kind === 'api' && needsDataset && (
            <div className="form-field admin-form-span">
              <label htmlFor="kb-dataset">
                数据集 ID <span className="required-mark">必填</span>
              </label>
              <input
                id="kb-dataset"
                className="input mono"
                value={datasetId}
                onChange={(event) => setDatasetId(event.target.value)}
                placeholder="Dify / RAGFlow 的数据集标识"
                disabled={saving}
              />
              <p className="field-hint">
                {kbProviderLabel(provider)} 通过数据集 ID 定位要检索的文档集合。
              </p>
            </div>
          )}

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
                <strong>启用此知识库</strong>
                <small>停用后不会参与生成时的检索。</small>
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
                <strong>设为默认知识库</strong>
                <small>同一时间只保留一个默认检索源。</small>
              </span>
            </label>
          </div>
        </div>

        <details className="admin-advanced">
          <summary>高级参数（JSON）</summary>
          <p className="field-hint">
            可选，写入 options 字段；用于第三方接口的额外检索参数。
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
      </form>
    </Modal>
  )
}
