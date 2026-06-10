/**
 * RSS Feed — auto-generated from published posts.
 *
 * Social media services (dlvr.it, IFTTT, Buffer) can subscribe to this
 * feed and auto-post new articles to X/Twitter, LinkedIn, etc.
 *
 * URL: https://www.gladlabs.io/feed.xml
 */

import { NextResponse } from 'next/server';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';
import {
  sortPostsNewestFirst,
  cleanPostTitle,
  postExcerpt,
} from '@/lib/posts';

// Time-based ISR backstop (1h), matching podcast-feed.xml / video-feed.xml.
// On-demand revalidateTag('posts') on publish is primary; this floor
// self-heals the feed if a publish path skips revalidate (poindexter#575).
export const revalidate = 3600;

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

interface Post {
  title: string;
  slug: string;
  excerpt?: string;
  content?: string;
  published_at?: string;
  updated_at?: string;
  distributed_at?: string;
  seo_description?: string;
  featured_image_url?: string;
}

/**
 * Only include posts that have been explicitly marked for distribution
 * and were published after this cutoff.  Prevents dlvr.it from
 * re-distributing old/migrated posts.
 */
const FEED_CUTOFF = '2026-04-12T00:00:00Z';

export async function GET() {
  try {
    // Fetch published posts from static JSON on R2.
    // `cache: 'no-store'` so every ISR regen pulls fresh data —
    // previously `next: { revalidate: 300 }` cached the R2 response in
    // Next.js's data cache for 5 min, which couldn't be busted from a
    // backend-side `revalidatePath('/feed.xml')` call. Result: feed
    // could lag the publish path by up to 5 min even after explicit
    // revalidation. The OUTER ISR layer (Cache-Control max-age=3600
    // below) still protects R2 from per-request hammering.
    const response = await fetch(`${STATIC_URL}/posts/index.json`, {
      cache: 'no-store',
    });

    let posts: Post[] = [];
    if (response.ok) {
      const data = await response.json();
      // Audit #1: sort before slicing — index.json order is
      // pipeline-dependent, and this feed drives social auto-posting.
      // Unsorted, the 20-item window and <lastBuildDate> (posts[0])
      // could both miss the actual latest posts.
      posts = sortPostsNewestFirst(
        ((data.posts || []) as Post[]).filter(
          (p) => p.distributed_at && (p.published_at || '') >= FEED_CUTOFF
        )
      ).slice(0, 20);
    }

    const now = new Date().toUTCString();
    const latestDate =
      posts.length > 0 && posts[0].published_at
        ? new Date(posts[0].published_at).toUTCString()
        : now;

    // Build RSS XML
    const items = posts
      .map((post: Post) => {
        const pubDate = post.published_at
          ? new Date(post.published_at).toUTCString()
          : now;
        // Audit #2/#5: clean titles before they hit feed readers and
        // social auto-posters; never use the title as the description
        // (dlvr.it would post the headline twice). postExcerpt returns
        // null when nothing real exists — fall back to empty, not filler.
        const title = cleanPostTitle(post.title);
        const description =
          post.seo_description ||
          postExcerpt(
            { title: post.title, excerpt: post.excerpt, content: post.content },
            300
          ) ||
          '';
        const link = `${SITE_URL}/posts/${post.slug}`;

        return `
    <item>
      <title><![CDATA[${title}]]></title>
      <link>${link}</link>
      <guid isPermaLink="true">${link}</guid>
      <description><![CDATA[${description}]]></description>
      <pubDate>${pubDate}</pubDate>${
        post.featured_image_url
          ? `
      <enclosure url="${post.featured_image_url}" type="image/jpeg" />`
          : ''
      }
    </item>`;
      })
      .join('\n');

    const rss = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${SITE_NAME}</title>
    <link>${SITE_URL}</link>
    <description>Technology, AI, and digital innovation — in-depth articles for developers and founders.</description>
    <language>en-us</language>
    <lastBuildDate>${latestDate}</lastBuildDate>
    <atom:link href="${SITE_URL}/feed.xml" rel="self" type="application/rss+xml" />
    <image>
      <url>${SITE_URL}/og-image.jpg</url>
      <title>${SITE_NAME}</title>
      <link>${SITE_URL}</link>
    </image>
${items}
  </channel>
</rss>`;

    return new NextResponse(rss, {
      headers: {
        'Content-Type': 'application/rss+xml; charset=utf-8',
        'Cache-Control': 'public, max-age=3600, s-maxage=3600',
      },
    });
  } catch (error) {
    // Return a minimal valid RSS feed on error
    return new NextResponse(
      `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>${SITE_NAME}</title>
    <link>${SITE_URL}</link>
    <description>Feed temporarily unavailable</description>
  </channel>
</rss>`,
      {
        headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' },
      }
    );
  }
}
