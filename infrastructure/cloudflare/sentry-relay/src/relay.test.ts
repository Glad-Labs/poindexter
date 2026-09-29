// Tests for the sentry-relay Worker.
//
// The handler runs against real SQLite (the node:sqlite-backed D1 double in
// test-d1.ts; D1 is SQLite), so the Worker's queue SQL runs exactly as
// written: the capped INSERT, the batched prune/select/count, the chunked
// ack. The tunables come from this Worker's own wrangler.toml [vars]; only
// the rate limiter and the secrets are substituted.

import { beforeEach, describe, expect, it } from 'vitest';

import wranglerToml from '../wrangler.toml?raw';

import worker, {
  type Env,
  bytesToBase64,
  extractDsnParts,
  inspectEnvelope,
  isValidPublicKey,
  projectAllowed,
  resetSchemaCacheForTests,
  secureEquals,
} from './index';
import { createD1 } from './test-d1';

const SECRET = 'test-relay-secret';
const ORIGIN = 'https://site.example.com';
// A fake key in the dashless form the SDK requires. Built, not written out:
// a 32-character hex literal reads as a credential to secret scanners.
const KEY = 'c'.repeat(32);
const DSN = `https://${KEY}@relay.example.com/7`;
const BASE = 'https://relay.example.com';
const AUTH = { Authorization: `Bearer ${SECRET}` };

const enc = new TextEncoder();

type Item = {
  type: string;
  payload: string | Uint8Array;
  withLength?: boolean;
};

/** Build a Sentry envelope: header line, then item header + payload per item. */
function envelope(
  items: Item[],
  header: Record<string, unknown> = { event_id: 'e'.repeat(32), dsn: DSN }
): Uint8Array {
  const parts: Uint8Array[] = [enc.encode(JSON.stringify(header) + '\n')];
  for (const item of items) {
    const payload =
      typeof item.payload === 'string'
        ? enc.encode(item.payload)
        : item.payload;
    const itemHeader: Record<string, unknown> = { type: item.type };
    if (item.withLength) itemHeader.length = payload.byteLength;
    parts.push(
      enc.encode(JSON.stringify(itemHeader) + '\n'),
      payload,
      enc.encode('\n')
    );
  }
  const total = parts.reduce((n, p) => n + p.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const p of parts) {
    out.set(p, offset);
    offset += p.byteLength;
  }
  return out;
}

const errorEnvelope = () =>
  envelope([{ type: 'event', payload: '{"message":"boom","level":"error"}' }]);

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe('inspectEnvelope', () => {
  it('reads the header and every item type', () => {
    const summary = inspectEnvelope(
      envelope([
        { type: 'event', payload: '{"a":1}' },
        { type: 'attachment', payload: 'raw', withLength: true },
      ])
    );
    expect(summary?.header.dsn).toBe(DSN);
    expect(summary?.itemTypes).toEqual(['event', 'attachment']);
  });

  it('skips a length-delimited payload even when it contains newlines', () => {
    // A compressed replay or attachment is binary and may hold 0x0a bytes;
    // only the declared length says where it ends.
    const binary = new Uint8Array([1, 10, 2, 10, 10, 3]);
    const summary = inspectEnvelope(
      envelope([
        { type: 'replay_recording', payload: binary, withLength: true },
        { type: 'event', payload: '{}' },
      ])
    );
    expect(summary?.itemTypes).toEqual(['replay_recording', 'event']);
  });

  it('refuses a payload shorter than its declared length', () => {
    const body = enc.encode(
      JSON.stringify({ dsn: DSN }) + '\n' + '{"type":"event","length":50}\n{}'
    );
    expect(inspectEnvelope(body)).toBeNull();
  });

  it('refuses a malformed header or item header', () => {
    expect(inspectEnvelope(new Uint8Array())).toBeNull();
    expect(
      inspectEnvelope(enc.encode('not-json\n{"type":"event"}\n{}'))
    ).toBeNull();
    expect(
      inspectEnvelope(enc.encode(`{"dsn":"${DSN}"}\nnot-json\n{}`))
    ).toBeNull();
    expect(
      inspectEnvelope(enc.encode(`{"dsn":"${DSN}"}\n{"length":2}\n{}`))
    ).toBeNull();
  });
});

describe('extractDsnParts', () => {
  it('pulls host, projectId and publicKey from a DSN', () => {
    expect(extractDsnParts(DSN)).toEqual({
      host: 'relay.example.com',
      projectId: '7',
      publicKey: KEY,
    });
  });

  it('returns null without a project path or for a non-URL', () => {
    expect(extractDsnParts(`https://${KEY}@relay.example.com`)).toBeNull();
    expect(extractDsnParts('clearly not a dsn')).toBeNull();
  });
});

describe('isValidPublicKey', () => {
  it('accepts a GlitchTip key with or without dashes', () => {
    expect(isValidPublicKey(KEY)).toBe(true);
    expect(isValidPublicKey('cccccccc-cccc-cccc-cccc-cccccccccccc')).toBe(true);
  });
  it('rejects anything else', () => {
    expect(isValidPublicKey('pubkey')).toBe(false);
    expect(isValidPublicKey(`${KEY}/../`)).toBe(false);
  });
});

describe('projectAllowed (open-proxy guard)', () => {
  it('allows only listed ids, trimming whitespace', () => {
    expect(projectAllowed('42', ' 7, 42 , 99 ')).toBe(true);
    expect(projectAllowed('500', '7,42,99')).toBe(false);
  });

  it('FAILS CLOSED when the allowlist is empty', () => {
    expect(projectAllowed('7', undefined)).toBe(false);
    expect(projectAllowed('7', '   ')).toBe(false);
  });
});

describe('secureEquals', () => {
  it('matches only identical strings', () => {
    expect(secureEquals('abc', 'abc')).toBe(true);
    expect(secureEquals('abc', 'abd')).toBe(false);
    expect(secureEquals('abc', 'abcd')).toBe(false);
  });
});

describe('bytesToBase64', () => {
  it('round-trips binary bytes, including past one chunk', () => {
    const bytes = new Uint8Array(70_000).map((_, i) => i % 256);
    expect(base64ToBytes(bytesToBase64(bytes))).toEqual(bytes);
  });
});

// ---------------------------------------------------------------------------
// Handler, against real SQLite
// ---------------------------------------------------------------------------

/** A `[vars]` value from the committed wrangler.toml: what production runs with. */
function tomlVar(name: string): string {
  const match = wranglerToml.match(
    new RegExp(`^${name}\\s*=\\s*"([^"]*)"`, 'm')
  );
  if (!match) throw new Error(`wrangler.toml [vars] has no ${name}`);
  return match[1];
}

const VARS = {
  RETENTION_DAYS: tomlVar('RETENTION_DAYS'),
  MAX_QUEUE_ROWS: tomlVar('MAX_QUEUE_ROWS'),
  MAX_ENVELOPE_BYTES: tomlVar('MAX_ENVELOPE_BYTES'),
};

let db: D1Database;
let limiterAllows = true;

beforeEach(() => {
  limiterAllows = true;
  db = createD1();
  resetSchemaCacheForTests();
});

function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    RELAY_DB: db,
    RATE_LIMITER: {
      limit: async () => ({ success: limiterAllows }),
    } as unknown as RateLimit,
    ...VARS,
    SENTRY_RELAY_SECRET: SECRET,
    ALLOWED_ORIGINS: ORIGIN,
    ALLOWED_PROJECT_IDS: '7',
    ...overrides,
  };
}

function post(body: BodyInit, headers: Record<string, string> = {}): Request {
  return new Request(`${BASE}/relay`, {
    method: 'POST',
    body,
    headers: { Origin: ORIGIN, ...headers },
  });
}

async function pending(
  env: Env,
  query = ''
): Promise<{
  envelopes: {
    id: number;
    project_id: string;
    public_key: string;
    item_types: string[];
    body: string;
  }[];
  backlog: number;
  expired: number;
}> {
  const res = await worker.fetch(
    new Request(`${BASE}/pending${query}`, { headers: AUTH }),
    env
  );
  expect(res.status).toBe(200);
  return res.json();
}

describe('configuration', () => {
  it('answers 503 on every path while the bearer secret is unset', async () => {
    const env = makeEnv({ SENTRY_RELAY_SECRET: undefined });
    expect((await worker.fetch(post(errorEnvelope()), env)).status).toBe(503);
    expect(
      (
        await worker.fetch(
          new Request(`${BASE}/pending`, { headers: AUTH }),
          env
        )
      ).status
    ).toBe(503);
  });

  it('reads its tunables from wrangler.toml [vars]', () => {
    // The committed defaults are what production runs with.
    expect(VARS).toEqual({
      RETENTION_DAYS: '7',
      MAX_QUEUE_ROWS: '5000',
      MAX_ENVELOPE_BYTES: '262144',
    });
  });

  it('binds the queue as RELAY_DB, the name the Worker reads', () => {
    expect(wranglerToml).toMatch(/^binding = "RELAY_DB"$/m);
  });
});

describe('POST /relay', () => {
  it('queues an error envelope byte-for-byte and answers with CORS', async () => {
    const env = makeEnv();
    const body = errorEnvelope();
    const res = await worker.fetch(post(body), env);
    expect(res.status).toBe(200);
    expect(res.headers.get('Access-Control-Allow-Origin')).toBe(ORIGIN);

    const queued = await pending(env);
    expect(queued.backlog).toBe(1);
    expect(queued.envelopes).toHaveLength(1);
    expect(queued.envelopes[0].project_id).toBe('7');
    expect(queued.envelopes[0].public_key).toBe(KEY);
    expect(queued.envelopes[0].item_types).toEqual(['event']);
    expect(base64ToBytes(queued.envelopes[0].body)).toEqual(body);
  });

  it('keeps binary payload bytes intact', async () => {
    const env = makeEnv();
    const body = envelope([
      { type: 'event', payload: '{"message":"boom"}' },
      {
        type: 'attachment',
        payload: new Uint8Array([0, 255, 10, 128, 13]),
        withLength: true,
      },
    ]);
    expect((await worker.fetch(post(body), env)).status).toBe(200);
    const queued = await pending(env);
    expect(base64ToBytes(queued.envelopes[0].body)).toEqual(body);
  });

  it('accepts a server-side send with no Origin header', async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      new Request(`${BASE}/relay`, { method: 'POST', body: errorEnvelope() }),
      env
    );
    expect(res.status).toBe(200);
    expect(res.headers.get('Access-Control-Allow-Origin')).toBeNull();
    expect((await pending(env)).backlog).toBe(1);
  });

  it('decompresses a gzipped body before queueing it', async () => {
    // The Node SDK gzips envelopes over 32 KB.
    const env = makeEnv();
    const body = errorEnvelope();
    const gzipped = await new Response(
      new Blob([body]).stream().pipeThrough(new CompressionStream('gzip'))
    ).arrayBuffer();
    const res = await worker.fetch(
      post(gzipped, { 'Content-Encoding': 'gzip' }),
      env
    );
    expect(res.status).toBe(200);
    expect(base64ToBytes((await pending(env)).envelopes[0].body)).toEqual(body);
  });

  it('answers 200 but stores nothing for sessions, client reports and transactions', async () => {
    const env = makeEnv();
    for (const type of [
      'session',
      'sessions',
      'client_report',
      'transaction',
    ]) {
      const res = await worker.fetch(
        post(envelope([{ type, payload: '{}' }])),
        env
      );
      expect(res.status).toBe(200);
    }
    expect((await pending(env)).backlog).toBe(0);
  });

  it('refuses a foreign browser origin, before touching the queue', async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      post(errorEnvelope(), { Origin: 'https://evil.example' }),
      env
    );
    expect(res.status).toBe(403);
    expect((await pending(env)).backlog).toBe(0);
  });

  it('refuses a project that is not allowlisted, and fails closed when none is', async () => {
    const other = envelope([{ type: 'event', payload: '{}' }], {
      dsn: `https://${KEY}@relay.example.com/99`,
    });
    expect((await worker.fetch(post(other), makeEnv())).status).toBe(403);
    const unconfigured = makeEnv({ ALLOWED_PROJECT_IDS: undefined });
    expect(
      (await worker.fetch(post(errorEnvelope()), unconfigured)).status
    ).toBe(403);
    expect((await pending(makeEnv())).backlog).toBe(0);
  });

  it('refuses an envelope whose header carries no usable DSN', async () => {
    const env = makeEnv();
    const noDsn = envelope([{ type: 'event', payload: '{}' }], {
      event_id: 'x',
    });
    const badKey = envelope([{ type: 'event', payload: '{}' }], {
      dsn: 'https://not-a-key@relay.example.com/7',
    });
    expect((await worker.fetch(post(noDsn), env)).status).toBe(400);
    expect((await worker.fetch(post(badKey), env)).status).toBe(400);
    expect((await worker.fetch(post('garbage'), env)).status).toBe(400);
  });

  it('refuses an envelope over MAX_ENVELOPE_BYTES', async () => {
    const env = makeEnv({ MAX_ENVELOPE_BYTES: '1024' });
    const big = envelope([
      { type: 'event', payload: `{"m":"${'x'.repeat(2048)}"}` },
    ]);
    expect((await worker.fetch(post(big), env)).status).toBe(413);
  });

  it('answers 503 when the queue is full instead of evicting', async () => {
    const env = makeEnv({ MAX_QUEUE_ROWS: '1' });
    expect((await worker.fetch(post(errorEnvelope()), env)).status).toBe(200);
    const res = await worker.fetch(post(errorEnvelope()), env);
    expect(res.status).toBe(503);
    expect(res.headers.get('Retry-After')).toBe('300');
    expect((await pending(env)).backlog).toBe(1);
  });

  it('answers 429 with Retry-After when rate limited', async () => {
    const env = makeEnv();
    limiterAllows = false;
    const res = await worker.fetch(post(errorEnvelope()), env);
    expect(res.status).toBe(429);
    expect(res.headers.get('Retry-After')).toBe('60');
  });

  it('answers a CORS preflight for the site and refuses one from elsewhere', async () => {
    const env = makeEnv();
    const ok = await worker.fetch(
      new Request(`${BASE}/relay`, {
        method: 'OPTIONS',
        headers: { Origin: ORIGIN },
      }),
      env
    );
    expect(ok.status).toBe(204);
    expect(ok.headers.get('Access-Control-Allow-Origin')).toBe(ORIGIN);
    expect(ok.headers.get('Access-Control-Allow-Methods')).toContain('POST');
    const evil = await worker.fetch(
      new Request(`${BASE}/relay`, {
        method: 'OPTIONS',
        headers: { Origin: 'https://evil.example' },
      }),
      env
    );
    expect(evil.status).toBe(403);
  });

  it('answers 405 to a GET', async () => {
    const res = await worker.fetch(new Request(`${BASE}/relay`), makeEnv());
    expect(res.status).toBe(405);
  });
});

describe('operator read side', () => {
  it('/pending and /ack require the bearer', async () => {
    const env = makeEnv();
    expect(
      (await worker.fetch(new Request(`${BASE}/pending`), env)).status
    ).toBe(401);
    const wrong = { Authorization: 'Bearer nope' };
    expect(
      (
        await worker.fetch(
          new Request(`${BASE}/pending`, { headers: wrong }),
          env
        )
      ).status
    ).toBe(401);
    const ack = new Request(`${BASE}/ack`, {
      method: 'POST',
      body: '{"ids":[1]}',
    });
    expect((await worker.fetch(ack, env)).status).toBe(401);
  });

  it('pages oldest first and reports the whole backlog', async () => {
    const env = makeEnv();
    for (let i = 0; i < 3; i++) {
      const body = envelope([{ type: 'event', payload: `{"n":${i}}` }]);
      expect((await worker.fetch(post(body), env)).status).toBe(200);
    }
    const page = await pending(env, '?limit=2');
    expect(page.backlog).toBe(3);
    expect(page.envelopes.map((e) => e.id)).toEqual([1, 2]);
  });

  it('/ack removes exactly the acked rows', async () => {
    const env = makeEnv();
    for (let i = 0; i < 3; i++) await worker.fetch(post(errorEnvelope()), env);
    const res = await worker.fetch(
      new Request(`${BASE}/ack`, {
        method: 'POST',
        headers: AUTH,
        body: JSON.stringify({ ids: [1, 3, 3, 'x', -1] }),
      }),
      env
    );
    expect(await res.json()).toEqual({ removed: 2 });
    const left = await pending(env);
    expect(left.envelopes.map((e) => e.id)).toEqual([2]);
  });

  it('/ack deletes in chunks past the 100-parameter D1 limit', async () => {
    const env = makeEnv();
    for (let i = 0; i < 3; i++) await worker.fetch(post(errorEnvelope()), env);
    const ids = Array.from({ length: 250 }, (_, i) => i + 1);
    const res = await worker.fetch(
      new Request(`${BASE}/ack`, {
        method: 'POST',
        headers: AUTH,
        body: JSON.stringify({ ids }),
      }),
      env
    );
    expect(await res.json()).toEqual({ removed: 3 });
  });

  it('/ack refuses a malformed body', async () => {
    const env = makeEnv();
    const bad = await worker.fetch(
      new Request(`${BASE}/ack`, {
        method: 'POST',
        headers: AUTH,
        body: 'nope',
      }),
      env
    );
    expect(bad.status).toBe(400);
    const tooMany = await worker.fetch(
      new Request(`${BASE}/ack`, {
        method: 'POST',
        headers: AUTH,
        body: JSON.stringify({
          ids: Array.from({ length: 501 }, (_, i) => i + 1),
        }),
      }),
      env
    );
    expect(tooMany.status).toBe(400);
  });

  it('prunes rows past RETENTION_DAYS and says how many it dropped', async () => {
    // A drain that was down longer than the retention window must show up
    // as lost envelopes, not as a quiet, empty queue.
    const env = makeEnv();
    await worker.fetch(post(errorEnvelope()), env);
    await worker.fetch(post(errorEnvelope()), env);
    await env.RELAY_DB.prepare(
      'UPDATE envelopes SET received_at = ?1 WHERE id = 1'
    )
      .bind('2020-01-01T00:00:00.000Z')
      .run();
    const page = await pending(env);
    expect(page.expired).toBe(1);
    expect(page.backlog).toBe(1);
    expect(page.envelopes.map((e) => e.id)).toEqual([2]);
  });

  it('answers 404 for an unknown path', async () => {
    const res = await worker.fetch(
      new Request(`${BASE}/`, { headers: AUTH }),
      makeEnv()
    );
    expect(res.status).toBe(404);
  });
});
