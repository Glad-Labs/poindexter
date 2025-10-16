// Vite config override for Strapi v5 to fix date-fns module resolution
// This resolves the date-fns v3/v4 compatibility issue with Vite/Rollup

export default {
  build: {
    commonjsOptions: {
      esmExternals: true,
    },
  },
  optimizeDeps: {
    include: ['date-fns'],
  },
  resolve: {
    alias: {
      // Fix date-fns deep imports
      'date-fns/format': 'date-fns/esm/format/index.js',
      'date-fns/_lib/cloneObject': 'date-fns/esm/_lib/cloneObject/index.js',
    },
  },
};
