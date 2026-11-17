import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// QITS Parking backend runs on :8000
// AI Vision / CCTV backend runs on :8001
const QITS_BACKEND = 'http://127.0.0.1:8002';
const AI_BACKEND = 'http://127.0.0.1:8001';

const sharedProxy: Record<string, any> = {
  // ── AI Vision & CCTV routes (more specific, matched first) ──
  '/api/parking': { target: AI_BACKEND, changeOrigin: true },
  '/api/detect': { target: AI_BACKEND, changeOrigin: true },
  '/api/ocr': { target: AI_BACKEND, changeOrigin: true },
  '/api/camera': { target: AI_BACKEND, changeOrigin: true },
  '/api/captures': { target: AI_BACKEND, changeOrigin: true },
  '/captures': { target: AI_BACKEND, changeOrigin: true },

  // ── QITS Parking routes (catch-all /api) ──
  '/api': { target: QITS_BACKEND, changeOrigin: true },
  '/uploads': { target: QITS_BACKEND, changeOrigin: true },
};

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    port: 5173,
    host: true, // Expose on all network interfaces (Hotspot, LAN, Wi-Fi)
    watch: {
      ignored: ['**/dist/**', '**/node_modules/**']
    },
    proxy: sharedProxy,
  },
  preview: {
    port: 5173,
    host: true,
    proxy: sharedProxy,
  },
})
