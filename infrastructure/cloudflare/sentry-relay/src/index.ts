// Cloudflare Worker — frontend error-capture relay for Glad Labs / Poindexter.
//
// Public visitors' browsers POST Sentry/GlitchTip error envelopes here (the
// Sentry SDK's `tunnel` target). The Worker validates the request, then
// forwards the raw envelope to the self-hosted GlitchTip ingest endpoint,
// which is reachable from the CF edge via a path-scoped Tailscale Funnel
// (URL kept as a Worker secret — never in browser source or this repo).
//
// Why a relay at all: the public site is served from Vercel and GlitchTip is
// self-hosted on the operator's LAN. Neither Vercel functions nor public
// browsers can reach the LAN, so the SDK's same-origin `/monitoring` tunnel (a
// Vercel route) had nowhere to forward to — frontend errors silently went
// nowhere even though the privacy policy advertised error monitoring as
// active. This Worker is the public, hardened front door; the Tailscale Funnel
// is the only path from the edge to the LAN-only tracker. Mirrors the
// page-views-beacon (browser → CF edge → operator backend) pattern.
//
// Returns: 200 on successful forward, 400 malformed envelope, 403 wrong origin
// or disallowed project, 405 non-POST, 429 rate-limited, 502 upstream forward
// failed, 503 relay not configured.

export interface Env {
  // Workers rate-limiting binding (wrangler.toml [[unsafe.bindings]]).
  RATE_LIMITER: RateLimit;
  // Comma-separated allowed browser Origin values, e.g.
  // "https://gladlabs.io,https://www.gladlabs.io". Empty disables the check
  // (dev only — operators MUST set this in production).
  ALLOWED_ORIGINS: string;
  // Comma-separated GlitchTip project IDs the relay will forward. The
  // open-proxy guard: an envelope for any other project is rejected. Empty =
  // fail closed (forward nothing).
  ALLOWED_PROJECT_IDS: string;
  // GlitchTip ingest origin reachable from the CF edge — a path-scoped
  // Tailscale Funnel base URL, e.g. "https://host.tailnet.ts.net". Set as a
  // Worker SECRET (`wrangler secret put GLITCHTIP_INGEST_ORIGIN`) so the
  // operator's tailnet hostname never lands in browser source or this repo.
  GLITCHTIP_INGEST_ORIGIN: string;
}

/** Parse the first NDJSON line of a Sentry envelope (its header), or null. */
export function parseEnvelopeHeader(
  body: string
): Record<string, unknown> | null {
  const firstLine = body.split('\n', 1)[0];
  if (!firstLine) return null;
  try {
    const parsed = JSON.parse(firstLine);
    return typeof parsed === 'object' && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/**
 * Extract { host, projectId, publicKey } from a Sentry DSN
 * (`https://<publicKey>@<host>/<projectId>`), or null when it isn't a DSN.
 */
export function extractDsnParts(
  dsn: string
): { host: string; projectId: string; publicKey: string } | null {
  let url: URL;
  try {
    url = new URL(dsn);
  } catch {
    return null;
  }
  const projectId = url.pathname.replace(/^\/+/, '').split('/')[0] ?? '';
  if (!projectId || !url.username) return null;
  return { host: url.host, projectId, publicKey: url.username };
}

/**
 * Open-proxy guard: true only when `projectId` is in the comma-separated
 * allowlist. An empty/blank allowlist returns false (fail closed) so an
 * unconfigured relay never forwards arbitrary projects to the backend.
 */
export function projectAllowed(
  projectId: string,
  allowlistCsv: string
): boolean {
  const allowed = (allowlistCsv || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return allowed.includes(projectId);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method !== 'POST') {
      return new Response(null, { status: 405 });
    }

    // Origin allowlist — browsers always send Origin on cross-origin fetch
    // (the Sentry SDK tunnel uses fetch). Stops drive-by abuse from other
    // origins. Non-browser clients (no Origin) fall through to the rate limit
    // + project allowlist below.
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

    // Per-IP rate limit. CF-Connecting-IP is the real edge client IP (not
    // spoofable via X-Forwarded-For). 'unknown' in local wrangler dev.
    const ip = req.headers.get('CF-Connecting-IP') || 'unknown';
    const { success } = await env.RATE_LIMITER.limit({ key: ip });
    if (!success) {
      return new Response(null, { status: 429 });
    }

    if (!env.GLITCHTIP_INGEST_ORIGIN) {
      // Fail loud — the relay isn't wired. Matches the stack's "no silent
      // fallbacks" posture: the operator sees 503s, not a silent black hole.
      return new Response('relay not configured', { status: 503 });
    }

    const body = await req.text();
    const header = parseEnvelopeHeader(body);
    const dsn = typeof header?.dsn === 'string' ? header.dsn : '';
    const parts = dsn ? extractDsnParts(dsn) : null;
    if (!parts) {
      return new Response(null, { status: 400 });
    }
    if (!projectAllowed(parts.projectId, env.ALLOWED_PROJECT_IDS)) {
      // Open-proxy guard: never forward an envelope for an unknown project.
      return new Response(null, { status: 403 });
    }

    // Forward the raw envelope to GlitchTip's ingest endpoint over the Funnel.
    // The envelope carries its own DSN key, so GlitchTip authenticates it; the
    // relay only chooses the destination project from the (allowlisted) id.
    const base = env.GLITCHTIP_INGEST_ORIGIN.replace(/\/+$/, '');
    const upstream = `${base}/api/${parts.projectId}/envelope/`;
    try {
      const resp = await fetch(upstream, {
        method: 'POST',
        body,
        headers: {
          'Content-Type':
            req.headers.get('Content-Type') || 'application/x-sentry-envelope',
        },
      });
      // The SDK only needs a 2xx to consider the send delivered.
      return new Response(null, { status: resp.ok ? 200 : 502 });
    } catch {
      return new Response(null, { status: 502 });
    }
  },
};
