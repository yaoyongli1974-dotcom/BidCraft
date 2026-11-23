import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getAdminHealth } from './admin'
import { issueAdminInvoice, listAdminInvoices, rejectAdminInvoice } from './billing'

class FakeXhr {
  static instances: FakeXhr[] = []
  status = 200
  responseText = '{"id":"inv-1","status":"issued"}'
  withCredentials = false
  responseType = ''
  upload: { onprogress?: (event: ProgressEvent) => void } = {}
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  method = ''
  url = ''
  body: unknown = null

  constructor() {
    FakeXhr.instances.push(this)
  }
  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  send(body?: unknown) {
    this.body = body
  }
  abort() {
    this.onabort?.()
  }
  respond(status: number, text: string) {
    this.status = status
    this.responseText = text
    this.onload?.()
  }
}

function fetchMock(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: '',
    json: async () => body,
    text: async () => JSON.stringify(body),
  })
}

describe('admin API clients', () => {
  beforeEach(() => {
    FakeXhr.instances.length = 0
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists invoices with an optional status filter', async () => {
    const fetch = fetchMock([{ id: 'inv-1' }])
    vi.stubGlobal('fetch', fetch)
    expect(await listAdminInvoices()).toEqual([{ id: 'inv-1' }])
    expect(fetch.mock.calls[0][0]).toBe('/admin/invoices')
    await listAdminInvoices('issued')
    expect(fetch.mock.calls[1][0]).toBe('/admin/invoices?status=issued')
  })

  it('rejects an invoice with a reason', async () => {
    const fetch = fetchMock({ id: 'inv-1', status: 'rejected' })
    vi.stubGlobal('fetch', fetch)
    await rejectAdminInvoice('inv-1', '信息有误')
    expect(fetch.mock.calls[0][0]).toBe('/admin/invoices/inv-1/reject')
    expect(fetch.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ reason: '信息有误' })
  })

  it('uploads an invoice file as multipart form data', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const file = new File(['pdf'], 'invoice.pdf', { type: 'application/pdf' })
    const pending = issueAdminInvoice('inv-1', 'FP-1', file)
    const xhr = FakeXhr.instances[0]
    expect(xhr.url).toBe('/admin/invoices/inv-1/issue')
    expect(xhr.method).toBe('POST')
    const form = xhr.body as FormData
    expect(form.get('invoice_no')).toBe('FP-1')
    expect((form.get('file') as File).name).toBe('invoice.pdf')
    xhr.respond(200, '{"id":"inv-1","status":"issued"}')
    expect(await pending).toEqual({ id: 'inv-1', status: 'issued' })
  })

  it('reads the full admin health payload', async () => {
    const fetch = fetchMock({ version: '0.27.0', status: 'ok' })
    vi.stubGlobal('fetch', fetch)
    expect(await getAdminHealth()).toEqual({ version: '0.27.0', status: 'ok' })
    expect(fetch.mock.calls[0][0]).toBe('/admin/health')
  })
})
