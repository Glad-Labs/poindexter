/**
 * RSS Feed — auto-generated from published posts.
 *
 * Social media services (dlvr.it, IFTTT, Buffer) can subscribe to this
 * feed and auto-post new articles to X/Twitter, LinkedIn, etc.
 *
 * URL: https://www.gladlabs.io/feed.xml
 */

import { NextResponse } from 'next/server';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';
const FASTAPI_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

interface Post {
  title: string;
  slug: string;
  excerpt?: string;
  content?: string;
  published_at?: string;
  updated_at?: string;
  seo_description?: string;
  featured_image_url?: string;
}

export async function GET() {
  try {
    // Fetch published posts from the API
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10_000);

    const response = await fetch(
      `${FASTAPI_URL}/api/posts?limit=20&published_only=true`,
      {
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        next: { revalidate: 3600 }, // Cache for 1 hour
      }
    );
    clearTimeout(timeoutId);

    let posts: Post[] = [];
    if (response.ok) {
      const data = await response.json();
      posts = data.posts || data.data || [];
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
    <title>Glad Labs</title>
    <link>${SITE_URL}</link>
    <description>Technology, AI, and digital innovation — in-depth articles for developers and founders.</description>
    <language>en-us</language>
    <lastBuildDate>${latestDate}</lastBuildDate>
    <atom:link href="${SITE_URL}/feed.xml" rel="self" type="application/rss+xml" />
    <image>
      <url>${SITE_URL}/og-image.jpg</url>
      <title>Glad Labs</title>
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
    <title>Glad Labs</title>
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
