/**
 * Podcast RSS Feed Route
 *
 * Proxies the podcast RSS feed from the R2 CDN, where the backend
 * publishes the canonical copy on every publish (see
 * services/publish_service.py). Replaces the stale static file that
 * was previously committed at public/podcast-feed.xml.
 *
 * GET /podcast-feed.xml → RSS XML feed of podcast episodes
 */

import { NextResponse } from 'next/server';

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

function deriveFeedUrl(): string {
  try {
    const parsed = new URL(STATIC_URL);
    return `${parsed.origin}/podcast/feed.xml`;
  } catch {
    return 'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/podcast/feed.xml';
  }
}

export const revalidate = 3600;

export async function GET() {
  try {
    const response = await fetch(deriveFeedUrl(), {
      next: { revalidate: 3600 },
    });

    if (!response.ok) {
      return new NextResponse('Podcast feed unavailable', { status: 502 });
    }

    const xml = await response.text();

    return new NextResponse(xml, {
      status: 200,
      headers: {
        'Content-Type': 'application/rss+xml; charset=utf-8',
        'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
      },
    });
  } catch {
    return new NextResponse('Podcast feed unavailable', { status: 502 });
  }
}
