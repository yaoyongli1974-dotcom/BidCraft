import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default tseslint.config(
  {
    ignores: ['dist', 'node_modules', '../bidcraft/web_dist'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // The two stable hook rules. The newer compiler-oriented rules in the
      // plugin's `recommended` preset assume a different data-fetching style
      // than this app's effect-based `useQuery`; enabling them here would only
      // add noise instead of catching regressions.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // TypeScript already resolves globals and reports unused bindings.
      'no-undef': 'off',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
    },
  },
  {
    files: ['src/App.tsx'],
    rules: {
      // Router config file: it owns the lazy page table and the route error
      // views on purpose, so fast-refresh granularity does not apply.
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    files: ['*.config.js', 'eslint.config.js'],
    languageOptions: {
      sourceType: 'module',
      globals: { process: 'readonly' },
    },
  },
)
