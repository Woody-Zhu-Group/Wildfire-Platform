import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The accounts build is served from the origin root, where routes such as
// /access-status load the same index.html; the Pages build stays relative.
export default defineConfig(({ mode }) => ({
  base: mode === 'accounts' ? '/' : './',
  publicDir: false,
  plugins: [react(), tailwindcss()],
}));
