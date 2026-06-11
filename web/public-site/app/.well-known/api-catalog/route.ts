// RFC 9727 API Catalog — application/linkset+json (RFC 9264)
// Describes the publicly-accessible API surface of gladlabs.io.
// Agents and automated tools use this to discover endpoints, documentation,
// and service descriptions without prior knowledge of the site structure.

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';

export async function GET() {
  const base = SITE_URL.replace(/\/$/, '');

  const catalog = {
    linkset: [
      {
        // Public content API — blog posts served from static R2 CDN.
        anchor: `${base}/api`,
        'service-doc': [
          {
            href: 'https://gladlabs.mintlify.app',
            type: 'text/html',
            title: 'Glad Labs API Documentation',
          },
        ],
        'describedby': [
          {
            href: `${base}/auth.md`,
            type: 'text/markdown',
            title: 'Authentication & Agent Registration',
          },
        ],
      },
      {
        // /api/posts — paginated post index
        anchor: `${base}/api/posts`,
        type: ['application/json'],
        'service-doc': [
          {
            href: 'https://gladlabs.mintlify.app',
            type: 'text/html',
          },
        ],
      },
      {
        // /api/newsletter/subscribe — newsletter subscription endpoint
        anchor: `${base}/api/newsletter/subscribe`,
        type: ['application/json'],
      },
    ],
  };

  return new Response(JSON.stringify(catalog, null, 2), {
    headers: {
      'Content-Type': 'application/linkset+json',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
    },
  });
}
