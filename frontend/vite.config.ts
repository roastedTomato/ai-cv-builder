import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';

export default defineConfig(({ command }) => ({
  plugins: [vue()],
  define: { 'process.env.NODE_ENV': JSON.stringify(command === 'build' ? 'production' : 'development') },
  build: {
    outDir: fileURLToPath(new URL('../web/static/vue/', import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
    lib: {
      entry: fileURLToPath(new URL('./src/main.ts', import.meta.url)),
      name: 'CvBuilderApp',
      formats: ['iife'],
      fileName: () => 'app.js',
    },
  },
}));
