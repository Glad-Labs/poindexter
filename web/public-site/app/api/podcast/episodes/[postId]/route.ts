/**
 * Podcast Episode Streaming Route
 *
 * Proxies MP3 episode files from the FastAPI backend.
 * Handles range requests for seeking in podcast players.
 *
 * GET /api/podcast/episodes/{postId}.mp3 → audio/mpeg stream
 */

import { NextRequest, NextResponse } from 'next/server';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ postId: string }> }
) {
  const { postId } = await params;

  // Validate postId format (UUID or safe string, must end with .mp3)
  if (!postId || !/^[\w-]+\.mp3$/.test(postId)) {
    return new NextResponse('Not found', { status: 404 });
  }

  try {
    const headers: Record<string, string> = {};
    const rangeHeader = request.headers.get('range');
    if (rangeHeader) {
      headers['Range'] = rangeHeader;
    }

    const response = await fetch(`${API_BASE}/api/podcast/episodes/${postId}`, {
      headers,
    });

    if (!response.ok) {
      return new NextResponse('Episode not found', { status: 404 });
    }

    const responseHeaders: Record<string, string> = {
      'Content-Type': 'audio/mpeg',
      'Cache-Control': 'public, max-age=86400, immutable',
      'Accept-Ranges': 'bytes',
    };

    const contentLength = response.headers.get('content-length');
    if (contentLength) {
      responseHeaders['Content-Length'] = contentLength;
    }

    const contentRange = response.headers.get('content-range');
    if (contentRange) {
      responseHeaders['Content-Range'] = contentRange;
    }

    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch {
    return new NextResponse('Episode unavailable', { status: 502 });
  }
}
