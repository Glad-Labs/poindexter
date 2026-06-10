'use client';
/**
 * AdUnit — Reusable in-content ad placement component
 *
 * Renders a Google AdSense ad unit. Only renders when NEXT_PUBLIC_ADSENSE_ID
 * is set. Supports different formats (auto, rectangle, horizontal, vertical).
 *
 * Usage:
 *   <AdUnit slot="1234567890" format="auto" />
 */

import { useEffect, useRef } from 'react';
import { ADSENSE_ID, ADSENSE_SLOT_ID } from '@/lib/site.config';

interface AdUnitProps {
  slot: string;
  format?: 'auto' | 'rectangle' | 'horizontal' | 'vertical';
  responsive?: boolean;
  className?: string;
}

export default function AdUnit({
  slot,
  format = 'auto',
  responsive = true,
  className = '',
}: AdUnitProps) {
  const adRef = useRef<HTMLModElement>(null);
  const adSenseId = ADSENSE_ID;
  const adSlot = slot || ADSENSE_SLOT_ID;

  useEffect(() => {
    if (!adSenseId || !adSlot) return;
    try {
      const w = window as unknown as { adsbygoogle?: object[] };
      (w.adsbygoogle = w.adsbygoogle || []).push({});
    } catch {
      // AdSense not loaded — silent fail
    }
  }, [adSenseId, adSlot]);

  if (!adSenseId || !adSlot) {
    return null;
  }

  return (
    <div className={`ad-unit ${className}`} role="complementary" aria-label="Advertisement">
      <ins
        ref={adRef}
        className="adsbygoogle"
        style={{ display: 'block' }}
        data-ad-client={adSenseId}
        data-ad-slot={adSlot}
        data-ad-format={format}
        data-full-width-responsive={responsive ? 'true' : 'false'}
      />
    </div>
  );
}
