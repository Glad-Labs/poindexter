// Cloudflare Worker — page-views beacon for Glad Labs / Poindexter.
// POST {slug, path, referrer} → writes one data point to Analytics Engine.
// Returns 204 on success, 405 for non-POST, 403 for wrong origin, 429 on rate limit.
//
// Read back via the CF AE SQL HTTP API:
//   POST https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql
//   SELECT blob1 AS slug, count() AS views FROM analytics_events WHERE ...

export interface Env {
  ANALYTICS_ENGINE: AnalyticsEngineDataset;
  // Workers rate-limiting binding (wrangler.toml [[unsafe.bindings]] type="ratelimit").
  // Operator creates the namespace in the CF dashboard; the binding is declared
  // in wrangler.toml under [[unsafe.bindings]].
  RATE_LIMITER: RateLimit;
  // Comma-separated list of allowed Origin header values, e.g.
  // "https://gladlabs.io,https://www.gladlabs.io". Set via wrangler secret or
  // dashboard. Empty string (default) disables origin enforcement — safe for
  // local dev but operators MUST set this in production.
  ALLOWED_ORIGINS: string;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method !== 'POST') {
      return new Response(null, { status: 405 });
    }

    // Origin allowlist — enforced when Origin is present (browsers always send
    // it for cross-origin fetch/sendBeacon; direct curl doesn't). Stops
    // drive-by browser-based inflation without breaking legitimate use.
    // Non-browser clients skip this check and hit the rate limiter below.
    const origin = req.headers.get('Origin');
    if (origin) {
      const allowed = (env.ALLOWED_ORIGINS || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      if (allowed.length > 0 && !allowed.includes(origin)) {
        return new Response(null, { status: 403 });
      }
    }

    // Per-IP rate limit via the Workers rate-limiting binding.
    // CF-Connecting-IP is the real client IP from CF's edge (not spoofable
    // via X-Forwarded-For). Falls back to 'unknown' in local wrangler dev
    // where the header isn't injected — all local requests share one bucket,
    // which is fine for development.
    const ip = req.headers.get('CF-Connecting-IP') || 'unknown';
    const { success } = await env.RATE_LIMITER.limit({ key: ip });
    if (!success) {
      return new Response(null, { status: 429 });
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
