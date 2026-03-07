import logger from '@/lib/logger';
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
    const skip = parseInt(searchParams.get('skip') || '0');
    const limit = parseInt(searchParams.get('limit') || '10');
    const status = searchParams.get('status') || 'published';

    // Forward to backend API
    const response = await fetch(
      `${API_BASE}/api/posts?skip=${skip}&limit=${limit}&published_only=${status === 'published'}`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();

    return NextResponse.json({
      items: data.data || data.items || [],
      total: data.meta?.pagination?.total || data.total || 0,
    });
  } catch (error) {
    logger.error('Error in posts API route:', error);
    return NextResponse.json(
      { error: 'Failed to fetch posts', items: [], total: 0 },
      { status: 500 }
    );
  }
}
