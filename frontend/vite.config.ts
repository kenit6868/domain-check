/// <reference types="vitest/config" />

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { extensionPackage, extensionVersion } from './tooling/extensionPackage.ts'

export default defineConfig({
  plugins: [react(), tailwindcss(), extensionPackage()],
  define: { 'import.meta.env.VITE_BUNDLED_EXTENSION_VERSION': JSON.stringify(extensionVersion) },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    restoreMocks: true,
  },
})

