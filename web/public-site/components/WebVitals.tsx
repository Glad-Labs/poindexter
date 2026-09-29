'use client';

import { useReportWebVitals } from 'next/web-vitals';

type VitalName = 'LCP' | 'FID' | 'CLS' | 'FCP' | 'TTFB' | 'INP';

// Thresholds for Core Web Vitals (good/needs improvement boundaries)
const THRESHOLDS: Record<VitalName, { good: number; poor: number }> = {
  LCP: { good: 2500, poor: 4000 },
  FID: { good: 100, poor: 300 },
  CLS: { good: 0.1, poor: 0.25 },
  FCP: { good: 1800, poor: 3000 },
  TTFB: { good: 800, poor: 1800 },
  INP: { good: 200, poor: 500 },
};

function getRating(
  name: string,
  value: number
): 'good' | 'needs-improvement' | 'poor' | 'unknown' {
  const t = THRESHOLDS[name as VitalName];
  if (!t) return 'unknown';
  if (value <= t.good) return 'good';
  if (value <= t.poor) return 'needs-improvement';
  return 'poor';
}

function sendToGoogleAnalytics({
  name,
  value,
  id,
}: {
  name: string;
  value: number;
  id: string;
}) {
  type WGtag = { gtag?: (..._args: unknown[]) => void };
  const w = window as unknown as WGtag;
  if (typeof window === 'undefined' || !w.gtag) return;
  w.gtag('event', name, {
    event_category: 'Web Vitals',
    event_label: id,
    value: Math.round(name === 'CLS' ? value * 1000 : value),
    non_interaction: true,
  });
}

export default function WebVitals() {
  useReportWebVitals((metric) => {
    const { name, value, id } = metric;
    const rating = getRating(name, value);

    if (process.env.NODE_ENV === 'development') {
      // eslint-disable-next-line no-console
      console.debug(
        `[Web Vitals] ${name}: ${Math.round(value)}ms — ${rating}`,
        {
          id,
          rating,
        }
      );
    }

    // Google Analytics is where vitals are read, every rating included.
    // Poor vitals deliberately do NOT go to Sentry. Error reports travel
    // through a relay into GlitchTip, and a public-site issue pages the
    // operator on its first event. A message carrying the measured value
    // ("LCP=4523ms") would open a new issue, and a new page, per distinct
    // number for what is a performance signal, not an error. The dynamic
    // import that sent it also marked every SDK export as used, which kept
    // tracing and replay code in the bundle every page loads.
    sendToGoogleAnalytics({ name, value, id });
  });

  return null;
}
