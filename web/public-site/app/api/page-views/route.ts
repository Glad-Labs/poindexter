/**
 * Page-Views Beacon — same-origin proxy to the FastAPI backend.
 *
 * The browser POSTs to `/api/page-views` (same-origin, no CORS, no headers
 * to forge). This route forwards the payload server-side to the backend's
 * unauthenticated `/api/track/view` endpoint.
 *
 * Going through a Next.js Route Handler (rather than letting the browser
 * call the backend directly) was the architectural fix for the silent
 * beacon failure that started 2026-04-09 — direct browser → backend calls
 * are CORS-fragile and require the backend's allowed_origins to track every
 * Vercel preview hostname. Server-side fetch from the same Vercel function
 * sidesteps that entirely.
 *
 * Returns 204 on success. Never returns the backend body — the browser
 * does not care.
 */

import { NextRequest, NextResponse } from 'next/server';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

// Keep the route on the Node runtime so server-side fetch to a tailscale /
// internal backend URL works without edge restrictions.
export const runtime = 'nodejs';
// Force dynamic — beacon hits must not be cached.
export const dynamic = 'force-dynamic';

interface BeaconPayload {
  path?: string;
  slug?: string;
  referrer?: string;
}

export async function POST(request: NextRequest) {
  let payload: BeaconPayload = {};

  // sendBeacon ships application/json; parse defensively. A malformed body
  // is treated as an empty beacon (still returns 204 — non-fatal).
  try {
    const text = await request.text();
    if (text) payload = JSON.parse(text) as BeaconPayload;
  } catch {
    // ignore — backend will treat empty path as no-op
  }

  const body = JSON.stringify({
    path: typeof payload.path === 'string' ? payload.path : '',
    slug: typeof payload.slug === 'string' ? payload.slug : '',
    referrer: typeof payload.referrer === 'string' ? payload.referrer : '',
  });

  // Forward to the backend. Fire-and-forget posture: a backend hiccup must
  // not surface to the reader.
  try {
    // 2 second timeout — sendBeacon is fire-and-forget on the browser side,
    // we should not block the function indefinitely on an unreachable backend.
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);

    const upstream = await fetch(`${API_BASE}/api/track/view`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // Forward the original UA so the backend can store it. The backend
        // reads it from the request headers, not the body.
        'User-Agent': request.headers.get('user-agent') || 'page-views-beacon',
      },
      body,
      signal: controller.signal,
    });

    clearTimeout(timeout);

    // Backend returns 204 on success regardless. Any 5xx is logged via the
    // backend; the beacon does not need to surface it to the reader.
    if (!upstream.ok && upstream.status >= 500) {
      // eslint-disable-next-line no-console
      console.warn(
        `[page-views] upstream returned ${upstream.status} — beacon dropped`
      );
    }
  } catch (err) {
    // Network error / timeout / abort — beacon dropped, log but do not throw.
    // eslint-disable-next-line no-console
    console.warn('[page-views] upstream unreachable — beacon dropped', err);
  }

  return new NextResponse(null, { status: 204 });
}
