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
    // Fetch published posts from static JSON on R2
    const response = await fetch(`${STATIC_URL}/posts/index.json`, {
      next: { revalidate: 300 }, // Revalidate every 5 min
    });

    let posts: Post[] = [];
    if (response.ok) {
      const data = await response.json();
      posts = (data.posts || [])
        .filter((p: Post) => p.distributed_at && (p.published_at || '') >= FEED_CUTOFF)
        .slice(0, 20);
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
        const description = post.seo_description || post.excerpt || post.title;
        const link = `${SITE_URL}/posts/${post.slug}`;

        return `
    <item>
      <title><![CDATA[${post.title}]]></title>
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
