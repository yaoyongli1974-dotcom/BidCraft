import { useState } from 'react'
import { Download, FileSpreadsheet, ShieldAlert, Trash2 } from 'lucide-react'
import { Button } from '../components/Button'
import { useToast } from '../context/useToast'
import {
  deleteAccount,
  downloadAccountExport,
  downloadStatement,
  getBillingProfile,
  saveBillingProfile,
  messageOf,
} from '../lib/api'
import { useEffect } from 'react'

export default function AccountPage() {
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [taxNo, setTaxNo] = useState('')
  const [email, setEmail] = useState('')
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getBillingProfile()
      .then((profile) => {
        setTitle(profile.title)
        setTaxNo(profile.tax_no)
        setEmail(profile.email)
      })
      .catch(() => undefined)
  }, [])

  async function saveProfile() {
    setSaving(true)
    setError('')
    try {
      await saveBillingProfile({ title, tax_no: taxNo, email })
      toast.success('开票信息已保存')
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  async function exportData() {
    setExporting(true)
    try {
      await downloadAccountExport()
      toast.success('数据导出已开始下载')
    } catch (err) {
      toast.error(messageOf(err))
    } finally {
      setExporting(false)
    }
  }

  async function removeAccount() {
    if (!window.confirm('注销后账号与名下项目将被删除，且不可恢复。确定继续？')) return
    setDeleting(true)
    setError('')
    try {
      await deleteAccount(password, confirm)
      toast.success('账号已注销')
      window.location.replace('/login')
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="account-page">
      <h1>账号与数据</h1>

      <section className="account-card">
        <h2>数据导出</h2>
        <p>导出名下项目的提纲、正文、资质证据与个人文档，打包为 ZIP。</p>
        <div className="account-actions">
          <Button variant="secondary" icon={<Download size={14} />} loading={exporting} onClick={exportData}>
            导出我的数据
          </Button>
          <Button variant="ghost" icon={<FileSpreadsheet size={14} />} onClick={() => downloadStatement()}>
            下载对账单（CSV）
          </Button>
        </div>
      </section>

      <section className="account-card">
        <h2>开票信息</h2>
        <div className="account-form">
          <label>
            发票抬头
            <input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            纳税人识别号
            <input value={taxNo} maxLength={40} onChange={(e) => setTaxNo(e.target.value)} />
          </label>
          <label>
            接收邮箱
            <input value={email} maxLength={120} onChange={(e) => setEmail(e.target.value)} />
          </label>
        </div>
        <div className="account-actions">
          <Button loading={saving} onClick={saveProfile}>
            保存开票信息
          </Button>
        </div>
      </section>

      <section className="account-card account-danger">
        <h2>
          <ShieldAlert size={16} /> 注销账号
        </h2>
        <p>注销会删除账号、名下项目、个人文档、资质与人员台账，且不可恢复。存在子账号时需先处理。</p>
        <div className="account-form">
          <label>
            登录密码
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label>
            输入「注销」确认
            <input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="注销" />
          </label>
        </div>
        <div className="account-actions">
          <Button
            variant="danger"
            icon={<Trash2 size={14} />}
            loading={deleting}
            onClick={removeAccount}
          >
            永久注销账号
          </Button>
        </div>
      </section>

      {error && <p className="form-alert">{error}</p>}
      <style>{`
        .account-page { max-width: 820px; margin: 0 auto; padding: 32px 20px 64px; display: grid; gap: 18px; }
        .account-page h1 { font-size: 22px; margin: 0 0 4px; }
        .account-card { border: 1px solid var(--line, #e5e5e5); border-radius: 12px; padding: 18px 20px; background: var(--surface, #fff); }
        .account-card h2 { font-size: 15px; margin: 0 0 8px; display: flex; align-items: center; gap: 6px; }
        .account-card p { color: var(--muted, #666); font-size: 13px; margin: 0 0 12px; }
        .account-form { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-bottom: 12px; }
        .account-form label { display: grid; gap: 4px; font-size: 12px; color: var(--muted, #666); }
        .account-form input { padding: 8px 10px; border: 1px solid var(--line, #ddd); border-radius: 8px; font-size: 13px; }
        .account-actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .account-danger { border-color: color-mix(in srgb, #c0392b 35%, transparent); }
        .legal-page { padding: 40px 20px 80px; }
        .legal-page-inner { max-width: 760px; margin: 0 auto; }
        .legal-nav { display: flex; gap: 14px; margin-bottom: 20px; font-size: 13px; }
        .legal-content h1 { font-size: 24px; margin: 0 0 12px; }
        .legal-content h2 { font-size: 16px; margin: 22px 0 8px; }
        .legal-content p { margin: 0; font-size: 14px; line-height: 1.75; color: var(--text, #222); }
        .legal-gap { height: 8px; }
        .legal-loading { padding: 60px; text-align: center; color: var(--muted, #666); }
      `}</style>
    </div>
  )
}
