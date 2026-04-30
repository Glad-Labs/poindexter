# Glad Labs Public Site

Public content website built with Next.js 15 and Tailwind CSS.

**Version:** 0.1.0
**Stack:** Next.js 15 (App Router) + React 18 + Tailwind CSS
**Port:** 3000

## Quick Start

```bash
# From monorepo root
npm install

# Start all services (backend required for content)
npm run dev

# Or public site only (needs backend running on :8002)
npm run dev:public
```

## Architecture

This is a **headless content consumer** — all content is fetched from the FastAPI backend at build/request time. There are no local markdown files.

```
web/public-site/
├── app/                         # Next.js 15 App Router
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
│   ├── AdSenseScript.tsx        # Google AdSense
│   ├── CookieConsentBanner.jsx  # Cookie consent
│   ├── GiscusComments.tsx       # GitHub Discussions comments
│   ├── NewsletterModal.tsx      # Newsletter subscription
│   ├── StructuredData.tsx       # JSON-LD structured data
│   └── WebVitals.tsx            # Core Web Vitals → Sentry
├── lib/                         # Utilities
│   ├── api-fastapi.js           # Backend API client (primary)
│   ├── posts.ts                 # Post types/interfaces
│   ├── url.js                   # URL helpers
│   ├── seo.js                   # Metadata generation
│   ├── structured-data.js       # JSON-LD generators
│   ├── content-utils.js         # Content processing
│   ├── error-handling.js        # Error handling
│   └── logger.js                # Client-side logging
├── styles/globals.css           # Tailwind global styles
├── e2e/                         # Playwright E2E tests (~16 specs)
├── next.config.js               # Next.js config (331 lines)
└── tailwind.config.cjs
```

## Content Source

All content comes from the FastAPI backend via `lib/api-fastapi.js`:

```
GET /api/posts           → {posts: [...], total, offset, limit}
GET /api/posts/{slug}    → Single post with HTML content
GET /api/categories      → {data: [...]}
GET /api/tags            → {data: [...]}
GET /api/posts/search    → Search results
```

Data flow:

1. `generateStaticParams()` fetches post slugs at build time
2. Pages are statically generated with ISR for updates
3. No client-side API calls — all content is server-rendered

## Environment Variables

```env
# web/public-site/.env.local
NEXT_PUBLIC_API_BASE_URL=http://localhost:8002
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8002
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```

Production builds validate that `NEXT_PUBLIC_API_BASE_URL` is set and not `localhost` (enforced in `next.config.js`).

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

## Testing

- **Unit tests:** Jest + React Testing Library (`lib/__tests__/`, `app/page.test.js`)
- **E2E tests:** Playwright (`e2e/` — 16 spec files covering home, posts, legal, auth, tags, authors, accessibility)

```bash
# E2E (requires backend + frontend running)
SKIP_SERVER_START=true npx playwright test --project=chromium
```

## Deployment

Deployed to Vercel via CI. Next.js config uses `output: 'standalone'` for Docker compatibility.

Security headers (HSTS, CSP, XSS protection) configured in `next.config.js`.

## Resources

- [System Architecture](../../docs/02-Architecture/System-Design.md)
- [Development Workflow](../../docs/04-Development/Development-Workflow.md)
- [Operations Guide](../../docs/05-Operations/Operations-Maintenance.md)
