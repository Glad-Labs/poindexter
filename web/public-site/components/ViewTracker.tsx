'use client';

import { useEffect } from 'react';

interface ViewTrackerProps {
  slug: string;
}

/**
 * Parameter names that may carry a distribution surface tag, in priority
 * order. `utm_source` is the default the backend emits (Google Analytics
 * parses it for free, so one tag feeds two systems); `ref` and `source` are
 * accepted so an operator who shortened `distribution_ref_source_param` — or
 * a hand-placed link — still lands attributed.
 */
const REF_PARAMS = ['utm_source', 'ref', 'source'] as const;

/** A surface token as the backend emits it — see services/distribution_ref.py. */
const REF_TOKEN = /^[a-z0-9][a-z0-9_-]{0,31}$/;

/**
 * Pull the distribution surface out of the query string, or '' when there
 * isn't one (a direct visit, or a link nobody tagged).
 *
 * Validated against the same token shape the backend produces rather than
 * forwarded verbatim: this value reaches a `GROUP BY` and a Grafana legend, so
 * an arbitrary visitor-supplied string would be both a cardinality bomb and a
 * needless injection surface. Anything that isn't a recognisable token is
 * dropped, which reads downstream as "untagged" — the honest answer.
 */
export function readRefSource(search: string): string {
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search || '');
  } catch {
    return '';
  }
  for (const name of REF_PARAMS) {
    const raw = (params.get(name) || '').trim().toLowerCase();
    if (raw && REF_TOKEN.test(raw)) return raw;
  }
  return '';
}

/**
 * ViewTracker — own-analytics beacon. Fires once per post mount and POSTs
 * to a Cloudflare Worker beacon (Analytics Engine), which the backend
 * `sync_cloudflare_analytics` job pulls into the local `page_views` table
 * every 5 minutes.
 *
 * URL resolution: `NEXT_PUBLIC_BEACON_URL` wins when set (production). It
 * falls back to the same-origin `/api/page-views` route for local dev
 * convenience — but that route was deleted server-side because Vercel
 * serverless functions can't reach the operator's local Docker network
 * (the silent 2026-04-09 → 2026-05-28 regression), so the fallback is
 * effectively a no-op in dev too (sendBeacon swallows the 404). Acceptable
 * for dev; production must set `NEXT_PUBLIC_BEACON_URL`.
 *
 * The component returns null and never blocks render. Failures are
 * fire-and-forget (sendBeacon with fetch fallback) so beacon misfires do
 * not impact reader experience.
 */
export function ViewTracker({ slug }: ViewTrackerProps) {
  useEffect(() => {
    // Fire once on mount. `path` stays the bare pathname — every downstream
    // consumer (posts.view_count, lab outcomes, the slug joins) keys on it, so
    // the surface tag travels as its own field rather than smuggled into the
    // path where it would fragment every one of those groupings.
    const payload = JSON.stringify({
      path: window.location.pathname,
      slug,
      referrer: document.referrer || '',
      ref: readRefSource(window.location.search),
    });

    let url = process.env.NEXT_PUBLIC_BEACON_URL || '/api/page-views';

    // Guard the scheme footgun: a bare host (no scheme, not root-relative)
    // is treated as a RELATIVE path by sendBeacon/fetch and silently POSTs to
    // the current origin — the 2026-06 page_views outage, where
    // NEXT_PUBLIC_BEACON_URL was "page-views-beacon.<acct>.workers.dev"
    // (no https://) so every beacon hit /posts/<that> and the data was lost.
    // Force https:// for host-like values; leave absolute URLs and the
    // root-relative dev fallback untouched.
    if (url && !/^https?:\/\//i.test(url) && !url.startsWith('/')) {
      url = `https://${url}`;
    }

    // Prefer sendBeacon (survives page unload during quick bounce-outs).
    try {
      if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
        // text/plain is a CORS-safelisted content-type, so the cross-origin
        // POST to the Worker is a "simple" request — no preflight (which the
        // Worker doesn't answer). application/json forces an OPTIONS preflight
        // and the beacon fails with net::ERR_FAILED (2026-06 page_views
        // outage). The Worker reads req.text() + JSON.parse, so it's
        // content-type agnostic — the body is still JSON.
        const blob = new Blob([payload], { type: 'text/plain' });
        const queued = navigator.sendBeacon(url, blob);
        if (queued) return;
      }
    } catch {
      // fall through to fetch
    }

    // Fallback: fire-and-forget fetch with keepalive. text/plain keeps it a
    // simple request (no preflight); no-cors means we can't read the response
    // (which we don't need) — the body still reaches the Worker.
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: payload,
      keepalive: true,
      mode: 'no-cors',
    }).catch(() => {
      // Swallow — beacon is non-essential for the reader.
    });
  }, [slug]);

  return null;
}
