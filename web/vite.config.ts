import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The API is served by the same origin in production. During development every
// root-level API namespace is proxied to the local FastAPI process.
const apiTarget = process.env.BIDCRAFT_API_TARGET || 'http://127.0.0.1:8000'

const proxy = Object.fromEntries(
  [
    '/health',
    '/packages',
    '/my',
    '/projects',
    '/enterprise',
    '/commercial',
    '/similarity',
    '/jobs',
    '/api',
    '/auth',
    '/billing/summary',
    '/billing/orders',
    '/billing/redeem',
    '/billing/wechat',
    '/admin/models',
    '/admin/kbs',
    '/admin/users',
    '/admin/billing',
    '/admin/redeem-codes',
    '/admin/rule-packages',
    '/admin/quotas',
    '/admin/textqa',
    '/admin/plugins',
    '/admin/audit',
  ].map((path) => [path, { target: apiTarget, changeOrigin: true }]),
)

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: '../bidcraft/web_dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          icons: ['lucide-react'],
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
