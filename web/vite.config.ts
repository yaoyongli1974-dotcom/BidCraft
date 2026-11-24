import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The API is served by the same origin in production. During development every
// root-level API namespace is proxied to the local FastAPI process.
const apiTarget = process.env.BIDCRAFT_API_TARGET || 'http://127.0.0.1:8000'

/**
 * Root-level API namespaces exposed by FastAPI (see `bidcraft/api/routes/`).
 * The desktop gateway keeps the same surface in `desktop.PROXY_PREFIXES`;
 * `tests/test_batch8_contract.py` guards both layers against drift.
 */
export const apiProxyNamespaces = [
  '/health',
  '/packages',
  '/my',
  '/projects',
  '/enterprise',
  '/commercial',
  '/similarity',
  '/evaluation',
  '/orchestration',
  '/agents',
  '/collaboration',
  '/jobs',
  '/batch-jobs',
  '/api',
  '/auth',
  '/account',
  '/approvals',
  '/legal',
  '/billing',
  '/admin',
] as const

/**
 * `/admin`, `/billing`, `/account` and `/legal` are both SPA routes and API
 * namespaces. A browser navigation (Accept: text/html) must reach Vite's
 * history fallback so a hard refresh still renders the React app; API calls
 * carry `Accept: application/json` and are proxied to the backend.
 */
function spaNavigationBypass() {
  return (
    req: { method?: string; headers: Record<string, unknown>; url?: string },
  ): string | undefined => {
    const accept = String(req.headers.accept ?? '')
    const navigational = req.method === 'GET' || req.method === 'HEAD'
    // Only real browser navigations carry `text/html`; leave `Accept: */*`
    // (binary downloads via requestBlob) to the proxy.
    if (navigational && accept.includes('text/html')) {
      return req.url ?? '/'
    }
    return undefined
  }
}

const proxy = Object.fromEntries(
  apiProxyNamespaces.map((path) => [
    path,
    { target: apiTarget, changeOrigin: true, bypass: spaNavigationBypass() },
  ]),
)

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: '../bidcraft/web_dist',
    emptyOutDir: true,
    // Vite 8 bundles with Rolldown: the object form of Rollup's
    // `output.manualChunks` is gone, so the two vendor chunks are declared as
    // Rolldown code-splitting groups instead (same names and contents).
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'react-vendor',
              test: /node_modules[\\/](?:react|react-dom|react-router|react-router-dom|scheduler)[\\/]/,
              priority: 2,
            },
            {
              name: 'icons',
              test: /node_modules[\\/]lucide-react[\\/]/,
              priority: 2,
            },
          ],
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy,
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
