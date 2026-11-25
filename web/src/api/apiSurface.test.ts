import { describe, expect, it } from 'vitest'
import viteConfigSource from '../../vite.config.ts?raw'

// Representative request literals from `web/src/api/*.ts` (collapsed to their
// root namespace). The Python counterpart `tests/test_batch8_contract.py`
// derives the same set from source; this test guards the Vite half locally.
const API_LITERALS = [
  '/health',
  '/packages',
  '/projects',
  '/projects/ref/commercial',
  '/projects/ref/similarity/reports',
  '/projects/ref/workflows',
  '/my/docs',
  '/my/qualifications',
  '/jobs/job-1',
  '/batch-jobs/job-1/download',
  '/auth/login',
  '/auth/me',
  '/account/billing-profile',
  '/account/sub-accounts',
  '/approvals/pending',
  '/legal/terms',
  '/billing/summary',
  '/billing/invoices',
  '/billing/statement',
  '/admin/health',
  '/admin/invoices',
  '/admin/models',
  '/admin/kbs/kb-1/assets',
  '/admin/kbs/kb-1/assets/doc-1/extract',
  '/admin/kbs/kb-1/assets/doc-1/review',
  '/admin/kbs/kb-1/assets/duplicates',
  '/admin/kbs/kb-1/assets/duplicates/group-1/resolve',
  '/agents',
  '/enterprise',
  '/enterprise/e-1/assets',
  '/enterprise/e-1/assets/doc-1/extract',
  '/enterprise/e-1/assets/doc-1/review',
  '/enterprise/e-1/assets/duplicates',
  '/enterprise/e-1/assets/duplicates/group-1/resolve',
] as const

// React Router pages that are not API namespaces.
const SPA_LITERALS = ['/app', '/login', '/register', '/my-docs', '/qualifications'] as const

function namespace(path: string): string {
  const match = /^\/([A-Za-z0-9_-]+)/.exec(path)
  if (!match) throw new Error(`Not an absolute path: ${path}`)
  return `/${match[1]}`
}

function declaredNamespaces(): Set<string> {
  const block = /apiProxyNamespaces\s*=\s*\[([\s\S]*?)\]\s*as\s+const/.exec(viteConfigSource)
  if (!block) throw new Error('vite.config.ts must export apiProxyNamespaces')
  const values = Array.from(block[1].matchAll(/['"`](\/[A-Za-z0-9_-]+)['"`]/g)).map(
    (match) => match[1],
  )
  return new Set(values)
}

describe('vite dev proxy API surface', () => {
  it('maps every frontend API literal namespace to a proxy entry', () => {
    const declared = declaredNamespaces()
    for (const literal of API_LITERALS) {
      expect(declared.has(namespace(literal)), `${literal} -> ${namespace(literal)}`).toBe(true)
    }
  })

  it('builds server.proxy from the declared namespace list', () => {
    expect(viteConfigSource).toContain('apiProxyNamespaces.map')
    expect(viteConfigSource).toContain('proxy,')
  })

  it('does not proxy client-only routes', () => {
    const declared = declaredNamespaces()
    for (const path of SPA_LITERALS) {
      expect(declared.has(namespace(path))).toBe(false)
    }
  })

  it('lets HTML navigation on shared root routes reach the SPA fallback', () => {
    expect(viteConfigSource).toContain('spaNavigationBypass')
    expect(viteConfigSource).toContain("accept.includes('text/html')")
    // `Accept: */*` is used by binary downloads; it must still be proxied.
    expect(viteConfigSource).not.toContain("accept === '*/*'")
  })
})
