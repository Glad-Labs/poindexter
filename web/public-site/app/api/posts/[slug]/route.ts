import logger from '@/lib/logger';
import * as Sentry from '@sentry/nextjs';
/**
 * Single Post API Route Handler
 * Provides access to a specific post by slug
 */

import { NextRequest, NextResponse } from 'next/server';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

// GET /api/posts/[slug]
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  try {
    // Unwrap the params promise (Next.js 15+)
    const { slug } = await params;

    // Query the backend directly by slug — avoids fetching the full post
    // collection (previously fetched limit=1000 and filtered client-side,
    // issue #93). The /api/posts/{slug} endpoint returns a single post or 404.
    const response = await fetch(
      `${API_BASE}/api/posts/${encodeURIComponent(slug)}`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (response.status === 404) {
      return NextResponse.json({ error: 'Post not found' }, { status: 404 });
    }

    if (!response.ok) {
      return NextResponse.json(
        { error: 'Failed to fetch post' },
        { status: 500 }
      );
    }

    const post = await response.json();

    return NextResponse.json(post);
  } catch (error) {
    Sentry.captureException(error);
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error';
    const errorStack = error instanceof Error ? error.stack : undefined;
    logger.error('Error fetching post', {
      message: errorMessage,
      stack: errorStack,
      endpoint: '/api/posts/[slug]',
    });
    return NextResponse.json(
      { error: 'Failed to fetch post' },
      { status: 500 }
    );
  }
}
