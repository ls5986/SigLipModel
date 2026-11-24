import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({root:'frontend',base:'./',plugins:[react()],build:{outDir:'../pi-studio-dist',emptyOutDir:true,rollupOptions:{input:'frontend/pi-studio-preview.html'}}});
