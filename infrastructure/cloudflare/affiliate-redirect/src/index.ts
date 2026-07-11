// Cloudflare Worker — /go/<code> affiliate redirect for Glad Labs / Poindexter.
//
// GET /go/<code> (site zone) or /<code> (go. subdomain) →
//   302 to the merchant URL resolved from static/affiliate-links.json,
//   plus one Analytics Engine data point per click.
// Unknown or blank code → 302 to HOME_URL.
//
// Read the clicks back via the CF AE SQL HTTP API:
//   POST https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql
//   SELECT blob1 AS code, count() AS clicks FROM affiliate_clicks WHERE ...
// SyncAffiliateClicksJob syncs that into the affiliate_link_clicks table.

export interface Env {
  ANALYTICS_ENGINE: AnalyticsEngineDataset;
  // Public URL of the code→url map (static/affiliate-links.json on R2).
  LINKS_URL: string;
  // Fallback redirect for a blank/unknown code.
  HOME_URL: string;
}

type LinkMap = Record<string, { url: string }>;

// Extract the affiliate code, handling both deploy shapes: /go/<code> on the
// site zone (strip the `go` route prefix) and /<code> on a go. subdomain.
//   /go/mercury -> mercury, /mercury -> mercury, /go/ -> '', / -> ''.
// A bare /go/ has no code, so it yields '' (→ HOME_URL) rather than the literal
// route segment "go".
export function codeFromPath(pathname: string): string {
  const segs = pathname.split('/').filter(Boolean);
  if (segs[0] === 'go') segs.shift(); // drop the /go route prefix (path-zone form)
  return segs[0] || '';
}

export function resolveTarget(map: LinkMap, code: string): string | null {
  return map[code]?.url ?? null;
}

async function loadMap(env: Env): Promise<LinkMap> {
  // Cache the small JSON map at the edge for 5 minutes so a redirect doesn't
  // fetch R2 on every click.
  const cache = caches.default;
  const cacheKey = new Request(env.LINKS_URL);
  let resp = await cache.match(cacheKey);
  if (!resp) {
    resp = await fetch(env.LINKS_URL, { cf: { cacheTtl: 300 } });
    if (resp.ok) {
      const cloned = new Response(resp.clone().body, resp);
      cloned.headers.set('Cache-Control', 'max-age=300');
      await cache.put(cacheKey, cloned);
    }
  }
  try {
    return (await resp.json()) as LinkMap;
  } catch {
    return {};
  }
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const home = env.HOME_URL || 'https://www.gladlabs.io';
    const code = codeFromPath(new URL(req.url).pathname);
    if (!code) return Response.redirect(home, 302);

    const map = await loadMap(env);
    const target = resolveTarget(map, code);
    if (!target) return Response.redirect(home, 302);

    const cf = (req.cf as Record<string, unknown> | undefined) ?? {};
    env.ANALYTICS_ENGINE.writeDataPoint({
      blobs: [
        code, // blob1: affiliate code
        (req.headers.get('Referer') || '').slice(0, 500), // blob2: source post URL
        ((cf.country as string) || '').slice(0, 8), // blob3: country
        (req.headers.get('user-agent') || '').slice(0, 200), // blob4: UA
      ],
      doubles: [],
      indexes: [code],
    });
    return Response.redirect(target, 302);
  },
};
