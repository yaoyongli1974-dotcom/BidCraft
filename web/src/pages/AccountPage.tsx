import { useState } from 'react'
import { Download, FileSpreadsheet, ShieldAlert, Trash2 } from 'lucide-react'
import { Button } from '../components/Button'
import { useToast } from '../context/useToast'
import { cancelAccountDeletion, deleteAccount, getDeletionState, downloadAccountExport, downloadStatement, getBillingProfile, saveBillingProfile } from '../api/account'
import { createInvoice, getInvoices, invoiceFileUrl } from '../api/auth'
import { messageOf } from '../lib/http'
import { useEffect } from 'react'
import type { DeletionState } from '../api/account'
import type { InvoiceRequest } from '../api/auth'

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
  const [deletion, setDeletion] = useState<DeletionState | null>(null)
  const [invoices, setInvoices] = useState<InvoiceRequest[]>([])
  const [amountYuan, setAmountYuan] = useState('')
  const [invoiceNote, setInvoiceNote] = useState('')
  const [requesting, setRequesting] = useState(false)

  useEffect(() => {
    void loadInvoices()
  }, [])

  useEffect(() => {
    getDeletionState()
      .then(setDeletion)
      .catch(() => undefined)
  }, [])

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

  async function loadInvoices() {
    try {
      setInvoices(await getInvoices())
    } catch {
      /* 未登录或接口不可用时静默 */
    }
  }

  async function requestInvoice() {
    const amount = Math.round(Number(amountYuan) * 100)
    if (!title.trim()) {
      setError('请填写发票抬头')
      return
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setError('请填写正确的开票金额（元）')
      return
    }
    setRequesting(true)
    setError('')
    try {
      await createInvoice({
        title: title.trim(),
        amount_cents: amount,
        tax_no: taxNo.trim(),
        email: email.trim(),
        note: invoiceNote.trim(),
      })
      setAmountYuan('')
      setInvoiceNote('')
      toast.success('开票申请已提交，管理员开具后可在此下载')
      await loadInvoices()
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setRequesting(false)
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
    if (!window.confirm('提交注销申请后将进入 7 天冷静期，到期才真正删除。确定继续？')) return
    setDeleting(true)
    setError('')
    try {
      const state = await deleteAccount(password, confirm)
      setDeletion(state)
      setPassword('')
      setConfirm('')
      toast.success('注销申请已提交，冷静期内可随时撤销')
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setDeleting(false)
    }
  }

  async function revokeDeletion() {
    setDeleting(true)
    setError('')
    try {
      const state = await cancelAccountDeletion()
      setDeletion(state)
      toast.success('已撤销注销申请')
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

      <section className="account-card">
        <h2>开票申请</h2>
        <p>提交后由管理员开具，发票文件（PDF/图片）开具后可在此下载。</p>
        <div className="account-form">
          <label>
            开票金额（元）
            <input
              inputMode="decimal"
              value={amountYuan}
              onChange={(e) => setAmountYuan(e.target.value)}
              placeholder="例如 39.90"
            />
          </label>
          <label>
            备注
            <input value={invoiceNote} maxLength={200} onChange={(e) => setInvoiceNote(e.target.value)} />
          </label>
        </div>
        <div className="account-actions">
          <Button loading={requesting} onClick={requestInvoice}>
            提交开票申请
          </Button>
        </div>
        {invoices.length > 0 && (
          <table className="account-invoice-table">
            <thead>
              <tr>
                <th>抬头</th>
                <th>金额</th>
                <th>状态</th>
                <th>发票</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((row) => (
                <tr key={row.id}>
                  <td>{row.title}</td>
                  <td>¥{(row.amount_cents / 100).toFixed(2)}</td>
                  <td>
                    <span className={`invoice-status invoice-status--${row.status}`}>
                      {row.status_label || row.status}
                    </span>
                    {row.status === 'rejected' && row.reject_reason ? (
                      <span className="invoice-reason">{row.reject_reason}</span>
                    ) : null}
                  </td>
                  <td>
                    {row.has_file ? (
                      <a href={invoiceFileUrl(row.id)} target="_blank" rel="noreferrer">
                        下载
                      </a>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="account-card account-danger">
        <h2>
          <ShieldAlert size={16} /> 注销账号
        </h2>
        {deletion?.pending ? (
          <>
            <p>
              注销申请已提交，<strong>{deletion.days_left}</strong> 天后（{deletion.due_at?.slice(0, 10)}）执行删除。
              冷静期内账号仍可正常使用，可随时撤销。
            </p>
            <div className="account-actions">
              <Button variant="secondary" loading={deleting} onClick={revokeDeletion}>
                撤销注销申请
              </Button>
            </div>
          </>
        ) : (
          <>
        <p>
          提交后进入 {deletion?.cooling_off_days ?? 7} 天冷静期，到期才删除账号、名下项目、个人文档、资质与人员台账；
          冷静期内可撤销。存在子账号时需先处理。
        </p>
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
            提交注销申请
          </Button>
        </div>
          </>
        )}
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
