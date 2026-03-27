'use client';
/**
 * Phase 2: AdSense Component
 *
 * This component wraps the AdSense script with Next.js Script component.
 * Uses strategy="afterInteractive" to avoid layout shift and blocking hydration.
 *
 * Place your Google AdSense script ID in environment variables:
 * NEXT_PUBLIC_ADSENSE_ID=ca-pub-xxxxxxxxxxxxxxxx
 */

import * as Sentry from '@sentry/nextjs';
import Script from 'next/script';
import { useEffect, useState } from 'react';

export default function AdSenseScript() {
  const [mounted, setMounted] = useState(false);
  const adSenseId = process.env.NEXT_PUBLIC_ADSENSE_ID;

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted || !adSenseId) {
    return <></>; // Return empty fragment instead of null to keep hydration consistent
  }

  return (
    <Script
      async
      src={`https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${adSenseId}`}
      strategy="afterInteractive"
      onLoad={() => {
        // Push any queued ads
        const w = window as unknown as { adsbygoogle?: object[] };
        if (w.adsbygoogle) {
          w.adsbygoogle.push({});
        }
      }}
      onError={(e) => {
        Sentry.captureException(
          e || new Error('[AdSense] Failed to load script')
        );
      }}
    />
  );
}
