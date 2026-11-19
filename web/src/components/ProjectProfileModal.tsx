import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { Modal } from './Modal'
import { Button } from './Button'
import {
  PROFILE_TONES,
  getProjectProfile,
  messageOf,
  saveProjectProfile,
} from '../lib/api'
import type { Project, WritingProfile } from '../lib/types'

interface ProjectProfileModalProps {
  project: Project | null
  onClose: () => void
  onSaved?: () => void
}

const TEXT_FIELDS: Array<{ key: keyof WritingProfile; label: string; hint: string; max: number }> = [
  { key: 'company_name', label: '公司名称', hint: '投标主体全称', max: 80 },
  { key: 'legal_rep', label: '法定代表人', hint: '用于封面与授权书', max: 40 },
  { key: 'project_name', label: '项目名称', hint: '以招标文件为准', max: 120 },
  { key: 'owner_name', label: '招标人', hint: '采购人/建设单位全称', max: 120 },
  { key: 'bid_amount', label: '投标总价', hint: '如 1,286,000 元', max: 40 },
  { key: 'duration_days', label: '工期', hint: '如 120 日历天', max: 20 },
  { key: 'warranty_years', label: '质保期', hint: '如 2 年', max: 20 },
  { key: 'quality_standard', label: '质量标准', hint: '如 合格', max: 80 },
]

const EMPTY: WritingProfile = {
  company_name: '',
  legal_rep: '',
  project_name: '',
  owner_name: '',
  bid_amount: '',
  duration_days: '',
  warranty_years: '',
  quality_standard: '',
  tone: '稳健',
  forbidden_phrases: [],
  extra_facts: [],
}

/** R38 全局事实设定：写入项目的 writing_profile，生成与核对都会使用。 */
export function ProjectProfileModal({ project, onClose, onSaved }: ProjectProfileModalProps) {
  const [draft, setDraft] = useState<WritingProfile>(EMPTY)
  const [phrase, setPhrase] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!project) return
    let alive = true
    setLoading(true)
    setError(null)
    setPhrase('')
    getProjectProfile(project.id)
      .then((data) => {
        if (alive) setDraft(data.profile)
      })
      .catch((err) => {
        if (alive) setError(messageOf(err))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [project])

  const update = <K extends keyof WritingProfile>(key: K, value: WritingProfile[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  const addPhrase = () => {
    const text = phrase.trim().slice(0, 40)
    if (!text) return
    if (draft.forbidden_phrases.includes(text)) {
      setPhrase('')
      return
    }
    update('forbidden_phrases', [...draft.forbidden_phrases, text].slice(0, 50))
    setPhrase('')
  }

  const addFact = () => {
    update('extra_facts', [...draft.extra_facts, { key: '', value: '' }].slice(0, 30))
  }

  const updateFact = (index: number, patch: Partial<{ key: string; value: string }>) => {
    update(
      'extra_facts',
      draft.extra_facts.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    )
  }

  const removeFact = (index: number) => {
    update(
      'extra_facts',
      draft.extra_facts.filter((_, i) => i !== index),
    )
  }

  const submit = async () => {
    if (!project) return
    setSaving(true)
    setError(null)
    try {
      await saveProjectProfile(project.id, draft)
      onSaved?.()
      onClose()
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={project !== null}
      onClose={saving ? () => undefined : onClose}
      title="全局设定"
      eyebrow="生成与核对共用的事实"
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" onClick={submit} loading={saving} disabled={loading}>
            保存设定
          </Button>
        </>
      }
    >
      <p className="field-hint">
        这些事实会注入每一次章节生成；正文写成别的值，核对会报「与全局设定不一致」。留空表示不限定。
      </p>
      <div className="profile-grid">
        {TEXT_FIELDS.map((field) => (
          <div className="form-field" key={field.key}>
            <label htmlFor={`profile-${field.key}`}>{field.label}</label>
            <input
              id={`profile-${field.key}`}
              className="input"
              maxLength={field.max}
              value={String(draft[field.key] ?? '')}
              placeholder={field.hint}
              onChange={(event) => update(field.key, event.target.value as never)}
            />
          </div>
        ))}
        <div className="form-field">
          <label htmlFor="profile-tone">语气</label>
          <select
            id="profile-tone"
            className="input"
            value={draft.tone}
            onChange={(event) => update('tone', event.target.value as WritingProfile['tone'])}
          >
            {PROFILE_TONES.map((tone) => (
              <option key={tone} value={tone}>
                {tone}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="form-field">
        <label htmlFor="profile-phrase">禁用表达</label>
        <div className="profile-phrase-row">
          <input
            id="profile-phrase"
            className="input"
            maxLength={40}
            value={phrase}
            placeholder="如：绝对领先、零故障"
            onChange={(event) => setPhrase(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                addPhrase()
              }
            }}
          />
          <Button variant="secondary" onClick={addPhrase} disabled={!phrase.trim()}>
            添加
          </Button>
        </div>
        {draft.forbidden_phrases.length > 0 && (
          <div className="profile-tags">
            {draft.forbidden_phrases.map((item) => (
              <span className="profile-tag" key={item}>
                {item}
                <button
                  type="button"
                  aria-label={`移除 ${item}`}
                  onClick={() =>
                    update(
                      'forbidden_phrases',
                      draft.forbidden_phrases.filter((value) => value !== item),
                    )
                  }
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="form-field">
        <label>自定义事实</label>
        {draft.extra_facts.map((item, index) => (
          <div className="profile-fact-row" key={`fact-${index}`}>
            <input
              className="input"
              maxLength={40}
              placeholder="键，如 项目经理"
              value={item.key}
              onChange={(event) => updateFact(index, { key: event.target.value })}
            />
            <input
              className="input"
              maxLength={200}
              placeholder="值，如 张三"
              value={item.value}
              onChange={(event) => updateFact(index, { value: event.target.value })}
            />
            <Button variant="ghost" onClick={() => removeFact(index)}>
              删除
            </Button>
          </div>
        ))}
        <Button
          variant="secondary"
          onClick={addFact}
          disabled={draft.extra_facts.length >= 30}
          icon={<Plus size={14} />}
        >
          添加事实
        </Button>
      </div>

      {error && (
        <div className="form-alert" role="alert">
          {error}
        </div>
      )}
    </Modal>
  )
}
