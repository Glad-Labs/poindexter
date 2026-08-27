// Unit tests for the ls-webhook-relay Worker's pure helpers.
//
// The fetch handler is edge-runtime glue (rate-limit binding, KV binding)
// verified via the README curl smoke tests. What genuinely needs locking
// down: the signature check (the whole trust model — an unverified store
// would let anyone plant GitHub usernames) and the payload→KV-record
// extraction (the read-side contract with services/pro_delivery.py's
// `_relay_lookup`: `sub:<id>` / `order:<id>` keys, custom_data value).

import { describe, expect, it } from 'vitest';

import {
  extractRelayRecord,
  hexToBytes,
  retentionTtlSeconds,
  verifySignature,
} from './index';

// Mirrors the live payload shape observed on order 9315803 / subscription
// 2470345 (2026-08-26): custom_data rides meta on BOTH order_created and
// subscription_created; subscription events also carry attributes.order_id.
function subscriptionCreated(
  custom: Record<string, unknown> | null = { github_username: 'octocat' }
) {
  return {
    data: {
      id: '2470345',
      type: 'subscriptions',
      attributes: { status: 'on_trial', order_id: 9315803 },
    },
    meta: {
      test_mode: false,
      event_name: 'subscription_created',
      webhook_id: 'a2996eef-…',
      ...(custom === null ? {} : { custom_data: custom }),
    },
  };
}

describe('extractRelayRecord', () => {
  it('keys a subscription event by both subscription and order id', () => {
    const rec = extractRelayRecord(subscriptionCreated());
    expect(rec).not.toBeNull();
    expect(rec?.keys).toEqual(['sub:2470345', 'order:9315803']);
    expect(rec?.value.custom_data).toEqual({ github_username: 'octocat' });
    expect(rec?.value.event_name).toBe('subscription_created');
  });

  it('keys an order event by order id only', () => {
    const rec = extractRelayRecord({
      data: { id: 9315803, type: 'orders', attributes: { total: 0 } },
      meta: {
        event_name: 'order_created',
        custom_data: { github_username: 'octocat' },
      },
    });
    expect(rec?.keys).toEqual(['order:9315803']);
    expect(rec?.value.subscription_id).toBeNull();
  });

  it('returns null when the event carries no custom_data', () => {
    expect(extractRelayRecord(subscriptionCreated(null))).toBeNull();
  });

  it('returns null for empty custom_data', () => {
    expect(extractRelayRecord(subscriptionCreated({}))).toBeNull();
  });

  it('returns null for unmodeled resource types', () => {
    const rec = extractRelayRecord({
      data: { id: '1', type: 'license-keys', attributes: {} },
      meta: {
        event_name: 'license_key_created',
        custom_data: { github_username: 'x' },
      },
    });
    expect(rec).toBeNull();
  });

  it('tolerates a subscription event missing order_id', () => {
    const payload = subscriptionCreated();
    delete (payload.data.attributes as Record<string, unknown>).order_id;
    const rec = extractRelayRecord(payload);
    expect(rec?.keys).toEqual(['sub:2470345']);
  });

  it('returns null for junk payloads', () => {
    expect(extractRelayRecord(null)).toBeNull();
    expect(extractRelayRecord('nope')).toBeNull();
    expect(extractRelayRecord({ data: {} })).toBeNull();
    expect(extractRelayRecord({ meta: { custom_data: { a: 1 } } })).toBeNull();
  });
});

describe('hexToBytes', () => {
  it('decodes clean hex', () => {
    expect(Array.from(hexToBytes('00ff10') ?? [])).toEqual([0, 255, 16]);
  });

  it('rejects odd length, non-hex, and empty input', () => {
    expect(hexToBytes('abc')).toBeNull();
    expect(hexToBytes('zz')).toBeNull();
    expect(hexToBytes('')).toBeNull();
  });
});

describe('verifySignature', () => {
  // Independently computed: hex(HMAC-SHA256(secret, body)).
  async function sign(secret: string, body: string): Promise<string> {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      enc.encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );
    const mac = await crypto.subtle.sign('HMAC', key, enc.encode(body));
    return Array.from(new Uint8Array(mac))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  }

  it('accepts a correctly signed body', async () => {
    const body = JSON.stringify(subscriptionCreated());
    const sig = await sign('shhh', body);
    expect(await verifySignature('shhh', body, sig)).toBe(true);
  });

  it('rejects a wrong secret, tampered body, and missing header', async () => {
    const body = JSON.stringify(subscriptionCreated());
    const sig = await sign('shhh', body);
    expect(await verifySignature('other', body, sig)).toBe(false);
    expect(await verifySignature('shhh', body + ' ', sig)).toBe(false);
    expect(await verifySignature('shhh', body, null)).toBe(false);
    expect(await verifySignature('shhh', body, 'deadbeef')).toBe(false); // wrong length
    expect(await verifySignature('shhh', body, 'not-hex-'.repeat(8))).toBe(
      false
    );
  });
});

describe('retentionTtlSeconds', () => {
  it('parses days and falls back to 90 on junk', () => {
    expect(retentionTtlSeconds('30')).toBe(30 * 86400);
    expect(retentionTtlSeconds(undefined)).toBe(90 * 86400);
    expect(retentionTtlSeconds('')).toBe(90 * 86400);
    expect(retentionTtlSeconds('-5')).toBe(90 * 86400);
    expect(retentionTtlSeconds('banana')).toBe(90 * 86400);
  });
});
