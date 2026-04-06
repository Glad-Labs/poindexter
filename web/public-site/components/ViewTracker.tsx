'use client';

import { useEffect } from 'react';

interface ViewTrackerProps {
  slug: string;
}

export function ViewTracker({ slug }: ViewTrackerProps) {
  useEffect(() => {
    // Fire once on mount — track this page view
    // Production API URL hardcoded — no env var dependency.
    // This is a known, stable endpoint that won't change.
    const apiBase =
      process.env.NEXT_PUBLIC_API_BASE_URL ||
      process.env.NEXT_PUBLIC_FASTAPI_URL ||
      'http://localhost:8002';

    const payload = {
      path: window.location.pathname,
      slug,
      referrer: document.referrer || '',
    };

    // Use sendBeacon for reliability (survives page navigation)
    if (navigator.sendBeacon) {
      navigator.sendBeacon(
        `${apiBase}/api/track/view`,
        new Blob([JSON.stringify(payload)], { type: 'application/json' })
      );
    } else {
      // Fallback: fire-and-forget fetch
      fetch(`${apiBase}/api/track/view`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch(() => {});
    }
  }, [slug]);

  return null; // Invisible component
}
