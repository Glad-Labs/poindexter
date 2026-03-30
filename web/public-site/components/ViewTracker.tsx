'use client';

import { useEffect } from 'react';

interface ViewTrackerProps {
  slug: string;
}

export function ViewTracker({ slug }: ViewTrackerProps) {
  useEffect(() => {
    // Fire once on mount — track this page view
    const apiBase =
      process.env.NEXT_PUBLIC_API_BASE_URL ||
      process.env.NEXT_PUBLIC_FASTAPI_URL ||
      '';

    if (!apiBase) return;

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
