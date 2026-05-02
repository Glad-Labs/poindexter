/** @type {import('next').NextConfig} */
// Lazy-import Sentry so the build still works when its module resolution
// breaks. Background: @sentry/nextjs gets hoisted to root node_modules by
// npm workspaces, but `next` stays nested in web/public-site/node_modules
// (because web/storefront also depends on `next`, npm keeps per-workspace
// copies). When Sentry's `isBuild.js` does `require('next/constants')`,
// Node's module resolution walks up from the hoisted location and can't
// find Next — `Cannot find module 'next/constants'`. This crashed every
// Vercel build between PR #97 (Apr 30) and PR #148 (May 1) — gladlabs.io
// was stuck on a 3-day-old deploy.
//
// Until the hoisting is properly fixed (separate follow-up: bump Sentry
// to v11+ which supports Next 16, OR add `next` to root devDependencies
// to force hoist parity), gracefully skip Sentry when it can't load.
let withSentryConfig = null;
try {
  ({ withSentryConfig } = await import('@sentry/nextjs'));
} catch (err) {
  // eslint-disable-next-line no-console
  console.warn(
    '[next.config] @sentry/nextjs failed to load — building without Sentry wrapping.',
    err?.message || err,
  );
}

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
          'Set it in your Vercel environment config or .env.local.\n'
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

  // Allow localhost in production builds only when explicitly opted out (e.g. local `npm run build` testing)
  const skipLocalhostCheck = process.env.SKIP_ENV_VALIDATION === 'true';
  if (IS_PROD && isLocalhost && !skipLocalhostCheck) {
    throw new Error(
      `\n[next.config] NEXT_PUBLIC_API_BASE_URL="${raw}" points to localhost in production.\n` +
        'Set a real backend URL in your environment config.\n' +
        'To bypass this check locally, set SKIP_ENV_VALIDATION=true.\n'
    );
  }
})();

// Derive safe origins for the CSP connect-src directive from env vars.
// Uses URL().origin to strip paths and reject semicolons that could inject CSP directives.
const cspBackendOrigin = (() => {
  const raw =
    process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_FASTAPI_URL;
  if (raw) {
    try {
      return new URL(raw).origin;
    } catch {
      return '';
    }
  }
  return process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '';
})();

// Static JSON/image CDN (R2) — the search page and any other client-side
// consumer of posts/index.json fetches from here. Without it in connect-src
// the browser blocks the fetch and /search silently returns zero results
// (Gitea #262).
//
// Mirrors the same fallback used by client code when NEXT_PUBLIC_STATIC_URL
// isn't set (see app/page.js, app/search/page.jsx, lib/posts.ts) — otherwise
// a missing Vercel env var silently re-breaks /search because the fetch URL
// and the CSP allow-list drift apart.
const STATIC_URL_FALLBACK =
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';
const cspStaticOrigin = (() => {
  const raw = process.env.NEXT_PUBLIC_STATIC_URL || STATIC_URL_FALLBACK;
  try {
    return new URL(raw).origin;
  } catch {
    return '';
  }
})();

const nextConfig = {
  // Standalone output for Docker deployments — produces .next/standalone with a self-contained server.js
  output: 'standalone',

  // Image Optimization Configuration
  //
  // CVE-2026-27980 (GHSA-3x4c-7xq6-9pq8): Unbounded next/image disk cache
  // growth can exhaust storage. Fixed in Next.js 16.1.7 via LRU eviction +
  // images.maximumDiskCacheSize. On 15.x the config knob doesn't exist, so we
  // mitigate by constraining variant cardinality: fewer deviceSizes/imageSizes
  // and an explicit minimumCacheTTL to reduce churn. Also restrict qualities
  // to the default set (75) to prevent attackers varying the `q` parameter.
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
        hostname: 'res.cloudinary.com',
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
      {
        protocol: 'https',
        hostname: 'pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev',
        pathname: '/**',
      },
    ],

    // Reduced variant cardinality to mitigate CVE-2026-27980 disk cache
    // exhaustion. Only the sizes we actually serve — fewer combinations means
    // a bounded cache even without LRU eviction.
    deviceSizes: [640, 828, 1200, 1920],
    imageSizes: [32, 64, 128, 256],

    // Lock quality to a single value so the `q` query param cannot be varied
    // to generate unbounded cache entries. (Next.js 15.3+ supports `qualities`.)
    qualities: [75],

    // Keep optimized images cached for 24 h to reduce regeneration churn
    minimumCacheTTL: 86400,

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
          // Prevent clickjacking — DENY because this site should never be framed.
          // Aligned with vercel.json which also sets DENY.
          {
            key: 'X-Frame-Options',
            value: 'DENY',
          },
          // Disable legacy XSS auditor — modern browsers removed it; setting to 1 can
          // introduce new vulnerabilities in older browsers. Backend already sets this to 0.
          {
            key: 'X-XSS-Protection',
            value: '0',
          },
          // Content Security Policy - Prevent XSS and injection attacks.
          //
          // Note on 'unsafe-inline' in script-src: GTM, AdSense, and Giscus inject inline
          // scripts that require this directive. To remove it, implement nonce-based CSP via
          // middleware.ts (see Next.js docs on CSP with nonces). Tracked in issue #740.
          //
          // 'unsafe-eval' is required in development for Next.js React Refresh (HMR).
          // It is stripped in production builds automatically.
          {
            key: 'Content-Security-Policy',
            value:
              [
                "default-src 'self'",
                `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === 'development' ? " 'unsafe-eval'" : ''} https://www.googletagmanager.com https://pagead2.googlesyndication.com https://giscus.app https://va.vercel-scripts.com https://static.cloudflareinsights.com https://lmsqueezy.com https://assets.lemonsqueezy.com`,
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://giscus.app",
                "img-src 'self' data: https:",
                "font-src 'self' data: https://fonts.gstatic.com",
                `connect-src 'self'${cspBackendOrigin ? ' ' + cspBackendOrigin : ''}${cspStaticOrigin ? ' ' + cspStaticOrigin : ''} https://www.google-analytics.com https://va.vercel-scripts.com https://vitals.vercel-insights.com https://app.lemonsqueezy.com https://gladlabs.lemonsqueezy.com`,
                "frame-src 'self' https://pagead2.googlesyndication.com https://giscus.app https://app.lemonsqueezy.com https://gladlabs.lemonsqueezy.com",
              ].join('; ') + ';',
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
          // Cross-Origin-Opener-Policy — prevent cross-origin window references
          {
            key: 'Cross-Origin-Opener-Policy',
            value: 'same-origin',
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
      // Common URLs Google tries that don't exist — redirect to real pages
      { source: '/blog', destination: '/archive/1', permanent: true },
      { source: '/blog/:slug', destination: '/posts/:slug', permanent: true },
      // /posts now has its own page.tsx — no redirect needed
      { source: '/tags', destination: '/archive/1', permanent: true },
      { source: '/archive', destination: '/archive/1', permanent: true },
      { source: '/page/:num', destination: '/archive/:num', permanent: true },
      // Old WordPress URLs from Search Console — redirect valuable ones
      { source: '/2025/:path*', destination: '/archive/1', permanent: true },
      // Category, tag, and author pages now have real routes — no redirect needed
      { source: '/es/:path*', destination: '/', permanent: true },
      { source: '/contact-us', destination: '/about', permanent: true },
      { source: '/my-account', destination: '/', permanent: true },
      { source: '/sample-page', destination: '/', permanent: true },
      { source: '/privacy-policy-2', destination: '/privacy', permanent: true },
      // WordPress artifacts — redirect to homepage
      { source: '/woocommerce-placeholder', destination: '/', permanent: true },
      { source: '/site-logo', destination: '/', permanent: true },
      { source: '/feed', destination: '/', permanent: true },
      { source: '/feed/:path*', destination: '/', permanent: true },
      {
        source: '/gemini_generated_image:path(.*)',
        destination: '/',
        permanent: true,
      },
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

  // ESLint configuration — ignore during Vercel builds (CI runs lint separately)
  eslint: {
    dirs: ['app', 'components', 'lib', 'styles'],
    ignoreDuringBuilds: true,
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

  // Hide X-Powered-By header for security
  poweredByHeader: false,

  // React strict mode enabled — catches data mutation bugs and unsafe lifecycle patterns.
  // Double-render warnings should be fixed, not suppressed globally.
  reactStrictMode: true,
};

// Only apply Sentry wrapping if DSN is configured; otherwise pass through unchanged.
// This prevents build overhead and telemetry when Sentry is not yet set up.
const hasSentryDsn =
  Boolean(process.env.SENTRY_DSN) ||
  Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN);

export default (hasSentryDsn && withSentryConfig)
  ? withSentryConfig(nextConfig, {
      // Suppress Sentry CLI output during builds
      silent: true,
      // Only upload source maps in production
      disableServerWebpackPlugin: process.env.NODE_ENV !== 'production',
      disableClientWebpackPlugin: process.env.NODE_ENV !== 'production',
      // Automatically tree-shake Sentry logger statements
      disableLogger: true,
      // Use a tunneling route to avoid ad-blocker interference
      tunnelRoute: '/monitoring',
    })
  : nextConfig;
