import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  base: '/static/',
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      // 代理 API 请求到 Flask 后端
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true
      },
      // 代理 Socket.IO
      '/socket.io': {
        target: 'http://localhost:5000',
        changeOrigin: true,
        ws: true
      }
    }
  },
  build: {
    outDir: '../static/dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'echarts': ['echarts'],
          'socket.io': ['socket.io-client']
        }
      }
    }
  }
})
