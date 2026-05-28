'use client';

import { useEffect } from 'react';

interface ViewTrackerProps {
  slug: string;
}

/**
 * ViewTracker — own-analytics beacon. Fires once per post mount and POSTs
 * to the same-origin `/api/page-views` Next.js route, which forwards to
 * the backend `/api/track/view` server-side.
 *
 * Going through a same-origin Next.js route (instead of calling the backend
 * directly from the browser) avoids CORS entirely — the prior incarnation
 * pointed at a Railway URL and was silently broken by CORS regression on
 * 2026-04-09 before being deleted in cleanup commit 721115e2d on 2026-04-21.
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

    const url = '/api/page-views';

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

    // Fallback: fire-and-forget fetch with keepalive.
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
    }).catch(() => {
      // Swallow — beacon is non-essential for the reader.
    });
  }, [slug]);

  return null;
}
