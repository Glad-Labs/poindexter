import { defineConfig } from 'vite';
import path from 'path';

export default defineConfig({
  resolve: {
    alias: {
      '@strapi/admin/strapi-admin': path.resolve(__dirname, './admin-fix.mjs'),
    },
  },
});
