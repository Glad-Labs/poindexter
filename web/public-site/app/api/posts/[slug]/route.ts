/**
 * Single Post API Route Handler
 * Provides access to a specific post by slug
 */

import { NextRequest, NextResponse } from 'next/server';

interface Post {
  slug: string;
  [key: string]: any;
}

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

    // Fetch published posts and filter by slug
    const response = await fetch(
      `${API_BASE}/api/posts?published_only=true&limit=1000`,
      {
        next: { revalidate: 3600 }, // ISR: revalidate every hour
      }
    );

    if (!response.ok) {
      return NextResponse.json(
        { error: 'Failed to fetch posts' },
        { status: 500 }
      );
    }

    const data = await response.json();
    const posts = data.data || data || [];
    const post = posts.find((p: Post) => p.slug === slug);

    if (!post) {
      return NextResponse.json({ error: 'Post not found' }, { status: 404 });
    }

    return NextResponse.json(post);
  } catch (error) {
    const slug = (params as any).slug || 'unknown';
    console.error(`Error fetching post ${slug}:`, error);
    return NextResponse.json(
      { error: 'Failed to fetch post' },
      { status: 500 }
    );
  }
}
