/** @type {import('next').NextConfig} */
// Imported directly. This used to be a lazy import wrapped in try/catch,
// because @sentry/nextjs was hoisted to the root node_modules while `next`
// stayed nested in web/public-site — so Sentry's `require('next/constants')`
// walked up from the hoisted location and found nothing. That broke every
// Vercel build between PR #97 and PR #148 (Apr 30 - May 1), and the catch was
// added to keep deploys moving.
//
// The hoisting is fixed as of #2886: `next` is declared in the ROOT
// devDependencies, so it hoists to the root node_modules and Sentry resolves
// it from anywhere in the tree. The old comment's premise is now inverted —
// next is hoisted and Sentry is the nested one.
//
// Restoring a hard import on purpose: the catch degraded silently, so a
// resolution regression would have shipped a production site with NO error
// tracking and only a build-log line nobody reads. Failing the build is the
// louder, correct behaviour (CLAUDE.md "fail loud + notify"). If this ever
// throws again, fix the hoisting rather than re-adding the catch.
import { withSentryConfig } from '@sentry/nextjs';
// With the extension: Node's ESM loader runs this file and does not add it.
import { STATIC_ORIGIN } from './lib/static-url.js';

// Derive safe origins for the CSP connect-src directive from env vars.
// Uses URL().origin to strip paths and reject semicolons that could inject CSP directives.
//
// There is deliberately no backend (FastAPI worker) origin here, and no
// build-time backend URL at all. The worker is local-first with no public
// ingress, so neither Vercel nor a visitor's browser can reach it: content
// comes from the R2 static export, and newsletter signups go to Resend.
// NEXT_PUBLIC_API_BASE_URL used to be required for production builds and
// put its origin in connect-src, which on Vercel was a retired node's
// tailnet name that no longer resolved.

// Static JSON/image CDN (R2). Its origin is allowed in connect-src so that a
// browser-side fetch of posts/index.json isn't blocked (without it /search
// silently returned zero results, Gitea #262), and next/image may optimise the
// images it holds (images.remotePatterns below).
//
// STATIC_ORIGIN comes from lib/static-url.js, the module every page, route and
// the edge proxy takes its fetch URL from, so the allow-list and the fetch
// target move together. While each held its own copy of the fallback the two
// could drift apart, and when they did /search broke with no error.
const staticBucket = new URL(STATIC_ORIGIN);

// First-party page-view beacon (Cloudflare Worker). The browser blocks the
// ViewTracker beacon unless the Worker's origin is in connect-src, so derive
// it from the SAME env var ViewTracker POSTs to — keeping the allow-list and
// the beacon target from drifting apart. (The 2026-06 page_views outage:
// NEXT_PUBLIC_BEACON_URL pointed cross-origin but wasn't in connect-src, so
// every beacon was CSP-blocked.) Tolerate a scheme-less value (prepend https)
// so a bare-host env var resolves to a real origin instead of throwing.
const cspBeaconOrigin = (() => {
  const raw = process.env.NEXT_PUBLIC_BEACON_URL;
  if (!raw) return '';
  const withScheme = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
  try {
    return new URL(withScheme).origin;
  } catch {
    return '';
  }
})();

// Error relay (the sentry-relay Cloudflare Worker). The Sentry SDK's `tunnel`
// POSTs every envelope to this origin, so connect-src must allow it or the
// browser blocks each send. Derived from the SAME env var the SDK reads
// (lib/sentry-options.ts), the lesson of the beacon outage above. Until
// 2026-09-28 no Sentry origin was here at all.
const sentryDsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
const sentryTunnel = process.env.NEXT_PUBLIC_SENTRY_TUNNEL;
const cspSentryTunnelOrigin = (() => {
  if (!sentryTunnel) return '';
  try {
    return new URL(sentryTunnel).origin;
  } catch {
    return '';
  }
})();

// Fail a production build on a half-configured relay. The SDK initializes
// only with both values, so one without the other would ship a site whose
// error pages say "we've been notified" while every report is dropped, with
// nothing anywhere saying so.
(function validateSentryRelayEnv() {
  if (process.env.NODE_ENV !== 'production') return;
  if (Boolean(sentryDsn) !== Boolean(sentryTunnel)) {
    throw new Error(
      '\n[next.config] NEXT_PUBLIC_SENTRY_DSN and NEXT_PUBLIC_SENTRY_TUNNEL must be set together.\n' +
        'Set both (DSN https://<key>@<relay host>/<project id>, tunnel https://<relay host>/relay),\n' +
        'or neither to build without error reporting.\n'
    );
  }
  if (sentryTunnel && !cspSentryTunnelOrigin) {
    throw new Error(
      `\n[next.config] NEXT_PUBLIC_SENTRY_TUNNEL="${sentryTunnel}" is not a valid URL.\n`
    );
  }
  // @sentry/core's DSN_REGEX allows only word characters in the public key.
  // GlitchTip stores keys as dashed UUIDs, and a DSN built from that form is
  // rejected at runtime with a console-only "Invalid Sentry Dsn": the SDK
  // never starts and nothing else says so. Use the dashless hex key.
  if (sentryDsn && !/^https?:\/\/\w+@[\w.-]+(?::\d+)?\/\d+$/.test(sentryDsn)) {
    throw new Error(
      '\n[next.config] NEXT_PUBLIC_SENTRY_DSN is not a DSN the Sentry SDK accepts.\n' +
        'Expected https://<key>@<relay host>/<project id>, with the key as dashless hex\n' +
        '(the SDK rejects the dashed UUID form).\n'
    );
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
      // The static export bucket (lib/static-url.js). It holds the images as
      // well as the JSON. The wildcard below covers any *.r2.dev bucket, but
      // not a custom domain the bucket moves to.
      {
        protocol: staticBucket.protocol.replace(':', ''),
        hostname: staticBucket.hostname,
        pathname: '/**',
      },
      {
        protocol: 'https',
        hostname: 'startupfa.me',
        pathname: '/**',
      },
      // Wildcard pattern covering any *.r2.dev public bucket domain so
      // next/image can optimise images served from any R2 account-hash
      // subdomain (poindexter#732).
      {
        protocol: 'https',
        hostname: '**.r2.dev',
        pathname: '/**',
      },
      // Custom image CDN domain (storage_image_custom_domain).
      // Set NEXT_PUBLIC_IMAGE_CDN_HOST to activate (e.g. images.gladlabs.io).
      ...(process.env.NEXT_PUBLIC_IMAGE_CDN_HOST
        ? [
            {
              protocol: 'https',
              hostname: process.env.NEXT_PUBLIC_IMAGE_CDN_HOST,
              pathname: '/**',
            },
          ]
        : [
            // Fallback: always allow the known Glad Labs image CDN domain so
            // next/image works even before NEXT_PUBLIC_IMAGE_CDN_HOST is set.
            {
              protocol: 'https',
              hostname: 'images.gladlabs.io',
              pathname: '/**',
            },
          ]),
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
                `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === 'development' ? " 'unsafe-eval'" : ''} https://www.googletagmanager.com https://pagead2.googlesyndication.com https://giscus.app https://static.cloudflareinsights.com https://lmsqueezy.com https://assets.lemonsqueezy.com`,
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://giscus.app",
                "img-src 'self' data: https:",
                "font-src 'self' data: https://fonts.gstatic.com",
                `connect-src 'self' ${STATIC_ORIGIN}${cspBeaconOrigin ? ' ' + cspBeaconOrigin : ''}${cspSentryTunnelOrigin ? ' ' + cspSentryTunnelOrigin : ''} https://www.google-analytics.com https://app.lemonsqueezy.com https://gladlabs.lemonsqueezy.com https://ep1.adtrafficquality.google`,
                "frame-src 'self' https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net https://giscus.app https://app.lemonsqueezy.com https://gladlabs.lemonsqueezy.com",
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
      // RFC 8288 Link headers for agent discovery — advertises the API catalog,
      // auth documentation, and MCP server card to crawlers and AI agents.
      // Applied to all HTML pages so any entry point surfaces the discovery chain.
      {
        source: '/:path*',
        headers: [
          {
            key: 'Link',
            value: [
              '</.well-known/api-catalog>; rel="api-catalog"',
              '</auth.md>; rel="describedby"',
              '</.well-known/agent-skills/index.json>; rel="service-desc"',
              '</.well-known/mcp/server-card.json>; rel="service-desc"',
            ].join(', '),
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
      // Cache built assets for 30 days, in production only. `immutable` is
      // right there because chunk URLs carry a content hash. `next dev` serves
      // the same chunks under stable URLs (chunks/app/layout.js), so this
      // header would make the browser keep the first bundle it saw across
      // reloads and edits would look like they did nothing. Next warns about
      // it at every dev start: it flags any Cache-Control set on a `/_next/`
      // source. Left alone, dev answers `no-cache, must-revalidate` itself.
      ...(process.env.NODE_ENV === 'production'
        ? [
            {
              source: '/_next/static/:path*',
              headers: [
                {
                  key: 'Cache-Control',
                  value: 'public, max-age=2592000, immutable',
                },
              ],
            },
          ]
        : []),
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
      // gladlabs.io is the blog/proof; the product/store lives on gladlabs.ai.
      // The old on-blog storefront at /product is consolidated there so there's
      // exactly one store. 308 permanent — passes link equity to the store.
      {
        source: '/product',
        destination: 'https://www.gladlabs.ai',
        permanent: true,
      },
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
      // Legacy WordPress privacy URLs → the real page at /legal/privacy.
      // (Both previously pointed at /privacy, which 404s — caught in the
      // 2026-06-04 SEO indexing audit.)
      {
        source: '/privacy-policy',
        destination: '/legal/privacy',
        permanent: true,
      },
      {
        source: '/privacy-policy-2',
        destination: '/legal/privacy',
        permanent: true,
      },
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

  // Environment variables exposed to browser.
  //
  // NEXT_PUBLIC_GA_ID: the Vercel dashboard value takes precedence when set.
  // The fallback (G-NJMBCYNDWN) is the known Glad Labs GA4 measurement ID —
  // it is non-secret (visible in every gtag URL) and kept here so the
  // analytics tag fires even when the dashboard env var is absent.
  // Tracks poindexter#672 (GA4 data collection was silently disabled because
  // the variable was missing from the Vercel project env).
  env: {
    NEXT_PUBLIC_GA_ID: process.env.NEXT_PUBLIC_GA_ID || 'G-NJMBCYNDWN',
    // Disable Next.js telemetry to prevent trace file generation
    NEXT_TELEMETRY_DISABLED: '1',
  },

  // TypeScript configuration
  typescript: {
    tsconfigPath: './tsconfig.json',
  },

  // Experimental: Optimize package imports
  experimental: {
    // Barrel-optimize the design-system package so importing { Button, Card }
    // from '@glad-labs/brand' only pulls those modules, not the whole barrel
    // (#979). Safe because the package is effectively side-effect-free — its
    // package.json "sideEffects" only lists the token CSS, and src/index.js is
    // pure named re-exports.
    optimizePackageImports: ['@glad-labs/brand'],
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

// Wrap with Sentry only when the relay is configured (validated above), so a
// build without error reporting (local runs, forks) skips the SDK's webpack
// plugin. The options keep the build self-contained: no source-map upload,
// no release creation and no build telemetry. Each of those calls sentry.io,
// which this site does not use; its errors go to a self-hosted GlitchTip.
//
// There is deliberately no `tunnelRoute`. The same-origin `/monitoring`
// rewrite it creates only works for sentry.io DSNs (the SDK matches
// `o<org>.ingest.sentry.io` and leaves any other DSN untunnelled), so for a
// GlitchTip DSN it never did anything. The relay is the tunnel.
export default sentryDsn && sentryTunnel
  ? withSentryConfig(nextConfig, {
      silent: true,
      telemetry: false,
      sourcemaps: { disable: true },
      // Tag events with the deploy's commit so GlitchTip says which build
      // an error came from. `create: false` alone would leave the name
      // unset: the SDK only derives one when it may create the release.
      release: { create: false, name: process.env.VERCEL_GIT_COMMIT_SHA },
      // Navigation tracing is off (errors only), so there is deliberately no
      // onRouterTransitionStart hook in instrumentation-client.ts; exporting
      // one would pull the routing instrumentation back into every page.
      suppressOnRouterTransitionStartWarning: true,
      webpack: {
        treeshake: { removeDebugLogging: true, removeTracing: true },
      },
    })
  : nextConfig;
