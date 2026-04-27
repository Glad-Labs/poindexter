# Public Site Production-Readiness Audit — 2026-04-27

Scope: `web/public-site/` (Next.js 15 app router serving https://gladlabs.io).
Audited 5 of 10 candidate axes, chosen by expected hit-rate-of-issues:

1. ISR + cache headers
2. Error boundaries
3. Open Graph + Twitter Card metadata
4. Sitemap completeness
5. Image optimization

The remaining 5 axes (accessibility, loading skeletons, 404 handling,
internal-link integrity, CSP/security headers) are noted at the bottom
as "deferred" with one-line state checks but were not exhaustively
audited in this pass.

---

## 1. ISR + cache headers

**Current state.** No page uses `force-dynamic`. All routes either rely
on Next 15 defaults (static + ISR via `fetch` revalidation) or are
explicitly tagged route handlers (`/api/podcast`, `/feed.xml`,
`/podcast-feed.xml`, `/video-feed.xml`). The post page goes a step
further and uses tag-based invalidation (`revalidateTag('post:<slug>')`)
fired by the publish webhook in `app/api/revalidate/route.js`.

**Issues found.**

- **Inconsistent cache strategy on the homepage.** `app/page.js:39-59`
  defines its own local `getPosts()` that fetches `posts/index.json`
  with `next: { revalidate: 300 }` — TTL-based. Meanwhile
  `lib/posts.ts:47-65` (`fetchPostIndex`) fetches the same URL with
  `next: { tags: ['posts', 'post-index'] }` — tag-based, no TTL,
  invalidated on publish. The homepage therefore takes up to 5 minutes
  to reflect a new post, while `/posts`, `/archive/[page]`,
  `/category/[slug]`, `/tag/[slug]`, and `/author/[id]` all reflect it
  instantly. Severity: **medium**. Suggested fix: replace
  `app/page.js` `getPosts()` with `import { getPosts } from
'@/lib/posts'` and consume the same tag-cached fetcher every other
  listing page already uses. Drop the duplicated `STATIC_URL` constant
  too — it's now in three places.

- **Sitemap fetches use `revalidate: 300` not tag-based.**
  `app/sitemap.ts:38-51` revalidates the post index, categories, and
  sitemap fixtures every 5 minutes. This is consistent with itself but
  inconsistent with `lib/posts.ts`. After a publish, the sitemap can
  lag the rest of the site by 5 minutes for crawlers. Severity:
  **low** (Google re-crawls infrequently anyway). Suggested fix: add
  `tags: ['posts']` next to `revalidate: 300` so the publish webhook
  also invalidates the sitemap.

- **Long blanket Cache-Control on HTML.** `next.config.js:248-257`
  sets `Cache-Control: public, max-age=0, must-revalidate` on
  everything that isn't `_next/static`. That's the safe choice but it
  defeats CDN edge caching of ISR HTML. Vercel handles this
  automatically when the rule isn't present, so the current rule is
  more conservative than needed. Severity: **low**. Suggested fix:
  remove the catch-all HTML rule and let Vercel's default ISR caching
  apply (`s-maxage=<revalidate>, stale-while-revalidate`). Verify with
  `curl -I` on a post page after deploy that `s-maxage` shows up.

---

## 2. Error boundaries

**Current state.** Exactly one `error.tsx` exists at the app root
(`app/error.tsx`) with Sentry capture and a recovery UI. There is
also a single `not-found.tsx` at the root. Loading skeletons exist at
the root and at four route levels (`/posts/[slug]`, `/category/[slug]`,
`/tag/[slug]`, `/archive/[page]`).

**Issues found.**

- **No nested `error.tsx` per route group.** A throw in any single
  page component (e.g. an unexpected response shape from
  `getPostBySlug`) takes the user to the root error boundary, which
  loses the layout chrome (`TopNav`, `Footer`) because Next.js
  unmounts everything up to the nearest `error.tsx`. Severity:
  **medium**. Suggested fix: add `app/posts/[slug]/error.tsx`,
  `app/category/[slug]/error.tsx`, `app/tag/[slug]/error.tsx`,
  `app/author/[id]/error.tsx`, and `app/archive/[page]/error.tsx`,
  each a small client component that captures with Sentry, shows a
  scoped error card, and offers a retry button — keeps `TopNav`/
  `Footer` intact.

- **Listing pages swallow errors silently.** `app/category/[slug]/
page.tsx`, `app/tag/[slug]/page.tsx`, `app/author/[id]/page.tsx`,
  and `app/archive/[page]/page.tsx` all `try/catch` with empty
  fallback arrays. The user sees "no articles" whether the API is
  down or the category genuinely has no posts. Severity: **medium**.
  Suggested fix: distinguish the two states the way `app/page.js`
  already does (returns `error: 'network'` vs empty array) and render
  a "Service unavailable" Card on transient failure separately from
  a "No articles yet" empty state. Bonus: log via the shared `logger`
  so Grafana can see frontend fetch failures.

- **404 page does a client-side fetch for suggestions.**
  `app/not-found.tsx:25-39` calls `getPaginatedPosts` from `useEffect`
  on mount. If the user is offline (the very condition that often
  produces a 404), the suggested-posts block stays in its loading
  state forever — `isLoading` is only set false in `finally`, but
  there's no UI for the failure case. Severity: **low**. Suggested
  fix: render an explicit fallback when the fetch errors, or move
  this to a server component and pre-render the top 3 posts at build
  time (they rarely churn).

---

## 3. Open Graph + Twitter Card metadata

**Current state.** Root layout (`app/layout.js`) sets a sensible
OG/Twitter default (image `/og-image.jpg`, site `@_gladlabs`). The
post page (`app/posts/[slug]/page.tsx`) generates per-post OG with
the post's cover image, plus a Twitter `summary_large_image` card.
Listing pages override `title` and `description` via `openGraph` but
historically inherited the root image.

**Issues found.**

- **Category and tag pages did not declare OG/Twitter images
  explicitly.** In Next 15, child route metadata replaces the parent
  `openGraph` object rather than merging — so when `category/[slug]`
  set `openGraph: { title, description }` without `images`, social
  scrapers received an OG card with **no image** for the category
  URL. Tested before fix: same hole on `tag/[slug]`. Severity:
  **high** (visible regression on every share of a category or tag
  link). Suggested fix: explicitly include `images` and the
  `twitter` block on every page that overrides `openGraph`.
  **Fixed in this commit** for `app/category/[slug]/page.tsx` and
  `app/tag/[slug]/page.tsx`.

- **Author and archive pages still missing explicit OG image.**
  `app/author/[id]/page.tsx:34-37` and `app/archive/[page]/layout.tsx:
20-25` set `openGraph: { title, description }` only, no `images`,
  no `twitter:` block. Same root cause as above. Severity: **medium**
  (author/archive are lower-share pages than category/tag).
  Suggested fix: same template — add `images: [{ url: '/og-image.jpg',
width: 1200, height: 630, alt: ... }]` and a `twitter` summary card.

- **Twitter `creator` mismatch.** `app/posts/[slug]/page.tsx:106`
  hardcodes `creator: '@GladLabsAI'` while `app/layout.js:32-33` uses
  `@_gladlabs`. One of these is stale. Severity: **low**. Suggested
  fix: pick the canonical handle (per `site.config.ts`), centralize
  it as a `TWITTER_HANDLE` constant, and use it in both places.

- **`og-image.jpg` is a referenced asset; no audit of its existence
  or dimensions was performed in this pass.** Recommended follow-up:
  verify `web/public-site/public/og-image.jpg` is exactly 1200x630
  and under 5 MB (Twitter's hard limit).

---

## 4. Sitemap completeness

**Current state.** `app/sitemap.ts` reads three static JSON fixtures
from R2 (`posts/index.json`, `categories.json`, `sitemap.json`),
yielding posts + categories + tags + a small set of static + legal
pages.

**Issues found.**

- **Author pages are NOT in the sitemap.** `app/author/[id]/page.tsx`
  exists with `generateStaticParams` returning `poindexter-ai`, but
  `app/sitemap.ts:84-181` never emits `/author/<id>` URLs. Crawlers
  cannot discover the author archive page. Severity: **medium**
  (the page is linked from post bylines so Google will eventually
  find it via crawl, but a sitemap entry is faster and signals
  importance). Suggested fix: in `sitemap.ts`, hardcode the author
  IDs returned by `authorProfiles` (currently just `poindexter-ai`)
  or import them from a shared module. Add a single sitemap entry
  per author.

- **`/archive/2..N` paginated pages are not in the sitemap.** Only
  `/archive/1` is included. With ~46 posts at 10/page that's already
  5 pages; deeper pagination is invisible to crawlers. Severity:
  **low** (paginated archives are low-priority for SEO; the
  individual post pages carry the value). Suggested fix: compute
  `totalPages = Math.ceil(allPosts.length / 10)` and emit
  `/archive/<n>` for n = 1..totalPages. Use a lower priority (0.4)
  to match their actual SEO weight.

- **`/posts` is in the sitemap but `/about` only has `priority: 0.5`
  while `/archive/1` has `priority: 0.8`.** Minor inconsistency; the
  about page should probably be 0.6+ given it's brand-relevant.
  Severity: **low**. Suggested fix: revisit the priority distribution
  once Search Console has 30+ days of impression data and prioritize
  by actual click-through.

- **`changeFrequency: 'daily'` on the homepage is honest, but
  `weekly` on category pages may underrepresent reality.** New posts
  appear in their category continuously. Severity: **low**.
  Suggested fix: bump category `changeFrequency` to `daily` to match
  the homepage.

---

## 5. Image optimization

**Current state.** All production images render through `next/image`.
A grep for raw `<img>` tags in production source returned zero hits
(matches were all in tests, mocks, and HTML sanitizer test fixtures).
`next.config.js:100-154` caps device/image sizes, locks
`qualities: [75]`, and sets `minimumCacheTTL: 86400` to mitigate
CVE-2026-27980. Remote patterns are tightly scoped (R2, Cloudinary,
Pexels, localhost dev).

**Issues found.**

- **Sanitized post HTML can render raw `<img>` tags inside
  `dangerouslySetInnerHTML`.** `app/posts/[slug]/page.tsx:362-385`
  allows `img` in `sanitizeHtml`. Any `<img>` the LLM/editor injects
  into post content bypasses `next/image` — no Vercel optimization,
  no AVIF/WebP fallback, no responsive `sizes`. The Tailwind prose
  class (`prose-img:w-full`) does enforce a 100% width but loads the
  original full-resolution PNG/JPG. Severity: **medium** (page-weight
  hit on posts with embedded images). Suggested fix: post-process
  `contentWithIds` server-side to rewrite `<img src="...">` into
  `<img src="..." loading="lazy" decoding="async">` at minimum, or
  better, into a Cloudinary/Vercel `_next/image` URL. The
  alternative is to ban `<img>` in post content and require a
  Markdown image processor at content-pipeline time that emits a
  custom React component the post page can render server-side.

- **Twitter card image is the absolute path of a remote URL but OG
  image goes through metadataBase.** `app/posts/[slug]/page.tsx:105`
  passes `images: [imageUrl]` (could be a remote R2 URL or a
  relative `/og-image.jpg`) to Twitter; OG passes the same value but
  with explicit width/height. Twitter prefers absolute URLs.
  Severity: **low**. Suggested fix: when `imageUrl` is relative,
  prepend `SITE_URL` before passing to the `twitter.images` array.

- **No `priority` flag audit for above-the-fold images.** The post
  hero correctly uses `priority` (line 224) but home/archive/category/
  tag listing pages don't. The first card in each grid is usually
  above-the-fold on desktop. Severity: **low**. Suggested fix: pass
  `priority` only on the first card in the map (`index === 0`) on
  listing pages — this hints LCP image to the browser preloader.

---

## Quick fixes landed in this commit

- `app/category/[slug]/page.tsx` — explicit OG/Twitter image block
  (high-severity finding).
- `app/tag/[slug]/page.tsx` — explicit OG/Twitter image block + canonical
  alternates (high-severity finding).
- `app/posts/[slug]/loading.jsx` — replaced `Math.random()` skeleton
  widths with deterministic widths to fix React 18 hydration mismatch
  warning.

Everything else is left as-is for follow-up issues.

---

## Deferred axes (not audited in this pass — quick state check only)

- **Accessibility.** Skim shows good `alt`-text discipline (post hero
  uses `alt=""` to avoid double-announce), `aria-label` on social
  share buttons, `aria-hidden` on decorative dividers, semantic
  `<time dateTime=...>`. Color contrast not verified against
  WCAG AA (Matt is red-green colorblind; `var(--gl-cyan)` over
  dark backgrounds should be safe but worth a Lighthouse pass).
- **Loading + skeleton states.** Skeletons exist at root and at four
  dynamic routes. `Math.random()` issue fixed in this commit. No
  layout-shift audit performed.
- **404 handling.** A missing slug returns 404 (via `notFound()`),
  the 404 page has popular-posts CTA. Client-side fetch issue
  flagged above.
- **Internal link integrity.** Not verified against live data.
  Recommended check: crawl `STATIC_URL/posts/index.json`, walk every
  internal `href` in rendered HTML, assert no 404s.
- **CSP / security headers.** Strong baseline present
  (`next.config.js:157-258`): HSTS, CSP, COOP, Permissions-Policy,
  X-Frame-Options=DENY, no `X-Powered-By`. Known gap: `script-src`
  uses `'unsafe-inline'` for AdSense/GTM/Giscus — already tracked
  as issue #740 (nonce-based CSP via middleware).

## Recommended next actions (priority order)

1. **Add OG images to author + archive metadata** (10 min, finishes
   the hole partially closed in this commit).
2. **Unify homepage cache strategy with `lib/posts.ts`** (15 min,
   fixes 5-minute staleness on the most-visited page).
3. **Add nested `error.tsx` per route group** (30 min, big UX lift
   — preserves layout on errors, scoped Sentry context).
4. **Distinguish "service unavailable" from "no posts" on listing
   pages** (20 min, visible polish).
5. **Add `/author/<id>` and paginated `/archive/<n>` to sitemap.ts**
   (15 min, SEO win).
6. **Process `<img>` inside post HTML** (1-2 hours, perf win on
   image-heavy posts).
