import logger from '@/lib/logger';
/**
 * AdUnit Component
 * Displays responsive Google AdSense ad units
 * Supports multiple formats: responsive, leaderboard, medium-rectangle
 */

'use client';

import { useEffect } from 'react';

export default function AdUnit({ format = 'responsive', className = '' }) {
  const adSenseId = process.env.NEXT_PUBLIC_ADSENSE_ID;

  useEffect(() => {
    // Push ad when component mounts if AdSense script is loaded
    if (window.adsbygoogle && adSenseId) {
      try {
        window.adsbygoogle.push({});
      } catch (error) {
        logger.warn('[AdUnit] Failed to push ad:', error);
      }
    }
  }, [adSenseId]);

  // Return null if no AdSense ID configured
  if (!adSenseId) {
    return (
      <div
        className={`bg-slate-800 rounded-lg p-8 text-center text-slate-400 ${className}`}
      >
        <p className="text-sm">AdSense not configured</p>
      </div>
    );
  }

  // Determine ad dimensions based on format
  const getAdStyle = () => {
    switch (format) {
      case 'leaderboard':
        return { minHeight: '90px' };
      case 'medium-rectangle':
        return { minHeight: '280px' };
      default:
        return { minHeight: '250px' };
    }
  };

  return (
    <div
      className={`bg-slate-900/50 rounded-lg border border-slate-800 p-4 flex items-center justify-center my-6 ${className}`}
      style={getAdStyle()}
    >
      <ins
        className="adsbygoogle"
        style={{
          display: 'block',
          width: '100%',
          height: '100%',
        }}
        data-ad-client={adSenseId}
        data-ad-slot=""
        data-ad-format="auto"
        data-full-width-responsive="true"
      ></ins>
    </div>
  );
}
