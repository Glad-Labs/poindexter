import { MetadataRoute } from 'next';
import { SITE_URL } from '@/lib/site.config';

export default function robots(): MetadataRoute.Robots {
  const baseUrl = SITE_URL.replace(/\/$/, ''); // Remove trailing slash if present

  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: ['/_next/', '/api/', '/.well-known/', '/admin/', '/private/'],
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
