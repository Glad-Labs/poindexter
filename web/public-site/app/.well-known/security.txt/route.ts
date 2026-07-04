// RFC 9116 security.txt — vulnerability-disclosure contact for gladlabs.io.
// Canonical location per RFC 9116 §3 is /.well-known/security.txt; a legacy
// top-level /security.txt redirect lives in app/security.txt/route.ts.
//
// Expires is computed per-request (now + 180 days) instead of hardcoded so the
// file can never silently lapse — RFC 9116 §2.5.5 requires the field and wants
// it under a year out. `force-dynamic` keeps Next.js from freezing a
// build-time date into a static route; the Cache-Control header still lets
// the edge cache the response for a day.

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';

const EXPIRES_DAYS = 180;

export const dynamic = 'force-dynamic';

export async function GET() {
  const base = SITE_URL.replace(/\/$/, '');
  const expires = new Date(Date.now() + EXPIRES_DAYS * 24 * 60 * 60 * 1000);

  const body = [
    '# Glad Labs security contact (RFC 9116)',
    'Contact: mailto:security@gladlabs.io',
    `Expires: ${expires.toISOString()}`,
    `Canonical: ${base}/.well-known/security.txt`,
    'Policy: https://github.com/Glad-Labs/poindexter/blob/main/SECURITY.md',
    'Preferred-Languages: en',
    '',
  ].join('\n');

  return new Response(body, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'public, max-age=86400, stale-while-revalidate=604800',
    },
  });
}
