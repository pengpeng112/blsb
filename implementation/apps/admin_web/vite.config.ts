import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // R35：核心API把构建产物挂在 /ui（StaticFiles html=True）。
  // base 必须是 /ui/，否则 index.html 引用 /assets/*.js 会打到核心API根 → 404。
  base: '/ui/',
  server: {
    port: 5173,
    proxy: {
      '/core': { target: 'http://127.0.0.1:8000', changeOrigin: true, rewrite: p => p.replace(/^\/core/, '') },
      '/storage': { target: 'http://127.0.0.1:8001', changeOrigin: true, rewrite: p => p.replace(/^\/storage/, '') },
      '/exchange': { target: 'http://127.0.0.1:8002', changeOrigin: true, rewrite: p => p.replace(/^\/exchange/, '') }
    }
  },
  build: { outDir: 'dist' }
})
