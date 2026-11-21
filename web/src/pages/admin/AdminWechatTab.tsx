import { useEffect, useState } from 'react'
import { MessageCircle } from 'lucide-react'
import type { WechatConfig } from '../../lib/types'
import { getWechatConfig, testWechatConfig, updateWechatConfig } from '../../api/account'
import { messageOf } from '../../lib/http'
import { useQuery } from '../../lib/hooks'
import { useToast } from '../../context/useToast'
import { Button } from '../../components/Button'
import { AdminActionError, AdminSectionLabel } from './AdminShared'

export function AdminWechatTab() {
  const toast = useToast()
  const query = useQuery('admin:wechat', getWechatConfig)
  const cfg: WechatConfig | undefined = query.data
  const [appId, setAppId] = useState('')
  const [secret, setSecret] = useState('')
  const [clearSecret, setClearSecret] = useState(false)
  const [redirect, setRedirect] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [mock, setMock] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!cfg) return
    setAppId(cfg.app_id)
    setRedirect(cfg.redirect_uri)
    setEnabled(cfg.enabled)
    setMock(cfg.mock)
    setSecret('')
    setClearSecret(false)
  }, [cfg])

  const save = async () => {
    setSaving(true); setError(null)
    try {
      const next = await updateWechatConfig({
        app_id: appId, redirect_uri: redirect, enabled, mock,
        ...(secret ? { app_secret: secret } : {}),
        ...(clearSecret ? { clear_secret: true } : {}),
      })
      query.setData(next); setSecret(''); setClearSecret(false)
      toast.success('微信配置已保存', next.available ? '登录页会显示微信扫码登录' : '已保存，但配置尚不完整')
    } catch (err) { setError(messageOf(err)) } finally { setSaving(false) }
  }

  const runTest = async () => {
    setTesting(true); setError(null); setResult(null)
    try { setResult(await testWechatConfig()) }
    catch (err) { setError(messageOf(err)) }
    finally { setTesting(false) }
  }

  return (
    <div className="admin-packages">
      <div className="admin-tab-head">
        <AdminSectionLabel
          eyebrow="扫码登录"
          title="微信开放平台"
          description="配置网站应用的 AppID / AppSecret / 授权回调域；保存后登录页立即出现微信扫码登录。模拟模式不联网，用于本机演示。"
          aside={
            <Button variant="secondary" loading={testing} onClick={() => void runTest()}>
              测试连通性
            </Button>
          }
        />
      </div>
      {error && <AdminActionError message={error} onClose={() => setError(null)} />}
      <div className="admin-callout admin-callout--ok">
        <MessageCircle size={18} />
        <div>
          <strong>
            {cfg?.available ? '微信扫码登录已可用' : '尚未启用'}
            {cfg?.mock ? ' · 模拟模式' : ''}
          </strong>
          <p>
            配置来源：{cfg?.source === 'db' ? '管理控制台' : '环境变量'}
            {cfg?.has_secret ? ' · 密钥已加密保存' : ' · 未设置密钥'}
          </p>
        </div>
      </div>
      <div className="admin-form admin-wechat-form">
        <div className="form-field">
          <label htmlFor="wechat-appid">AppID</label>
          <input id="wechat-appid" className="input mono" value={appId} maxLength={120} autoComplete="off"
            placeholder="wx…" onChange={(e) => setAppId(e.target.value)} />
        </div>
        <div className="form-field">
          <label htmlFor="wechat-secret">AppSecret</label>
          <input id="wechat-secret" className="input mono" type="password" value={secret} autoComplete="new-password"
            placeholder={cfg?.has_secret ? '已保存，留空不修改' : '未设置'} onChange={(e) => setSecret(e.target.value)} />
          <label className="checkbox-inline">
            <input type="checkbox" checked={clearSecret} onChange={(e) => setClearSecret(e.target.checked)} />
            清除已保存的密钥
          </label>
        </div>
        <div className="form-field">
          <label htmlFor="wechat-redirect">授权回调地址</label>
          <input id="wechat-redirect" className="input" value={redirect} maxLength={500}
            placeholder="https://你的域名/auth/wechat/callback" onChange={(e) => setRedirect(e.target.value)} />
          <p className="field-hint">需与开放平台「授权回调域」一致，且为公网 HTTPS 地址。</p>
        </div>
        <div className="admin-switch-row">
          <label className="checkbox-inline">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            启用微信扫码登录
          </label>
          <label className="checkbox-inline">
            <input type="checkbox" checked={mock} onChange={(e) => setMock(e.target.checked)} />
            模拟模式（不联网）
          </label>
        </div>
        <div className="admin-form-actions">
          <Button variant="primary" loading={saving} disabled={saving || testing} onClick={() => void save()}>
            保存配置
          </Button>
        </div>
        {result && (
          <p className={result.ok ? 'admin-test-ok' : 'admin-test-bad'} role="status">
            {result.ok ? '测试通过：' : '测试未通过：'}{result.message}
          </p>
        )}
        <p className="admin-footnote">
          密钥经本机密钥加密后存库，接口不回显明文；未启用时登录页不显示微信入口。
        </p>
      </div>
    </div>
  )
}
