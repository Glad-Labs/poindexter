'use client';

import { useEffect } from 'react';

interface ViewTrackerProps {
  slug: string;
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
    // Fire once on mount.
    const payload = JSON.stringify({
      path: window.location.pathname,
      slug,
      referrer: document.referrer || '',
    });

    const url = process.env.NEXT_PUBLIC_BEACON_URL || '/api/page-views';

    // Prefer sendBeacon (survives page unload during quick bounce-outs).
    try {
      if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
        const blob = new Blob([payload], { type: 'application/json' });
        const queued = navigator.sendBeacon(url, blob);
        if (queued) return;
      }
    } catch {
      // fall through to fetch
    }

    // Fallback: fire-and-forget fetch with keepalive. Use no-cors so
    // cross-origin Worker beacons don't get blocked by preflight when
    // sendBeacon is unavailable — the body still reaches the Worker, we
    // just can't read the response (which we don't need anyway).
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
      mode: 'no-cors',
    }).catch(() => {
      // Swallow — beacon is non-essential for the reader.
    });
  }, [slug]);

  return null;
}
