/**
 * Podcast RSS Feed Route
 *
 * Proxies the podcast RSS feed from the FastAPI backend.
 * Accessible at /api/podcast (redirects to feed.xml) and discoverable
 * by podcast apps via <link rel="alternate" type="application/rss+xml">
 * in the site head.
 *
 * GET /api/podcast → RSS XML feed
 */

import { NextRequest, NextResponse } from 'next/server';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

export async function GET(request: NextRequest) {
  try {
    const response = await fetch(`${API_BASE}/api/podcast/feed.xml`, {
      next: { revalidate: 3600 }, // Revalidate hourly
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
  } catch (error) {
    return new NextResponse('Podcast feed unavailable', { status: 502 });
  }
}
