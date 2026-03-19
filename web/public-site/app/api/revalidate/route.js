import logger from '@/lib/logger';
import * as Sentry from '@sentry/nextjs';
import { revalidatePath } from 'next/cache';

/**
 * On-demand ISR revalidation endpoint
 * Triggered when posts are published or updated in the admin UI
 * Invalidates caches for all pages that display posts
 *
 * Security: Requires REVALIDATE_SECRET token to prevent abuse
 */
export async function POST(request) {
  try {
    // Verify revalidation token for security
    const secret = request.headers.get('x-revalidate-secret');
    const REVALIDATE_SECRET = process.env.REVALIDATE_SECRET || 'dev-secret-key';

    if (secret !== REVALIDATE_SECRET) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const { paths = [] } = await request.json();

    // Revalidate specific paths or all post-related paths
    const pathsToRevalidate =
      paths.length > 0
        ? paths
        : [
            '/', // Homepage (featured post)
            '/archive', // Archive pages
            '/posts', // Posts listing
          ];

    logger.log('🔄 Revalidating paths:', pathsToRevalidate);

    for (const path of pathsToRevalidate) {
      await revalidatePath(path, 'page');
    }

    return new Response(
      JSON.stringify({
        success: true,
        message: `Successfully revalidated ${pathsToRevalidate.length} path(s)`,
        paths: pathsToRevalidate,
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
