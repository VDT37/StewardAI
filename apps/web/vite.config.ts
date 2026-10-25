import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Steward serves the built console at /console. In development, `pnpm dev` proxies the admin
// API to a Steward on localhost:8000 (for example through the SSH tunnel).
export default defineConfig({
  base: '/console/',
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { '/admin': 'http://localhost:8000' },
  },
})
