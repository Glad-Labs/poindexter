// Cloudflare Worker — Lemon Squeezy webhook relay for Poindexter Pro delivery.
//
// Why this exists: Lemon Squeezy delivers checkout custom_data (the buyer's
// GitHub username, collected by the storefront Pro CTA) ONLY in webhook
// payloads (`meta.custom_data`) — the REST API structurally omits it from
// order/subscription objects (verified live 2026-08-26, order 9315803). The
// Poindexter worker is local-first with no public ingress, so it can't
// receive webhooks directly; this Worker is the public front door that
// catches them at the CF edge and parks the tiny piece the poll can't get —
// the custom_data → order/subscription mapping — in Workers KV, where the
// worker's outbound-only poll picks it up (services/pro_delivery.py).
//
//   LS webhook ──POST──▶ this Worker ──▶ KV {sub:<id>, order:<id> → custom_data}
//                (HMAC verified)              ▲
//                                             └── polled by SyncProSubscriptionsJob
//
// Deliberately NOT a revenue pipe: the Worker stores custom_data mappings
// only. The worker-side poll remains the single writer of revenue_events,
// so the webhook_id-vs-order_id dedup reconciliation warned about in
// services/pro_delivery.py never arises.
//
// POST / → 200 {stored} on verified payload, 200 {ignored} when the event
// carries no custom_data, 401 bad signature, 405 non-POST, 429 rate limit,
// 503 when LS_WEBHOOK_SECRET is unset (fail closed, never store unverified).

export interface Env {
  // KV namespace holding the custom_data mappings. Operator creates it
  // (`wrangler kv namespace create RELAY_KV`) and fills the id in
  // wrangler.toml.
  RELAY_KV: KVNamespace;
  // Workers rate-limiting binding (wrangler.toml [[unsafe.bindings]]
  // type="ratelimit").
  RATE_LIMITER: RateLimit;
  // Lemon Squeezy webhook signing secret — the SAME value registered on the
  // LS webhook (app_settings.lemon_squeezy_webhook_secret; `poindexter pro
  // relay register` sends it to LS). Set via `wrangler secret put
  // LS_WEBHOOK_SECRET`. Unset → the Worker refuses every request (503).
  LS_WEBHOOK_SECRET: string;
  // How long a mapping lives in KV. Renewal-cycle webhooks
  // (subscription_updated) re-put the key, refreshing the clock, so a live
  // subscription's mapping never actually expires; the TTL is garbage
  // collection for lapsed ones.
  RETENTION_DAYS: string;
}

// The minimal record the poll needs. Everything else about the order /
// subscription is already available to it via the LS REST API.
export interface RelayValue {
  event_name: string;
  order_id: string | null;
  subscription_id: string | null;
  custom_data: Record<string, unknown>;
  stored_at: string;
}

export interface RelayRecord {
  keys: string[];
  value: RelayValue;
}

/**
 * Extract the KV writes for one webhook payload, or null when the event
 * carries nothing worth storing (no custom_data). Pure — unit-tested.
 *
 * Key shape is the read-side contract with services/pro_delivery.py's
 * `_relay_lookup`: `sub:<subscription_id>` and `order:<order_id>`.
 * Subscription events know both ids (attributes.order_id); order events
 * only their own.
 */
export function extractRelayRecord(payload: unknown): RelayRecord | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const body = payload as Record<string, any>;
  const meta = body.meta;
  const data = body.data;
  if (typeof meta !== 'object' || meta === null) return null;
  if (typeof data !== 'object' || data === null) return null;

  const custom = meta.custom_data;
  if (typeof custom !== 'object' || custom === null || Array.isArray(custom)) {
    return null;
  }
  if (Object.keys(custom).length === 0) return null;

  const dataId = data.id != null ? String(data.id) : null;
  const attrs =
    typeof data.attributes === 'object' && data.attributes !== null
      ? (data.attributes as Record<string, unknown>)
      : {};

  let subscriptionId: string | null = null;
  let orderId: string | null = null;
  if (data.type === 'subscriptions') {
    subscriptionId = dataId;
    orderId = attrs.order_id != null ? String(attrs.order_id) : null;
  } else if (data.type === 'orders') {
    orderId = dataId;
  } else {
    // Unmodeled resource type (license keys, invoices, …) — nothing for
    // the delivery poll to join on.
    return null;
  }

  const keys: string[] = [];
  if (subscriptionId) keys.push(`sub:${subscriptionId}`);
  if (orderId) keys.push(`order:${orderId}`);
  if (keys.length === 0) return null;

  return {
    keys,
    value: {
      event_name:
        typeof meta.event_name === 'string' ? meta.event_name : 'unknown',
      order_id: orderId,
      subscription_id: subscriptionId,
      custom_data: custom as Record<string, unknown>,
      stored_at: new Date().toISOString(),
    },
  };
}

/** Decode a hex string to bytes; null when it isn't clean hex. Pure. */
export function hexToBytes(hex: string): Uint8Array<ArrayBuffer> | null {
  if (hex.length === 0 || hex.length % 2 !== 0 || /[^0-9a-fA-F]/.test(hex)) {
    return null;
  }
  const out = new Uint8Array(new ArrayBuffer(hex.length / 2));
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

/**
 * Verify Lemon Squeezy's X-Signature header: hex(HMAC-SHA256(secret, raw
 * body)). crypto.subtle.verify does the comparison natively (constant-time),
 * so no hand-rolled equality check.
 */
export async function verifySignature(
  secret: string,
  rawBody: string,
  signatureHex: string | null
): Promise<boolean> {
  if (!signatureHex) return false;
  const sig = hexToBytes(signatureHex.trim());
  if (sig === null || sig.length !== 32) return false;
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['verify']
  );
  return crypto.subtle.verify('HMAC', key, sig, enc.encode(rawBody));
}

/** Retention days → KV expirationTtl seconds, defaulting sanely. Pure. */
export function retentionTtlSeconds(retentionDays: string | undefined): number {
  const days = Number.parseInt(retentionDays ?? '', 10);
  const effective = Number.isFinite(days) && days > 0 ? days : 90;
  return effective * 86400;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method !== 'POST') {
      return new Response(null, { status: 405 });
    }

    // Fail closed: without the signing secret nothing can be verified, and
    // an unverified store would let anyone plant github usernames.
    if (!env.LS_WEBHOOK_SECRET) {
      return new Response('LS_WEBHOOK_SECRET not configured', { status: 503 });
    }

    // Per-IP rate limit — same posture as the sibling workers. LS webhook
    // volume is a handful of events per purchase; 60/min absorbs any burst
    // while killing a brute-force loop against the HMAC.
    const ip = req.headers.get('CF-Connecting-IP') || 'unknown';
    const { success } = await env.RATE_LIMITER.limit({ key: ip });
    if (!success) {
      return new Response(null, { status: 429 });
    }

    const rawBody = await req.text();
    const ok = await verifySignature(
      env.LS_WEBHOOK_SECRET,
      rawBody,
      req.headers.get('X-Signature')
    );
    if (!ok) {
      return new Response(null, { status: 401 });
    }

    let payload: unknown;
    try {
      payload = JSON.parse(rawBody);
    } catch {
      // Signed-but-malformed should never happen; 200 so LS doesn't retry
      // a body that will never parse.
      return Response.json({ ignored: 'unparseable' });
    }

    const record = extractRelayRecord(payload);
    if (record === null) {
      return Response.json({ ignored: 'no custom_data' });
    }

    const ttl = retentionTtlSeconds(env.RETENTION_DAYS);
    const value = JSON.stringify(record.value);
    for (const key of record.keys) {
      await env.RELAY_KV.put(key, value, { expirationTtl: ttl });
    }
    return Response.json({ stored: record.keys.length });
  },
};
