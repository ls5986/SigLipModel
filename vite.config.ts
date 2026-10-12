import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  root: 'frontend',
  plugins: [react()],
  build: { outDir: '../frontend-dist', emptyOutDir: true, assetsDir: 'assets' },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8765' } },
});
