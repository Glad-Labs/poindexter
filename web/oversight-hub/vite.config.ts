import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Load .env.local (and .env) so REACT_APP_* vars are available in define block.
  // envPrefix '' loads ALL variables, not just VITE_*.
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [react()],
    server: {
      port: 3001,
      strictPort: false, // Allow fallback if 3001 is in use
      open: false,
    },
    build: {
      outDir: 'build',
      sourcemap: true,
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ['react', 'react-dom', 'react-router-dom'],
            mui: ['@mui/material', '@mui/icons-material'],
            utils: ['axios', 'zustand'],
          },
        },
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    esbuild: {
      loader: 'jsx',
      include: /src\/.*\.jsx?$/,
      exclude: [],
    },
    optimizeDeps: {
      esbuildOptions: {
        loader: {
          '.js': 'jsx',
        },
      },
    },
    define: {
      // Expose REACT_APP_* env vars to browser code via process.env shim.
      // loadEnv reads .env.local so these are always populated in dev.
      'process.env': JSON.stringify(
        Object.fromEntries(
          Object.entries(env).filter(
            ([key]) => key.startsWith('REACT_APP_') || key === 'NODE_ENV'
          )
        )
      ),
    },
  };
});
