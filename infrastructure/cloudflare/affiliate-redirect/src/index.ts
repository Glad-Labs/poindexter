// Cloudflare Worker — /go/<code> affiliate redirect for Glad Labs / Poindexter.
//
// GET /go/<code> (site zone) or /<code> (go. subdomain) →
//   302 to the merchant URL resolved from static/affiliate-links.json,
//   plus one Analytics Engine data point per click.
// Blank or unknown code → 302 to HOME_URL.
// Missing config → 503. Map unreachable → 502. (See "Failure modes" below.)
//
// Read the clicks back via the CF AE SQL HTTP API:
//   POST https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql
//   SELECT blob1 AS code, count() AS clicks FROM affiliate_clicks WHERE ...
// SyncAffiliateClicksJob syncs that into the affiliate_link_clicks table.
//
// ## Failure modes
//
// Every unhappy path used to collapse into "302 to HOME_URL", which is also
// what a perfectly healthy Worker does for a stale link — so a broken Worker
// and a working one were pixel-identical from the outside while affiliate
// attribution silently died. The three cases are now distinct on the wire:
//
//   503  LINKS_URL or HOME_URL unset — the Worker isn't wired.
//   502  the code→url map could not be loaded (bad LINKS_URL, R2 outage,
//        non-2xx, unparseable body). A Worker/R2 fault, not a reader's stale
//        link.
//   302  blank or genuinely-unknown code → HOME_URL. The one case that is
//        normal, so it keeps the "never a broken link" promise.

export interface Env {
  ANALYTICS_ENGINE: AnalyticsEngineDataset;
  // LINKS_URL and HOME_URL are Worker SECRETS (`wrangler secret put <NAME>`),
  // so each binding is absent until the operator sets it — wrangler.toml
  // deliberately declares no [vars]: a plain-text var of the same name would
  // clobber the secret on the next deploy and silently revert the config.
  //
  // Public URL of the code→url map (static/affiliate-links.json on R2).
  LINKS_URL?: string;
  // Fallback redirect for a blank/unknown code. A secret rather than a [vars]
  // entry so no operator hostname is committed to this repo, and so a deploy
  // can't quietly point every unknown code at someone else's site.
  HOME_URL?: string;
}

type LinkMap = Record<string, { url: string }>;

/**
 * The Cloudflare edge cache — `caches.default`, an extension to the standard
 * CacheStorage API.
 *
 * The cast is load-bearing. The sibling test file imports vitest, which pulls
 * TypeScript's DOM lib into the program, and DOM's `CacheStorage` has no
 * `default` member — it shadows the one `@cloudflare/workers-types` declares,
 * so `caches.default` fails to typecheck even though it is correct at runtime.
 * Narrowing at this single call site keeps this worker's tsconfig identical to
 * the other three in infrastructure/cloudflare/ instead of forking one of them.
 */
function edgeCache(): Cache {
  return (caches as CacheStorage & { default: Cache }).default;
}

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

/**
 * Fetch the code→url map, or `null` when it could not be loaded.
 *
 * The null return is the point of this function's signature. Previously every
 * failure here returned `{}`, so each code resolved to null and each click
 * 302'd home — the same response a healthy Worker gives for an unknown code.
 * Callers turn `null` into a loud 502 instead, which is what makes a
 * misconfigured Worker observable at all.
 */
async function loadMap(env: Env): Promise<LinkMap | null> {
  const url = env.LINKS_URL;
  if (!url) return null;
  try {
    // Cache the small JSON map at the edge for 5 minutes so a redirect doesn't
    // fetch R2 on every click.
    const cache = edgeCache();
    // Throws on a malformed URL — e.g. an un-substituted "<r2-public-host>"
    // placeholder left by a deploy. The catch below turns that into a 502
    // rather than an unhandled 500 with no explanation.
    const cacheKey = new Request(url);
    let resp = await cache.match(cacheKey);
    if (!resp) {
      resp = await fetch(url, { cf: { cacheTtl: 300 } });
      // A 404/403 body is not a link map; don't cache it and don't let
      // JSON.parse decide the outcome.
      if (!resp.ok) return null;
      const cloned = new Response(resp.clone().body, resp);
      cloned.headers.set('Cache-Control', 'max-age=300');
      await cache.put(cacheKey, cloned);
    }
    const parsed = (await resp.json()) as unknown;
    // Guard the shape too: a JSON `null`, array, or scalar would make
    // resolveTarget throw or misbehave on every click.
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return null;
    }
    return parsed as LinkMap;
  } catch {
    return null;
  }
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const home = env.HOME_URL;
    if (!home || !env.LINKS_URL) {
      // Fail loud — the Worker isn't wired. Matches the stack's "no silent
      // fallbacks" posture and sentry-relay's 503: the operator gets a status
      // code instead of readers quietly bouncing off an unconfigured host.
      return new Response('affiliate-redirect not configured', { status: 503 });
    }

    const code = codeFromPath(new URL(req.url).pathname);
    if (!code) return Response.redirect(home, 302);

    const map = await loadMap(env);
    if (map === null) {
      return new Response('affiliate link map unavailable', { status: 502 });
    }

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
