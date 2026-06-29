// Next.js 16 proxy (replaces the deprecated `middleware.ts` convention).
// Runs at the Vercel Edge before any page render.
//
// Responsibilities:
//  1. /robots.txt — append the Content-Signal directive (draft-romm-aipref-
//     contentsignals) that MetadataRoute.Robots cannot inject itself. The edge
//     layer fetches the metadata route's output and appends the line so agents
//     see the full declaration while the Jest unit tests still get a typed
//     MetadataRoute.Robots object from the module directly.
//  2. Markdown content negotiation — return text/markdown for blog posts when
//     the caller sends `Accept: text/markdown`.
//  3. Inject `Vary: Accept` on all responses so edge caches store separate
//     entries for markdown vs HTML consumers.

import { NextRequest, NextResponse } from 'next/server';

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';

const POST_PATH = /^\/posts\/([^/]+)\/?$/;
// Distinct, edge-cacheable markdown URL: /posts/<slug>.md . Cloudflare's cache
// key ignores `Vary: Accept` (only Accept-Encoding is honored), so the markdown
// variant gets its OWN URL instead of content-negotiating on the HTML URL —
// otherwise an agent's `Accept: text/markdown` request poisons the shared cache
// and browsers get served raw markdown too (the 2026-06-29 incident).
const POST_MD_PATH = /^\/posts\/([^/]+)\.md$/;
const MD_CACHE = 'public, max-age=3600, stale-while-revalidate=86400';
// Header used to prevent infinite recursion when the proxy fetches /robots.txt.
const PROXY_BYPASS_HEADER = 'x-proxy-bypass';

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const accept = request.headers.get('accept') ?? '';

  // ── /robots.txt — append Content-Signal ────────────────────────────────
  if (pathname === '/robots.txt' && !request.headers.has(PROXY_BYPASS_HEADER)) {
    try {
      const upstream = await fetch(request.url, {
        headers: {
          ...Object.fromEntries(request.headers),
          [PROXY_BYPASS_HEADER]: '1',
        },
        signal: AbortSignal.timeout(3000),
      });
      if (upstream.ok) {
        const text = await upstream.text();
        const body =
          text.trimEnd() +
          '\n\n' +
          '# Content Signals (https://contentsignals.org/)\n' +
          '# search=yes — public blog; search-engine indexing is welcome.\n' +
          '# ai-train=no — not licensed for AI model training.\n' +
          '# ai-input=no — not licensed as LLM prompt context.\n' +
          'Content-Signal: ai-train=no, search=yes, ai-input=no\n';
        return new NextResponse(body, {
          status: 200,
          headers: {
            'Content-Type': 'text/plain; charset=utf-8',
            'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
          },
        });
      }
    } catch {
      // Fall through — serve the metadata route's response unmodified.
    }
  }

  // ── Distinct cacheable markdown URL: /posts/<slug>.md ──────────────────
  const mdMatch = POST_MD_PATH.exec(pathname);
  if (mdMatch) {
    const slug = mdMatch[1];
    const post = await fetchPostJson(slug);
    if (post) {
      // Own cache key → safe to cache at the edge.
      return markdownResponse(postToMarkdown(post, slug), MD_CACHE);
    }
    // Unknown slug — fall through to normal 404 handling.
  }

  // ── Same-URL markdown negotiation (legacy clients) — NON-cacheable ─────
  // Back-compat for agents that send `Accept: text/markdown` to the HTML URL.
  // Served `no-store` so Cloudflare never caches it under the HTML key (CF
  // can't vary on Accept); agents wanting a cacheable copy use the
  // `/posts/<slug>.md` alternate advertised in the Link header.
  if (POST_PATH.test(pathname) && accept.includes('text/markdown')) {
    const slug = pathname.replace(/^\/posts\//, '').replace(/\/$/, '');
    const post = await fetchPostJson(slug);
    if (post) {
      const res = markdownResponse(postToMarkdown(post, slug), 'no-store');
      res.headers.set('Link', mdAlternateLink(slug));
      return res;
    }
    // Fall through — serve normal HTML page.
  }

  // ── Pass through; inject Vary + advertise the markdown alternate ───────
  const response = NextResponse.next();
  response.headers.set('Vary', 'Accept');
  const postMatch = POST_PATH.exec(pathname);
  if (postMatch) {
    response.headers.set('Link', mdAlternateLink(postMatch[1]));
  }
  return response;
}

export const config = {
  matcher: [
    '/robots.txt',
    '/posts/:slug',
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|svg|ico|webp|avif)).*)',
  ],
};

// ---------------------------------------------------------------------------

function postToMarkdown(post: Record<string, unknown>, slug: string): string {
  const siteUrl = SITE_URL.replace(/\/$/, '');
  const title = String(post.title ?? slug);
  const date = String(post.date ?? post.published_at ?? '');
  const author = String(post.author ?? 'Glad Labs');
  const tags = Array.isArray(post.tags)
    ? (post.tags as string[]).join(', ')
    : String(post.tags ?? '');
  const excerpt = String(post.excerpt ?? post.summary ?? '');
  const rawContent = String(
    post.content_markdown ??
      post.body_markdown ??
      post.content ??
      post.body ??
      ''
  );

  // Strip HTML tags when only HTML content is available.
  const content = rawContent.startsWith('<')
    ? rawContent
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/p>/gi, '\n\n')
        .replace(/<\/h[1-6]>/gi, '\n\n')
        .replace(/<[^>]+>/g, '')
        .replace(/&nbsp;/g, ' ')
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .trim()
    : rawContent;

  return [
    `# ${title}`,
    '',
    date ? `**Published:** ${date}` : '',
    author ? `**Author:** ${author}` : '',
    tags ? `**Tags:** ${tags}` : '',
    `**URL:** ${siteUrl}/posts/${slug}`,
    '',
    excerpt ? `> ${excerpt}` : '',
    '',
    content,
  ]
    .filter((line) => line !== undefined)
    .join('\n');
}

// `Link` header value advertising the cacheable markdown alternate so agents
// can discover the poison-safe `/posts/<slug>.md` URL.
function mdAlternateLink(slug: string): string {
  const siteUrl = SITE_URL.replace(/\/$/, '');
  return `<${siteUrl}/posts/${slug}.md>; rel="alternate"; type="text/markdown"`;
}

// Fetch a post's static JSON from R2. Returns null (caller falls through to the
// normal HTML page / 404) on any non-OK status, timeout, or network error.
async function fetchPostJson(
  slug: string,
): Promise<Record<string, unknown> | null> {
  try {
    const upstream = await fetch(`${STATIC_URL}/posts/${slug}.json`, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(5000),
    });
    if (upstream.ok) {
      return (await upstream.json()) as Record<string, unknown>;
    }
  } catch {
    // Swallow — caller serves the normal HTML page instead.
  }
  return null;
}

// Build a `text/markdown` response with the given Cache-Control. The distinct
// `/posts/<slug>.md` URL passes a cacheable value; the same-URL Accept
// negotiation passes `no-store` so it can never poison the HTML cache key.
function markdownResponse(md: string, cacheControl: string): NextResponse {
  return new NextResponse(md, {
    status: 200,
    headers: {
      'Content-Type': 'text/markdown; charset=utf-8',
      Vary: 'Accept',
      'Cache-Control': cacheControl,
      'x-markdown-tokens': String(md.length),
    },
  });
}
