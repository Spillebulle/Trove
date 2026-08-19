import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      // The dev server talks to the API on 8080. The live view is a WebSocket
      // on the same prefix, so it needs `ws: true` or the upgrade request is
      // proxied as an ordinary GET and the socket closes before a frame
      // arrives.
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // noVNC 1.7 (the screen view's RFB client) uses top-level await, which
    // Vite's default es2020 target refuses. Every browser that can open this
    // app can do es2022.
    target: 'es2022',
  },
})
