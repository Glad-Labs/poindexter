// Cloudflare Worker — newsletter unsubscribe relay.
//
// Why this exists: the unsubscribe flow was complete on the backend
// (per-subscriber tokens, no validity oracle, rate limiting — hardened in
// #252) and 404 on every public exit. The link in every sent email pointed
// at www.gladlabs.io/newsletter/unsubscribe, a page that does not exist, and
// the RFC 8058 `List-Unsubscribe` header pointed at the same dead URL. The
// worker that owns the database is local-first with no public ingress, and a
// human clicking a link in their mail client has to reach something live —
// this is the one part of the system a poll cannot substitute for.
//
//   recipient ──GET ?token=──▶ this Worker  → confirmation page (NO mutation)
//   recipient ──POST ?token=─▶ this Worker  → KV {unsub:<token>}
//   Gmail one-click ──POST──▶ this Worker  → KV {unsub:<token>}
//                                              │
//   ApplyUnsubscribeRequestsJob ◀── GET /pending  (bearer)
//                               ──▶ POST /ack     (bearer)
//
// GET MUST NOT MUTATE. Corporate mail scanners, antivirus link-checkers and
// client prefetchers follow every URL in an email; a GET that unsubscribes
// would silently drop subscribers who never clicked. So GET renders a
// confirm button and only POST records anything. RFC 8058 one-click is
// already a POST, so inbox-level unsubscribe still works in one step.
//
// The Worker cannot validate tokens — it has no database. The token IS the
// credential (43 base64url chars, ~256 bits from secrets.token_urlsafe(32)),
// and the backend validates on apply, so an unknown token is a harmless
// no-op there. What the Worker does enforce is shape, rate limit and TTL, so
// KV cannot be used as free storage.
//
// GET  /?token=<t>  → 200 confirm page, 400 malformed token
// POST /?token=<t>  → 200 recorded (idempotent), 400 malformed token
// GET  /pending     → 200 {tokens:[…]}, 401 bad bearer
// POST /ack         → 200 {removed:n}, 401 bad bearer
// anything else     → 405; all paths: 429 rate limited, 503 when
//                     UNSUBSCRIBE_RELAY_SECRET is unset (fail closed).

export interface Env {
  // KV holding pending unsubscribe requests. Operator creates it
  // (`wrangler kv namespace create RELAY_KV`) and fills the id in
  // wrangler.toml.
  RELAY_KV: KVNamespace;
  // Workers rate-limiting binding (wrangler.toml [[unsafe.bindings]]).
  RATE_LIMITER: RateLimit;
  // Shared secret for the READ side only — the operator poll presents it as
  // a bearer. The write side is deliberately unauthenticated: the recipient
  // holding the token is the authorisation. Set via
  // `wrangler secret put UNSUBSCRIBE_RELAY_SECRET`; unset → 503 everywhere.
  UNSUBSCRIBE_RELAY_SECRET: string;
  // Days a pending request lives in KV before expiring. The poll runs every
  // few minutes, so this only bounds the damage if the poll stops.
  RETENTION_DAYS: string;
}

/** `secrets.token_urlsafe(32)` → exactly 43 base64url characters. */
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const KEY_PREFIX = 'unsub:';

export interface PendingValue {
  requested_at: string;
  /** "link" (confirm button) or "one-click" (RFC 8058 inbox button). */
  via: string;
}

export function isValidToken(token: string | null): token is string {
  return typeof token === 'string' && TOKEN_RE.test(token);
}

/** Constant-time string compare — avoids leaking the secret via timing. */
export function secureEquals(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function bearerOk(request: Request, secret: string): boolean {
  const header = request.headers.get('Authorization') || '';
  if (!header.startsWith('Bearer ')) return false;
  return secureEquals(header.slice(7), secret);
}

function page(title: string, body: string, status = 200): Response {
  // Self-contained, no external assets — this renders inside whatever
  // browser the mail client spawns, including ones with no network beyond
  // the click.
  return new Response(
    `<!doctype html><html lang="en"><head><meta charset="utf-8">` +
      `<meta name="viewport" content="width=device-width,initial-scale=1">` +
      `<meta name="robots" content="noindex">` +
      `<title>${title}</title><style>` +
      `body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1.5rem;color:#111}` +
      `h1{font-size:1.25rem}button{font:inherit;padding:.6rem 1.2rem;border:0;border-radius:6px;` +
      `background:#111;color:#fff;cursor:pointer}p{color:#444}` +
      `@media(prefers-color-scheme:dark){body{background:#111;color:#eee}p{color:#bbb}` +
      `button{background:#eee;color:#111}}` +
      `</style></head><body>${body}</body></html>`,
    { status, headers: { 'content-type': 'text/html; charset=utf-8' } }
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const secret = env.UNSUBSCRIBE_RELAY_SECRET;
    if (!secret) {
      // Fail closed. Without the secret the read side would be open, and an
      // open /pending leaks which tokens are in flight.
      return new Response(JSON.stringify({ error: 'relay not configured' }), {
        status: 503,
        headers: { 'content-type': 'application/json' },
      });
    }

    const ip = request.headers.get('CF-Connecting-IP') || 'anon';
    const { success } = await env.RATE_LIMITER.limit({ key: ip });
    if (!success) {
      return new Response(JSON.stringify({ error: 'rate limited' }), {
        status: 429,
        headers: { 'content-type': 'application/json' },
      });
    }

    const url = new URL(request.url);
    const ttl = Math.max(1, Number(env.RETENTION_DAYS || '30')) * 86400;

    // ---- operator read side (bearer) ------------------------------------
    if (url.pathname === '/pending' && request.method === 'GET') {
      if (!bearerOk(request, secret)) return unauthorized();
      const listed = await env.RELAY_KV.list({
        prefix: KEY_PREFIX,
        limit: 1000,
      });
      const tokens = listed.keys.map((k) => k.name.slice(KEY_PREFIX.length));
      return json({ tokens });
    }

    if (url.pathname === '/ack' && request.method === 'POST') {
      if (!bearerOk(request, secret)) return unauthorized();
      let body: { tokens?: unknown };
      try {
        body = await request.json();
      } catch {
        return json({ error: 'invalid json' }, 400);
      }
      const tokens = Array.isArray(body.tokens) ? body.tokens : [];
      let removed = 0;
      for (const t of tokens) {
        if (isValidToken(typeof t === 'string' ? t : null)) {
          await env.RELAY_KV.delete(KEY_PREFIX + t);
          removed++;
        }
      }
      return json({ removed });
    }

    // ---- recipient side (public; the token is the credential) -----------
    if (url.pathname !== '/' && url.pathname !== '/unsubscribe') {
      return json({ error: 'not found' }, 405);
    }

    const token = url.searchParams.get('token');
    if (!isValidToken(token)) {
      return request.method === 'POST'
        ? json({ error: 'invalid token' }, 400)
        : page(
            'Unsubscribe',
            `<h1>That link looks incomplete</h1><p>Please use the unsubscribe ` +
              `link exactly as it appears in the email.</p>`,
            400
          );
    }

    if (request.method === 'GET') {
      // Deliberately does NOT record anything — see the header note about
      // link scanners. The button below is the only path that mutates.
      return page(
        'Unsubscribe',
        `<h1>Unsubscribe from this newsletter?</h1>` +
          `<p>You will stop receiving new posts by email.</p>` +
          `<form method="POST"><button type="submit">Unsubscribe</button></form>`
      );
    }

    if (request.method === 'POST') {
      // Gmail / Apple Mail one-click send `List-Unsubscribe=One-Click` as a
      // form body and render nothing; the confirm button above posts an
      // empty form. Both land here and are treated identically.
      const via = (await request.clone().text()).includes('One-Click')
        ? 'one-click'
        : 'link';
      const value: PendingValue = {
        requested_at: new Date().toISOString(),
        via,
      };
      // Idempotent: re-clicking overwrites the same key rather than queuing
      // a second request.
      await env.RELAY_KV.put(KEY_PREFIX + token, JSON.stringify(value), {
        expirationTtl: ttl,
      });

      if (via === 'one-click') return json({ ok: true });
      return page(
        'Unsubscribed',
        `<h1>You're unsubscribed</h1><p>You may receive one more email if ` +
          `a send was already in progress.</p>`
      );
    }

    return json({ error: 'method not allowed' }, 405);
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function unauthorized(): Response {
  return json({ error: 'unauthorized' }, 401);
}
