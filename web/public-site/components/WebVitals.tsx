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
  type WGtag = { gtag?: (...args: unknown[]) => void };
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

    sendToGoogleAnalytics({ name, value, id });

    // Alert Sentry for poor Core Web Vitals so on-call engineers get notified
    // when real users experience degraded performance. Uses dynamic import to
    // avoid bundling Sentry in non-Sentry deployments that omit @sentry/nextjs.
    if (rating === 'poor' && typeof window !== 'undefined') {
      import('@sentry/nextjs')
        .then((Sentry) => {
          Sentry.captureMessage(
            `Web Vital degraded: ${name}=${Math.round(value)}ms`,
            {
              level: 'warning',
              tags: { vital: name, rating },
            }
          );
        })
        .catch(() => {
          // Sentry not installed — silently skip
        });
    }
  });

  return null;
}
