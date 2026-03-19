import logger from '@/lib/logger';
import * as Sentry from '@sentry/nextjs';
/**
 * Posts API Route Handler
 * Provides unified access to posts from FastAPI backend
 */

import { NextRequest, NextResponse } from 'next/server';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

// GET /api/posts?page=1&limit=10&status=published
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const offset = parseInt(
      searchParams.get('offset') || searchParams.get('skip') || '0'
    );
    const limit = parseInt(searchParams.get('limit') || '10');
    const status = searchParams.get('status') || 'published';

    // Forward to backend API
    const response = await fetch(
      `${API_BASE}/api/posts?offset=${offset}&limit=${limit}&published_only=${status === 'published'}`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();

    return NextResponse.json({
      items: data.posts || data.data || data.items || [],
      total: data.total ?? data.meta?.pagination?.total ?? 0,
    });
  } catch (error) {
    Sentry.captureException(error);
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error';
    const errorStack = error instanceof Error ? error.stack : undefined;
    logger.error('Error in posts API route', {
      message: errorMessage,
      stack: errorStack,
      endpoint: '/api/posts',
    });
    return NextResponse.json(
      { error: 'Failed to fetch posts', items: [], total: 0 },
      { status: 500 }
    );
  }
}
