import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // All API calls and auth routes go to Flask
      '/api': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/nba/props/research': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/login': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/signup': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/logout': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/my-bets': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/settings': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/admin': { target: 'http://127.0.0.1:5001', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
