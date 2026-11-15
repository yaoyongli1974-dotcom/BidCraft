import { useEffect, useState } from 'react'
import { Ruler } from 'lucide-react'
import type { QuotaInput, QuotaItem } from '../../lib/types'
import { createQuota, messageOf, updateQuota } from '../../lib/api'
import { className } from '../../lib/format'
import { Modal } from '../../components/Modal'
import { Button } from '../../components/Button'

export type QuotaModalState = { mode: 'create' } | { mode: 'edit'; quota: QuotaItem }

const FIELD_MAX = {
  name: 120,
  spec: 200,
  unit: 20,
  category: 40,
  note: 200,
} as const

interface AdminQuotaModalProps {
  open: boolean
  state: QuotaModalState
  onClose: () => void
  onSaved: (quota: QuotaItem, created: boolean) => void
}

/** 新建 / 编辑一条企业定额。名称与单价是后端强校验的两项，这里先做一次本地兜底。 */
export function AdminQuotaModal({ open, state, onClose, onSaved }: AdminQuotaModalProps) {
  const editing = state.mode === 'edit' ? state.quota : undefined

  const [name, setName] = useState('')
  const [spec, setSpec] = useState('')
  const [unit, setUnit] = useState('')
  const [price, setPrice] = useState('')
  const [category, setCategory] = useState('')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setSaving(false)
    setError(null)
    setTouched(false)
    setName(editing?.name ?? '')
    setSpec(editing?.spec ?? '')
    setUnit(editing?.unit ?? '')
    setPrice(editing ? String(editing.unit_price) : '')
    setCategory(editing?.category ?? '')
    setNote(editing?.note ?? '')
  }, [open, editing])

  const parsedPrice = price.trim() === '' ? null : Number(price)
  const priceInvalid = parsedPrice !== null && (!Number.isFinite(parsedPrice) || parsedPrice < 0)
  const nameMissing = !name.trim()
  const canSubmit = !nameMissing && parsedPrice !== null && !priceInvalid

  const submit = async () => {
    setTouched(true)
    if (nameMissing) return
    if (parsedPrice === null) {
      setError('请填写定额单价。')
      return
    }
    if (!Number.isFinite(parsedPrice) || parsedPrice < 0) {
      setError('定额单价必须是不小于 0 的数字。')
      return
    }
    const payload: QuotaInput = {
      name: name.trim(),
      spec: spec.trim(),
      unit: unit.trim(),
      unit_price: Math.round(parsedPrice * 10000) / 10000,
      category: category.trim(),
      note: note.trim(),
    }
    setSaving(true)
    setError(null)
    try {
      const saved =
        state.mode === 'edit' && state.quota
          ? await updateQuota(state.quota.id, payload)
          : await createQuota(payload)
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
      title={state.mode === 'edit' ? '编辑定额' : '新增定额'}
      eyebrow="企业定额库"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="quota-form"
            loading={saving}
            disabled={!canSubmit && touched}
          >
            {state.mode === 'edit' ? '保存修改' : '新增定额'}
          </Button>
        </>
      }
    >
      <form
        id="quota-form"
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="admin-form-note">
          <Ruler size={15} aria-hidden />
          <span>
            清单套价只使用这里维护的单价，程序不会自行生成价格。名称与规格越贴近清单写法，匹配越准。
          </span>
        </div>

        <div className="admin-form-grid">
          <div className="form-field admin-form-span">
            <label htmlFor="quota-name">
              定额名称 <span className="required-mark">必填</span>
            </label>
            <input
              id="quota-name"
              className={className('input', touched && nameMissing && 'input--error')}
              value={name}
              onChange={(event) => {
                setName(event.target.value)
                if (error) setError(null)
              }}
              placeholder="例如：六类非屏蔽双绞线 敷设"
              maxLength={FIELD_MAX.name}
              autoComplete="off"
              data-autofocus
              disabled={saving}
            />
            <p className="field-hint">按清单的「名称」列填写，可带施工做法。</p>
            {touched && nameMissing && <span className="field-error">请填写定额名称。</span>}
          </div>

          <div className="form-field admin-form-span">
            <label htmlFor="quota-spec">
              规格型号 <span className="optional-mark">可选</span>
            </label>
            <input
              id="quota-spec"
              className="input"
              value={spec}
              onChange={(event) => setSpec(event.target.value)}
              placeholder="例如：UTP CAT6 4P"
              maxLength={FIELD_MAX.spec}
              autoComplete="off"
              disabled={saving}
            />
            <p className="field-hint">规格一致会在匹配时加分，留空则只看名称。</p>
          </div>

          <div className="form-field">
            <label htmlFor="quota-unit">单位</label>
            <input
              id="quota-unit"
              className="input"
              value={unit}
              onChange={(event) => setUnit(event.target.value)}
              placeholder="m / 个 / 套"
              maxLength={FIELD_MAX.unit}
              autoComplete="off"
              disabled={saving}
            />
          </div>

          <div className="form-field">
            <label htmlFor="quota-price">
              定额单价（元） <span className="required-mark">必填</span>
            </label>
            <input
              id="quota-price"
              className={className(
                'input',
                'input--number',
                priceInvalid && 'input--error',
              )}
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={price}
              onChange={(event) => {
                setPrice(event.target.value)
                if (error) setError(null)
              }}
              placeholder="0.00"
              disabled={saving}
            />
            <p className="field-hint">含税综合单价；必须是不小于 0 的数字。</p>
            {priceInvalid && <span className="field-error">请输入不小于 0 的数字。</span>}
          </div>

          <div className="form-field">
            <label htmlFor="quota-category">
              分类 <span className="optional-mark">可选</span>
            </label>
            <input
              id="quota-category"
              className="input"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              placeholder="例如：综合布线"
              maxLength={FIELD_MAX.category}
              autoComplete="off"
              disabled={saving}
            />
            <p className="field-hint">用于管理端筛选，不参与匹配。</p>
          </div>

          <div className="form-field admin-form-span">
            <label htmlFor="quota-note">
              备注 <span className="optional-mark">可选</span>
            </label>
            <input
              id="quota-note"
              className="input"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="价格来源、适用条件等"
              maxLength={FIELD_MAX.note}
              autoComplete="off"
              disabled={saving}
            />
          </div>
        </div>

        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}
      </form>
    </Modal>
  )
}
