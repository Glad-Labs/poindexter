import logger from '@/lib/logger';
import * as Sentry from '@sentry/nextjs';
import { revalidatePath, revalidateTag } from 'next/cache';

/**
 * On-demand ISR revalidation endpoint
 * Triggered when posts are published or updated in the admin UI
 * Invalidates caches for all pages that display posts
 *
 * Security: Requires REVALIDATE_SECRET token to prevent abuse
 *
 * Accepts both `paths` (revalidatePath) and `tags` (revalidateTag).
 * Tag-based invalidation is preferred for data fetches because
 * revalidatePath only invalidates the route cache — not the data
 * cache keyed by fetch URL. Tags kill the null-cached response
 * that otherwise persists for 300s after a new post goes live.
 */
export async function POST(request) {
  try {
    // Verify revalidation token for security
    const secret = request.headers.get('x-revalidate-secret');
    const REVALIDATE_SECRET = process.env.REVALIDATE_SECRET;
    if (!REVALIDATE_SECRET) {
      return new Response(
        JSON.stringify({ error: 'Revalidation not configured' }),
        {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    }

    if (secret !== REVALIDATE_SECRET) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const { paths = [], tags = [] } = await request.json();

    // Revalidate specific paths or all post-related paths
    const pathsToRevalidate =
      paths.length > 0
        ? paths
        : [
            '/', // Homepage (featured post)
            '/archive', // Archive pages
            '/posts', // Posts listing
          ];

    // Default tags if none supplied — always invalidate the post index
    // and any slug-specific data cached under post:<slug>.
    const tagsToRevalidate = tags.length > 0 ? tags : ['posts', 'post-index'];

    logger.log('🔄 Revalidating paths:', pathsToRevalidate);
    logger.log('🔄 Revalidating tags:', tagsToRevalidate);

    for (const path of pathsToRevalidate) {
      await revalidatePath(path, 'page');
    }

    for (const tag of tagsToRevalidate) {
      revalidateTag(tag);
    }

    return new Response(
      JSON.stringify({
        success: true,
        message: `Revalidated ${pathsToRevalidate.length} path(s) + ${tagsToRevalidate.length} tag(s)`,
        paths: pathsToRevalidate,
        tags: tagsToRevalidate,
        timestamp: new Date().toISOString(),
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  } catch (error) {
    Sentry.captureException(error);
    logger.error('❌ Revalidation error:', error);
    return new Response(
      JSON.stringify({
        error: 'Revalidation failed',
      }),
      {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }
}
