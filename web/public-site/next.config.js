/** @type {import('next').NextConfig} */

// ── Build-time environment validation ──────────────────────────────────────
// Runs when Next.js boots (`next build` and `next dev`).
// Production builds fail fast if the API URL is missing or invalid.
(function validateEnv() {
  const IS_PROD = process.env.NODE_ENV === 'production';
  const raw =
    process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_FASTAPI_URL;

  if (!raw) {
    if (IS_PROD) {
      throw new Error(
        '\n[next.config] NEXT_PUBLIC_API_BASE_URL is required for production builds.\n' +
          'Set it in your Vercel/Railway environment config or .env.local.\n'
      );
    }
    return; // dev: runtime url.js will use localhost fallback
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(
      `\n[next.config] NEXT_PUBLIC_API_BASE_URL="${raw}" is not a valid URL.\n`
    );
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(
      `\n[next.config] NEXT_PUBLIC_API_BASE_URL="${raw}" must use http or https (got ${parsed.protocol}).\n`
    );
  }

  const isLocalhost =
    parsed.hostname === 'localhost' ||
    parsed.hostname === '127.0.0.1' ||
    parsed.hostname === '0.0.0.0';

  if (IS_PROD && isLocalhost) {
    throw new Error(
      `\n[next.config] NEXT_PUBLIC_API_BASE_URL="${raw}" points to localhost in production.\n` +
        'Set a real backend URL in your environment config.\n'
    );
  }
})();

const nextConfig = {
  // Image Optimization Configuration
  images: {
    // Supported image formats with automatic optimization
    formats: ['image/avif', 'image/webp'],

    // Use remotePatterns instead of deprecated domains property
    remotePatterns: [
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '8000',
        pathname: '/**',
      },
      {
        protocol: 'http',
        hostname: 'localhost',
        pathname: '/**',
      },
      {
        protocol: 'https',
        hostname: 'via.placeholder.com',
        pathname: '/**',
      },
      {
        protocol: 'https',
        hostname: 'res.cloudinary.com',
        pathname: '/**',
      },
      {
        protocol: 'https',
        hostname: 'cdn.example.com',
        pathname: '/**',
      },
      {
        protocol: 'https',
        hostname: 'pexels.com',
        pathname: '/**',
      },
      {
        protocol: 'https',
        hostname: 'images.pexels.com',
        pathname: '/**',
      },
    ],

    // Image size optimization
    deviceSizes: [640, 750, 828, 1080, 1200, 1920, 2048, 3840],
    imageSizes: [16, 32, 48, 64, 96, 128, 256, 384],

    // Optimize static image imports
    disableStaticImages: false,
  },

  // Security Headers for Content-Type validation
  headers: async () => {
    return [
      {
        source: '/:path*',
        headers: [
          // HSTS - Enforce HTTPS
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains; preload',
          },
          // Prevent content-type sniffing
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
          // Prevent clickjacking
          {
            key: 'X-Frame-Options',
            value: 'SAMEORIGIN',
          },
          // Enable XSS filter in browsers
          {
            key: 'X-XSS-Protection',
            value: '1; mode=block',
          },
          // Content Security Policy - Prevent XSS and injection attacks
          {
            key: 'Content-Security-Policy',
            value:
              "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.googletagmanager.com https://pagead2.googlesyndication.com https://giscus.app; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://giscus.app; img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' http://localhost:8000 https://www.google-analytics.com https://cofounder-production.up.railway.app https://api.railway.app; frame-src 'self' https://pagead2.googlesyndication.com https://giscus.app;",
          },
          // Control referrer information
          {
            key: 'Referrer-Policy',
            value: 'strict-origin-when-cross-origin',
          },
          // Feature Policy / Permissions-Policy
          {
            key: 'Permissions-Policy',
            value:
              'camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()',
          },
          // Enable DNS prefetch for performance
          {
            key: 'X-DNS-Prefetch-Control',
            value: 'on',
          },
        ],
      },
      // Cache images for 1 year
      {
        source: '/images/:path*',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=31536000, immutable',
          },
        ],
      },
      // Cache assets for 30 days
      {
        source: '/_next/static/:path*',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=2592000, immutable',
          },
        ],
      },
      // Don't cache HTML (always fresh)
      {
        source: '/:path((?!_next/static).*)',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=0, must-revalidate',
          },
        ],
      },
    ];
  },

  // Redirects for URL structure changes
  redirects: async () => {
    return [
      // Redirect old blog URLs to new structure if needed
      // {
      //   source: '/blog/:slug',
      //   destination: '/posts/:slug',
      //   permanent: true,
      // },
    ];
  },

  // Rewrites for API proxy (optional)
  rewrites: async () => {
    return {
      beforeFiles: [
        // Example: proxy API calls
        // {
        //   source: '/api/strapi/:path*',
        //   destination: `${process.env.NEXT_PUBLIC_STRAPI_API_URL}/:path*`,
        // },
      ],
    };
  },

  /*
  // Webpack configuration for additional optimizations
  webpack(config, { isServer }) {
    // config.optimization.minimize = true;
    config.watchOptions = {
      ignored:
        /node_modules|\.next|\.swc|\.git|dist|build|trace|\.vercel|coverage/,
      poll: false,
      aggregateTimeout: 300,
    };
    return config;
  },
  */

  // Disable Fast Refresh rebuild detection for .next folder changes
  onDemandEntries: {
    maxInactiveAge: 60 * 1000, // Keep for 60 seconds
  },

  // Environment variables exposed to browser
  env: {
    NEXT_PUBLIC_FASTAPI_URL: process.env.NEXT_PUBLIC_FASTAPI_URL,
    // Disable Next.js telemetry to prevent trace file generation
    NEXT_TELEMETRY_DISABLED: '1',
  },

  // ESLint configuration
  eslint: {
    dirs: ['app', 'components', 'lib', 'styles'],
  },

  // TypeScript configuration
  typescript: {
    tsconfigPath: './tsconfig.json',
  },

  // Experimental: Optimize package imports
  experimental: {
    // optimizePackageImports: ['components', 'lib'],
  },

  // Compression configuration
  compress: true,

  // Generate etags for cache validation
  generateEtags: true,

  // Production source maps (set to false to reduce bundle size in production)
  productionBrowserSourceMaps: false,

  // Internationalization (if needed later)
  // i18n: {
  //   locales: ['en', 'es', 'fr'],
  //   defaultLocale: 'en',
  // },

  // Trailing slashes (set to false for clean URLs)
  trailingSlash: false,

  // React strict mode - disabled for smoother dev experience
  reactStrictMode: false,
};

export default nextConfig;
