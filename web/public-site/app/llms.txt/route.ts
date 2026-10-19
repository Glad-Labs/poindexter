/**
 * /llms.txt — the site index for language models (lib/llms-txt.ts).
 */
import { buildLlmsTxt } from '@/lib/llms-txt';
import { getMainFeedPosts } from '@/lib/posts';
import {
  SITE_NAME,
  SITE_SUMMARY,
  SITE_TAGLINE,
  SITE_URL,
} from '@/lib/site.config';

// Time-based ISR backstop (1h), like the feeds; the post index fetch carries
// the 'posts' tag, so a publish's revalidateTag refreshes it sooner.
export const revalidate = 3600;

export async function GET() {
  const posts = await getMainFeedPosts();
  const body = buildLlmsTxt(posts, {
    name: SITE_NAME,
    url: SITE_URL,
    tagline: SITE_TAGLINE,
    summary: SITE_SUMMARY,
  });
  return new Response(body, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
    },
  });
}
