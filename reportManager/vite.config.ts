import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { readFileSync } from 'fs';
const PKG_VERSION = JSON.parse(readFileSync(path.resolve(__dirname, 'package.json'), 'utf-8')).version;

export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(PKG_VERSION) },
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, '../neotec_insight/public/insight'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/index-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  server: {
    port: 8080,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/method': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
});
