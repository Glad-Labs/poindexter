// Cloudflare Worker — page-views beacon for Glad Labs / Poindexter.
// POST {slug, path, referrer} → writes one data point to Analytics Engine.
// Returns 204 on success, 405 for non-POST, 204 for empty body.
//
// Read back via the CF AE SQL HTTP API:
//   POST https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql
//   SELECT blob1 AS slug, count() AS views FROM analytics_events WHERE ...

export interface Env {
  ANALYTICS_ENGINE: AnalyticsEngineDataset;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method !== 'POST') {
      return new Response(null, { status: 405 });
    }

    let body: { slug?: string; path?: string; referrer?: string } = {};
    try {
      const text = await req.text();
      if (text) body = JSON.parse(text);
    } catch {
      // malformed body → no-op, still 204 (sendBeacon is fire-and-forget)
    }

    const slug = (body.slug || '').slice(0, 500);
    const path = (body.path || '').slice(0, 500);
    if (!slug && !path) {
      return new Response(null, { status: 204 });
    }

    const cf = (req.cf as Record<string, unknown> | undefined) ?? {};

    env.ANALYTICS_ENGINE.writeDataPoint({
      blobs: [
        slug, // blob1: post slug (lab join key)
        path, // blob2: full path
        (body.referrer || '').slice(0, 500), // blob3: referrer
        ((cf.country as string) || '').slice(0, 8), // blob4: country
        (req.headers.get('user-agent') || '').slice(0, 200), // blob5: UA
      ],
      doubles: [],
      indexes: [slug || path], // index by slug for fast slug-group queries
    });

    return new Response(null, { status: 204 });
  },
};
