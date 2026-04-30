import logger from '@/lib/logger';
/**
 * Single Post API Route Handler
 * Proxies to static JSON on R2/CDN — no FastAPI backend needed.
 */

import { NextRequest, NextResponse } from 'next/server';

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

// GET /api/posts/[slug]
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  try {
    const { slug } = await params;

    const response = await fetch(
      `${STATIC_URL}/posts/${encodeURIComponent(slug)}.json`,
      {
        next: { revalidate: 300 },
      }
    );

    if (!response.ok) {
      return NextResponse.json({ error: 'Post not found' }, { status: 404 });
    }

    const post = await response.json();
    return NextResponse.json(post);
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error';
    logger.error('Error fetching post', {
      message: errorMessage,
      endpoint: '/api/posts/[slug]',
    });
    return NextResponse.json(
      { error: 'Failed to fetch post' },
      { status: 500 }
    );
  }
}
