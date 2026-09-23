import { describe, expect, it } from 'vitest';
import worker, { type Env, isValidToken, secureEquals } from './index';

const SECRET = 'test-relay-secret';
const TOKEN = 'A'.repeat(43);

function makeEnv(overrides: Partial<Env> = {}): Env {
  const store = new Map<string, string>();
  return {
    RELAY_KV: {
      put: async (k: string, v: string) => void store.set(k, v),
      get: async (k: string) => store.get(k) ?? null,
      delete: async (k: string) => void store.delete(k),
      list: async ({ prefix }: { prefix: string }) => ({
        keys: [...store.keys()]
          .filter((k) => k.startsWith(prefix))
          .map((name) => ({ name })),
      }),
      _store: store,
    } as unknown as KVNamespace,
    RATE_LIMITER: {
      limit: async () => ({ success: true }),
    } as unknown as RateLimit,
    UNSUBSCRIBE_RELAY_SECRET: SECRET,
    RETENTION_DAYS: '30',
    ...overrides,
  };
}

const req = (url: string, init?: RequestInit) => new Request(url, init);
const base = 'https://relay.example.com';

describe('token shape', () => {
  it('accepts a 43-char base64url token (secrets.token_urlsafe(32))', () => {
    expect(isValidToken('abc-DEF_123'.padEnd(43, 'x'))).toBe(true);
  });
  it('rejects wrong length and non-base64url characters', () => {
    expect(isValidToken('A'.repeat(42))).toBe(false);
    expect(isValidToken('A'.repeat(44))).toBe(false);
    expect(isValidToken('!'.repeat(43))).toBe(false);
    expect(isValidToken(null)).toBe(false);
  });
});

describe('secureEquals', () => {
  it('matches only identical strings', () => {
    expect(secureEquals('abc', 'abc')).toBe(true);
    expect(secureEquals('abc', 'abd')).toBe(false);
    expect(secureEquals('abc', 'abcd')).toBe(false);
  });
});

describe('GET must not mutate', () => {
  // The whole reason this Worker splits GET from POST: corporate mail
  // scanners and client prefetchers follow every link in an email. A GET
  // that unsubscribed would silently drop subscribers who never clicked.
  it('renders a confirm page and stores NOTHING', async () => {
    const env = makeEnv();
    const res = await worker.fetch(req(`${base}/?token=${TOKEN}`), env);
    expect(res.status).toBe(200);
    expect(await res.text()).toContain('<form method="POST"');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((env.RELAY_KV as any)._store.size).toBe(0);
  });
});

describe('POST records the request', () => {
  it('stores the token and confirms to a human', async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      req(`${base}/?token=${TOKEN}`, { method: 'POST' }),
      env
    );
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("You're unsubscribed");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const stored = JSON.parse(
      (env.RELAY_KV as any)._store.get(`unsub:${TOKEN}`)
    );
    expect(stored.via).toBe('link');
  });

  it('handles RFC 8058 one-click with a JSON reply, not a page', async () => {
    // Gmail/Apple Mail POST this body and render nothing.
    const env = makeEnv();
    const res = await worker.fetch(
      req(`${base}/?token=${TOKEN}`, {
        method: 'POST',
        body: 'List-Unsubscribe=One-Click',
      }),
      env
    );
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('application/json');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const stored = JSON.parse(
      (env.RELAY_KV as any)._store.get(`unsub:${TOKEN}`)
    );
    expect(stored.via).toBe('one-click');
  });

  it('is idempotent — re-clicking does not queue a second request', async () => {
    const env = makeEnv();
    await worker.fetch(req(`${base}/?token=${TOKEN}`, { method: 'POST' }), env);
    await worker.fetch(req(`${base}/?token=${TOKEN}`, { method: 'POST' }), env);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((env.RELAY_KV as any)._store.size).toBe(1);
  });

  it('rejects a malformed token', async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      req(`${base}/?token=nope`, { method: 'POST' }),
      env
    );
    expect(res.status).toBe(400);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((env.RELAY_KV as any)._store.size).toBe(0);
  });
});

describe('operator read side', () => {
  it('/pending requires the bearer', async () => {
    const env = makeEnv();
    expect((await worker.fetch(req(`${base}/pending`), env)).status).toBe(401);
    const res = await worker.fetch(
      req(`${base}/pending`, {
        headers: { Authorization: `Bearer ${SECRET}` },
      }),
      env
    );
    expect(res.status).toBe(200);
  });

  it('/pending lists queued tokens and /ack removes them', async () => {
    const env = makeEnv();
    await worker.fetch(req(`${base}/?token=${TOKEN}`, { method: 'POST' }), env);

    const auth = { Authorization: `Bearer ${SECRET}` };
    const pending = await (
      await worker.fetch(req(`${base}/pending`, { headers: auth }), env)
    ).json();
    expect(pending).toEqual({ tokens: [TOKEN] });

    const acked = await (
      await worker.fetch(
        req(`${base}/ack`, {
          method: 'POST',
          headers: auth,
          body: JSON.stringify({ tokens: [TOKEN] }),
        }),
        env
      )
    ).json();
    expect(acked).toEqual({ removed: 1 });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((env.RELAY_KV as any)._store.size).toBe(0);
  });

  it('/ack ignores malformed tokens rather than deleting by prefix', async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      req(`${base}/ack`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${SECRET}` },
        body: JSON.stringify({ tokens: ['../*', 42, null] }),
      }),
      env
    );
    expect(await res.json()).toEqual({ removed: 0 });
  });
});

describe('fail closed', () => {
  it('503s everywhere when the secret is unset', async () => {
    const env = makeEnv({ UNSUBSCRIBE_RELAY_SECRET: '' });
    for (const r of [
      req(`${base}/?token=${TOKEN}`),
      req(`${base}/?token=${TOKEN}`, { method: 'POST' }),
      req(`${base}/pending`),
    ]) {
      expect((await worker.fetch(r, env)).status).toBe(503);
    }
  });

  it('429s when rate limited', async () => {
    const env = makeEnv({
      RATE_LIMITER: {
        limit: async () => ({ success: false }),
      } as unknown as RateLimit,
    });
    const res = await worker.fetch(
      req(`${base}/?token=${TOKEN}`, { method: 'POST' }),
      env
    );
    expect(res.status).toBe(429);
  });
});
