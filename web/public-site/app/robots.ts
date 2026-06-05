import { MetadataRoute } from 'next';
import { SITE_URL } from '@/lib/site.config';

export default function robots(): MetadataRoute.Robots {
  const baseUrl = SITE_URL.replace(/\/$/, ''); // Remove trailing slash if present

  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        // Do NOT disallow /_next/ — Google must fetch the CSS/JS under
        // /_next/static to render pages (it uses them for layout,
        // mobile-friendliness, and full-render indexing). Blocking it only
        // walls off build assets that are never indexed as pages anyway,
        // while degrading how Google renders the real content. Those assets
        // were ~all of the "Blocked by robots.txt" entries in Search Console
        // (SEO indexing audit, 2026-06-04).
        disallow: ['/api/', '/.well-known/', '/admin/', '/private/'],
      },
      {
        // Block aggressive scrapers that don't contribute to SEO
        userAgent: 'DotBot',
        disallow: '/',
      },
    ],
    sitemap: `${baseUrl}/sitemap.xml`,
  };
}
