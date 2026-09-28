/**
 * Video RSS Feed Route
 *
 * Proxies the video RSS feed from the R2 CDN, where the backend
 * publishes the canonical copy on every publish (see
 * services/publish_service.py — mirrors the podcast/feed.xml flow).
 *
 * GET /video-feed.xml → RSS XML feed of video episodes
 */

import { NextResponse } from 'next/server';
import { STATIC_ORIGIN } from '@/lib/static-url';

// The backend publishes the feed beside `static/`, at the bucket's origin.
const FEED_URL = `${STATIC_ORIGIN}/video/feed.xml`;

export const revalidate = 3600;

export async function GET() {
  try {
    const response = await fetch(FEED_URL, {
      next: { revalidate: 3600 },
    });

    if (!response.ok) {
      return new NextResponse('Video feed unavailable', { status: 502 });
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
    return new NextResponse('Video feed unavailable', { status: 502 });
  }
}
