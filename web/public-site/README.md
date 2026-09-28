# Glad Labs Public Site

Public content website built with Next.js 16 and Tailwind CSS.

**Version:** 0.1.0
**Stack:** Next.js 16 (App Router) + React 19 + Tailwind CSS
**Port:** 3000

## Quick Start

```bash
# From monorepo root
npm install

# Backend (:8002) and public site together
npm run dev

# Or the public site alone. It reads the R2 static export, so it needs no backend.
npm run dev:public
```

## Architecture

This is a **headless content consumer**: all content comes from the static JSON export the content pipeline pushes to R2 (see Content Source below). It never calls the FastAPI worker, which is local-first with no public ingress. There are no local markdown files.

```
web/public-site/
├── app/                         # Next.js 16 App Router
│   ├── layout.js                # Root layout
│   ├── page.js                  # Homepage
│   ├── error.tsx                # Error boundary
│   ├── not-found.tsx            # 404 page
│   ├── robots.ts                # robots.txt
│   ├── sitemap.ts               # XML sitemap
│   ├── posts/[slug]/page.tsx    # Post detail (SSG + ISR)
│   ├── category/[slug]/page.tsx # Category archive
│   ├── tag/[slug]/page.tsx      # Tag archive
│   ├── author/[id]/page.tsx     # Author profile
│   ├── archive/[page]/page.tsx  # Paginated archive
│   ├── legal/                   # Privacy, terms, cookies, data requests
│   └── api/posts/               # API routes for post data
├── components/                  # React components
│   ├── AdUnit.tsx               # Google AdSense in-content ad slot
│   ├── CookieConsentBanner.jsx  # Cookie consent (consent-gated GA + AdSense loaders)
│   ├── GiscusWrapper.tsx        # GitHub Discussions comments
│   ├── NewsletterModal.tsx      # Newsletter subscription
│   ├── StructuredData.tsx       # JSON-LD structured data
│   └── WebVitals.tsx            # Core Web Vitals → Sentry
├── lib/                         # Utilities
│   ├── posts.ts                 # Static R2 post client + types (primary)
│   ├── seo.js                   # Metadata generation
│   ├── structured-data.js       # JSON-LD generators
│   ├── site.config.js           # Site name + URL
│   └── logger.js                # Client-side logging
├── styles/globals.css           # Tailwind global styles
├── e2e/                         # Playwright E2E tests (~16 specs)
├── next.config.js               # Next.js config (CSP + security headers, redirects, images)
└── tailwind.config.cjs
```

## Content Source

All content comes from static JSON on R2/CDN via `lib/posts.ts` — the content
pipeline pushes updated JSON on every publish and fires `revalidateTag('posts')`:

```
GET {STATIC_URL}/posts/index.json   → {posts: [...]}
GET {STATIC_URL}/posts/{slug}.json  → Single post with HTML content
GET {STATIC_URL}/categories.json    → {categories: [...]}
GET {STATIC_URL}/sitemap.json       → {urls: [...]}
```

Data flow:

1. `generateStaticParams()` fetches post slugs at build time
2. Pages are statically generated with ISR for updates
3. No client-side API calls — all content is server-rendered

## Environment Variables

All optional for local dev; `.env.example` describes them.

```env
# web/public-site/.env.local
NEXT_PUBLIC_SITE_URL=http://localhost:3000
# R2 static export; defaults to the Glad Labs bucket when unset
NEXT_PUBLIC_STATIC_URL=https://<bucket>.r2.dev/static
```

There is no backend URL. `NEXT_PUBLIC_API_BASE_URL` (and its older name `NEXT_PUBLIC_FASTAPI_URL`) used to be required for production builds; nothing reads it now.

## Key Features

- **Static Generation** with ISR — pages pre-built, content refreshed in background
- **SEO** — Dynamic meta tags, Open Graph, Twitter Cards, XML sitemap, JSON-LD
- **Performance** — Image optimization (AVIF/WebP), code splitting, security headers
- **Comments** — Giscus (GitHub Discussions-powered)
- **Analytics** — Google Analytics, AdSense, Sentry error tracking, Web Vitals

## Development

```bash
npm run dev          # Dev server with hot reload
npm run build        # Production build
npm run start        # Start production server
npm run lint         # ESLint
npm run test         # Jest unit tests
```

`next dev` and `next build` keep `tsconfig.json` in step with what Next needs
(`moduleResolution: bundler`, `jsx: react-jsx`, the `.next/dev/types`
include). The committed file already holds those values, so a run leaves it
untouched. If `git status` shows it modified after a run, a Next upgrade
changed its requirements: commit what Next wrote. `npx tsc --noEmit` type-checks
without a build.

## Testing

- **Unit tests:** Jest + React Testing Library (co-located `*.test.*` files and `__tests__/` dirs)
- **E2E tests:** Playwright (`e2e/` — 15 spec files covering home, posts, legal, auth, tags, authors, accessibility)

```bash
# E2E against a site that is already running (the auth and task specs also need the FastAPI worker)
SKIP_SERVER_START=true npx playwright test --project=chromium
```

## Deployment

Deployed to Vercel via CI. Next.js config uses `output: 'standalone'` for Docker compatibility.

Security headers (HSTS, CSP, XSS protection) configured in `next.config.js`.

The 30-day `immutable` `Cache-Control` on `/_next/static/*` is sent in
production only. `next dev` serves those chunks under unhashed URLs, and an
`immutable` header makes the browser keep the first bundle it saw across
reloads, so edits look like they did nothing.
`__tests__/next-config-headers.test.js` pins this.

## Resources

- [System Architecture](../../docs/architecture/overview.md)
- [Development Workflow](../../docs/operations/local-development-setup.md)
- [Operations Guide](../../docs/operations/index.mdx)
