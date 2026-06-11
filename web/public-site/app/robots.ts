import { MetadataRoute } from 'next';
import { SITE_URL } from '@/lib/site.config';

// Content Signals (`Content-Signal: ai-train=no, search=yes, ai-input=no`)
// cannot be injected via MetadataRoute.Robots — the type has no escape hatch
// for custom directives. The `proxy.ts` edge proxy intercepts /robots.txt and
// appends the directive to whatever this function generates, so the live HTTP
// response always includes it. This file exists to satisfy Next.js's metadata
// route system (Next.js 16 requires the default export here) and to keep the
// Jest unit tests working against a typed return value.
export default function robots(): MetadataRoute.Robots {
  const baseUrl = SITE_URL.replace(/\/$/, ''); // Remove trailing slash if present

  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        // Do NOT disallow /_next/ — Google must fetch the CSS/JS under
        // /_next/static to render pages for full-render indexing.
        //
        // Do NOT disallow /.well-known/ — agent discovery bots need access to
        // /.well-known/api-catalog, /.well-known/agent-skills/, and
        // /.well-known/mcp/ to discover this site's capabilities. Blocking it
        // here would prevent well-behaved agents from reading those endpoints.
        disallow: ['/api/', '/admin/', '/private/'],
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
