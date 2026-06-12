import * as Sentry from '@sentry/nextjs';
import type { MetadataRoute } from 'next';
import { SITE_URL } from '@/lib/site.config';

// Time-based ISR backstop (1h). On-demand revalidateTag('posts') on publish
// is primary; this floor keeps the sitemap from going stale indefinitely if
// a publish path ever skips the on-demand revalidate (poindexter#575).
export const revalidate = 3600;

/**
 * Type definitions for sitemap content
 */
interface Post {
  slug: string;
  updated_at?: string;
  published_at?: string;
  // Legacy camelCase variants (in case API format changes)
  updatedAt?: string;
  publishedAt?: string;
}

interface Category {
  slug: string;
}

interface Tag {
  slug: string;
}

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

/**
 * Dynamic Sitemap Generation for Next.js 15
 *
 * Reads from static JSON on R2/CDN — no API server needed.
 * Automatically indexes all published posts, categories, and tags.
 *
 * Google will crawl this immediately on deployment.
 */

async function fetchPublishedContent() {
  // Fetch posts, categories, and sitemap data in parallel.
  // Tag-based cache — all three JSON blobs are regenerated on publish, so
  // they share the 'posts' tag and get killed by revalidateTag('posts')
  // instead of waiting out a 300s TTL (#967).
  const [postsRes, categoriesRes, sitemapRes] = await Promise.all([
    fetch(`${STATIC_URL}/posts/index.json`, {
      next: { tags: ['posts', 'post-index'] },
    }),
    fetch(`${STATIC_URL}/categories.json`, {
      next: { tags: ['posts', 'post-index'] },
    }),
    fetch(`${STATIC_URL}/sitemap.json`, {
      next: { tags: ['posts', 'post-index'] },
    }),
  ]);

  // For each response: throw on 5xx (ISR keeps stale sitemap), empty on 404.
  function assertOkOrEmpty(res: Response, label: string): boolean {
    if (res.ok) return true;
    if (res.status === 404) return false;
    const err = new Error(
      `fetchPublishedContent(${label}): R2 returned ${res.status} ${res.statusText}`,
    );
    Sentry.captureException(err);
    throw err;
  }

  const allPosts: Post[] = assertOkOrEmpty(postsRes, 'posts/index.json')
    ? (await postsRes.json()).posts || []
    : [];

  let allCategories: Category[] = [];
  if (assertOkOrEmpty(categoriesRes, 'categories.json')) {
    const catData = await categoriesRes.json();
    allCategories = catData.categories || catData || [];
  }

  // Extract unique tags from sitemap.json tag URLs, or fall back to empty.
  let allTags: Tag[] = [];
  if (assertOkOrEmpty(sitemapRes, 'sitemap.json')) {
    const sitemapData = await sitemapRes.json();
    const urls: { loc: string }[] = sitemapData.urls || sitemapData || [];
    allTags = urls
      .filter((u) => u.loc && u.loc.includes('/tag/'))
      .map((u) => {
        const slug = u.loc.split('/tag/').pop()?.replace(/\/$/, '') || '';
        return { slug };
      })
      .filter((t) => t.slug);
  }

  return { allPosts, allCategories, allTags };
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const baseUrl = SITE_URL;
  const { allPosts, allCategories, allTags } = await fetchPublishedContent();

  // Static pages
  const staticPages: MetadataRoute.Sitemap = [
    {
      url: baseUrl,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 1,
    },
    {
      url: `${baseUrl}/about`,
      lastModified: new Date(),
      changeFrequency: 'monthly',
      priority: 0.5,
    },
    {
      url: `${baseUrl}/posts`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.8,
    },
    {
      url: `${baseUrl}/archive/1`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.8,
    },
    {
      url: `${baseUrl}/dev-diary`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.7,
    },
  ];

  // Legal pages
  const legalPages: MetadataRoute.Sitemap = [
    {
      url: `${baseUrl}/legal/privacy`,
      lastModified: new Date(),
      changeFrequency: 'yearly',
      priority: 0.3,
    },
    {
      url: `${baseUrl}/legal/terms`,
      lastModified: new Date(),
      changeFrequency: 'yearly',
      priority: 0.3,
    },
    {
      url: `${baseUrl}/legal/cookie-policy`,
      lastModified: new Date(),
      changeFrequency: 'yearly',
      priority: 0.3,
    },
    {
      url: `${baseUrl}/legal/data-requests`,
      lastModified: new Date(),
      changeFrequency: 'yearly',
      priority: 0.3,
    },
  ];

  // Blog posts — API returns snake_case fields (updated_at, published_at)
  const postPages: MetadataRoute.Sitemap = (allPosts || []).map(
    (post: Post) => ({
      url: `${baseUrl}/posts/${post.slug}`,
      lastModified:
        post.updated_at || post.updatedAt
          ? new Date((post.updated_at || post.updatedAt)!)
          : new Date(post.published_at || post.publishedAt || new Date()),
      changeFrequency: 'weekly' as const,
      priority: 0.7,
    })
  );

  // Category pages
  const categoryPages: MetadataRoute.Sitemap = (allCategories || []).map(
    (category: Category) => ({
      url: `${baseUrl}/category/${category.slug}`,
      lastModified: new Date(),
      changeFrequency: 'weekly' as const,
      priority: 0.7,
    })
  );

  // Tag pages
  const tagPages: MetadataRoute.Sitemap = (allTags || []).map((tag: Tag) => ({
    url: `${baseUrl}/tag/${tag.slug}`,
    lastModified: new Date(),
    changeFrequency: 'weekly' as const,
    priority: 0.6,
  }));

  return [
    ...staticPages,
    ...legalPages,
    ...postPages,
    ...categoryPages,
    ...tagPages,
  ];
}
