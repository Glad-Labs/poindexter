import logger from '@/lib/logger';
/**
 * Posts API Route Handler
 * Proxies to static JSON on R2/CDN — no FastAPI backend needed.
 */

import { NextRequest, NextResponse } from 'next/server';

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

// GET /api/posts?offset=0&limit=10
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const offset = parseInt(
      searchParams.get('offset') || searchParams.get('skip') || '0'
    );
    const limit = parseInt(searchParams.get('limit') || '10');

    const response = await fetch(`${STATIC_URL}/posts/index.json`, {
      next: { revalidate: 300 },
    });

    if (!response.ok) {
      throw new Error(`Static JSON returned ${response.status}`);
    }

    const data = await response.json();
    const allPosts = data.posts || [];
    const paged = allPosts.slice(offset, offset + limit);

    return NextResponse.json({
      items: paged,
      total: allPosts.length,
    });
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error';
    logger.error('Error in posts API route', {
      message: errorMessage,
      endpoint: '/api/posts',
    });
    return NextResponse.json(
      { error: 'Failed to fetch posts', items: [], total: 0 },
      { status: 500 }
    );
  }
}
