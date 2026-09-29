// Cloudflare Worker — error-envelope relay for a local-first Poindexter install.
//
// Browsers and the site's serverless functions send Sentry SDK envelopes here
// (the SDK's `tunnel` option). The Worker checks each one and QUEUES it in
// D1. The operator's worker drains the queue with an outbound poll
// (DrainSentryRelayJob) and posts each envelope to its LAN-only GlitchTip.
// Nothing reaches into the LAN, so the tracker needs no public ingress: the
// same split as unsubscribe-relay and ls-webhook-relay.
//
//   browser / serverless fn ──POST /relay──▶ this Worker ──▶ D1 `envelopes`
//                                                               │
//   DrainSentryRelayJob ◀── GET /pending (bearer) ──────────────┘
//                       ──▶ POST /ack   (bearer, once GlitchTip has it)
//
// Why queue instead of forwarding: the first version of this Worker
// forwarded each envelope to GlitchTip through a public tunnel. It was never
// deployed, and it would have been refused anyway: GlitchTip reads the DSN
// key only from `?sentry_key=` or `X-Sentry-Auth`, never from the envelope
// header that a tunnelled envelope carries. The drain job adds the key from
// the columns stored here.
//
// Only envelopes carrying an error-ish item (event, feedback, user_report)
// are stored. Sessions, client reports, transactions and replays are
// answered 200 and dropped, so an SDK default that drifts back on cannot
// fill the queue with page-view noise.
//
// POST /relay    → 200 queued (or dropped by design), 400 malformed,
//                  403 origin or project not allowed, 413 too large,
//                  429 rate limited, 503 queue full
// OPTIONS /relay → 204 CORS preflight (403 for a foreign origin)
// GET  /pending  → 200 {envelopes:[…], backlog, expired}, 401 bad bearer
// POST /ack      → 200 {removed}, 400 bad body, 401 bad bearer
// all paths      → 503 while SENTRY_RELAY_SECRET is unset (fail closed)

export interface Env {
  // D1 queue (wrangler.toml [[d1_databases]]). Created by the first
  // `wrangler deploy`; the table is created on first use.
  RELAY_DB: D1Database;
  // Workers rate-limiting binding (wrangler.toml [[unsafe.bindings]]).
  RATE_LIMITER: RateLimit;
  // The three below are Worker SECRETS (`wrangler secret put <NAME>`), so
  // each binding is absent until the operator sets it. wrangler.toml must not
  // declare them under [vars]: a plain-text var of the same name would
  // clobber the secret on the next deploy.
  //
  // Comma-separated browser Origin values allowed to POST, e.g.
  // "https://example.com,https://www.example.com". Unset disables the check
  // (local dev only). Requests without an Origin header (serverless
  // functions) skip it and rely on the rate limit and project allowlist.
  ALLOWED_ORIGINS?: string;
  // Comma-separated GlitchTip project ids the relay will queue. The
  // open-proxy guard: an envelope for any other project is refused. Unset =
  // fail closed (queues nothing).
  ALLOWED_PROJECT_IDS?: string;
  // Bearer for the read side (/pending, /ack). The operator's
  // app_settings.sentry_relay_secret holds the same value. Unset → 503
  // everywhere: an open /pending would hand out visitors' error reports.
  SENTRY_RELAY_SECRET?: string;
  // Tunables ([vars] in wrangler.toml).
  RETENTION_DAYS?: string;
  MAX_QUEUE_ROWS?: string;
  MAX_ENVELOPE_BYTES?: string;
}

/** Item types worth a GlitchTip issue. Everything else is dropped at the edge. */
export const STORED_ITEM_TYPES: ReadonlySet<string> = new Set([
  'event',
  'feedback',
  'user_report',
]);

const DEFAULT_RETENTION_DAYS = 7;
const DEFAULT_MAX_QUEUE_ROWS = 5000;
const DEFAULT_MAX_ENVELOPE_BYTES = 256 * 1024;
const DEFAULT_PENDING_LIMIT = 25;
const MAX_PENDING_LIMIT = 50;
const MAX_ACK_IDS = 500;
// D1 caps bound parameters per statement at 100.
const ACK_CHUNK = 100;

const NEWLINE = 0x0a;
const utf8 = new TextDecoder();

// ---------------------------------------------------------------------------
// Pure helpers (exported for tests)
// ---------------------------------------------------------------------------

export interface EnvelopeSummary {
  header: Record<string, unknown>;
  itemTypes: string[];
}

function parseJsonObject(bytes: Uint8Array): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(utf8.decode(bytes));
    return typeof parsed === 'object' &&
      parsed !== null &&
      !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** The bytes from `start` up to the next newline (or the end), and where the next line starts. */
function lineAt(
  bytes: Uint8Array,
  start: number
): { line: Uint8Array; next: number } {
  let end = bytes.indexOf(NEWLINE, start);
  if (end === -1) end = bytes.length;
  return { line: bytes.subarray(start, end), next: end + 1 };
}

/**
 * Walk a Sentry envelope: the header line, then each item header and its
 * payload. A payload with a `length` is exactly that many bytes and may
 * contain newlines (compressed replays, attachments); one without runs to the
 * next newline. Returns null for anything that is not a well-formed envelope.
 */
export function inspectEnvelope(bytes: Uint8Array): EnvelopeSummary | null {
  if (bytes.length === 0) return null;
  const first = lineAt(bytes, 0);
  const header = parseJsonObject(first.line);
  if (!header) return null;

  const itemTypes: string[] = [];
  let pos = first.next;
  while (pos < bytes.length) {
    const itemHead = lineAt(bytes, pos);
    if (itemHead.line.length === 0) {
      // A trailing newline, or blank padding between items.
      pos = itemHead.next;
      continue;
    }
    const item = parseJsonObject(itemHead.line);
    if (!item || typeof item.type !== 'string') return null;
    itemTypes.push(item.type);

    if (item.length !== undefined) {
      const length = item.length;
      if (
        typeof length !== 'number' ||
        !Number.isInteger(length) ||
        length < 0
      ) {
        return null;
      }
      const end = itemHead.next + length;
      if (end > bytes.length) return null; // truncated payload
      pos = end < bytes.length && bytes[end] === NEWLINE ? end + 1 : end;
    } else {
      pos = lineAt(bytes, itemHead.next).next;
    }
  }
  return { header, itemTypes };
}

/**
 * Extract { host, projectId, publicKey } from a Sentry DSN
 * (`https://<publicKey>@<host>/<projectId>`), or null when it isn't one.
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

const PROJECT_ID_RE = /^[1-9][0-9]{0,9}$/;
// GlitchTip keys are UUIDs, accepted with or without dashes.
const PUBLIC_KEY_RE =
  /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;

export function isValidProjectId(projectId: string): boolean {
  return PROJECT_ID_RE.test(projectId);
}

export function isValidPublicKey(publicKey: string): boolean {
  return PUBLIC_KEY_RE.test(publicKey);
}

/**
 * Open-proxy guard: true only when `projectId` is in the comma-separated
 * allowlist. An empty or blank allowlist returns false (fail closed), so an
 * unconfigured relay never queues arbitrary projects.
 */
export function projectAllowed(
  projectId: string,
  allowlistCsv: string | undefined
): boolean {
  return csv(allowlistCsv).includes(projectId);
}

/** Constant-time string compare, so the bearer can't be recovered by timing. */
export function secureEquals(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function bytesToBase64(bytes: Uint8Array): string {
  // btoa takes a binary string. Build it in chunks: spreading a large
  // array into fromCharCode overflows the call stack.
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function csv(value: string | undefined): string[] {
  return (value || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function positiveInt(raw: string | undefined, fallback: number): number {
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : fallback;
}

// ---------------------------------------------------------------------------
// Body reading
// ---------------------------------------------------------------------------

const TOO_LARGE = Symbol('too-large');

/**
 * Read a request body into bytes, decompressing gzip (the Node SDK gzips
 * envelopes over 32 KB and says so in Content-Encoding) and refusing
 * anything past `maxBytes` after decompression.
 */
async function readBody(
  req: Request,
  maxBytes: number
): Promise<Uint8Array | typeof TOO_LARGE | null> {
  const declared = Number(req.headers.get('Content-Length'));
  if (Number.isFinite(declared) && declared > maxBytes) return TOO_LARGE;
  if (!req.body) return null;

  const encoding = (req.headers.get('Content-Encoding') || '').toLowerCase();
  let stream: ReadableStream<Uint8Array> = req.body;
  if (encoding.includes('gzip')) {
    stream = stream.pipeThrough(new DecompressionStream('gzip'));
  } else if (encoding && encoding !== 'identity') {
    return null;
  }

  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        return TOO_LARGE;
      }
      chunks.push(value);
    }
  } catch {
    // A corrupt gzip stream lands here.
    return null;
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

// ---------------------------------------------------------------------------
// D1 queue
// ---------------------------------------------------------------------------

// One statement per prepare(): D1's exec() splits its input on newlines.
const CREATE_TABLE =
  'CREATE TABLE IF NOT EXISTS envelopes (' +
  'id INTEGER PRIMARY KEY AUTOINCREMENT, ' +
  'received_at TEXT NOT NULL, ' +
  'project_id TEXT NOT NULL, ' +
  'public_key TEXT NOT NULL, ' +
  'item_types TEXT NOT NULL, ' +
  'body_b64 TEXT NOT NULL)';

// Per isolate. A cold isolate pays one extra no-op statement.
let schemaReady = false;

async function ensureSchema(db: D1Database): Promise<void> {
  if (schemaReady) return;
  await db.prepare(CREATE_TABLE).run();
  schemaReady = true;
}

/** Test seam: forget that the schema exists (each test gets a fresh D1). */
export function resetSchemaCacheForTests(): void {
  schemaReady = false;
}

interface QueuedRow {
  id: number;
  received_at: string;
  project_id: string;
  public_key: string;
  item_types: string;
  body_b64: string;
}

// ---------------------------------------------------------------------------
// Responses
// ---------------------------------------------------------------------------

function corsHeaders(origin: string | null): Record<string, string> {
  return origin
    ? { 'Access-Control-Allow-Origin': origin, Vary: 'Origin' }
    : {};
}

function empty(status: number, headers: Record<string, string> = {}): Response {
  return new Response(null, { status, headers });
}

function json(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {}
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

function bearerOk(req: Request, secret: string): boolean {
  const header = req.headers.get('Authorization') || '';
  if (!header.startsWith('Bearer ')) return false;
  return secureEquals(header.slice(7), secret);
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async function handleRelay(req: Request, env: Env): Promise<Response> {
  const origin = req.headers.get('Origin');
  const allowedOrigins = csv(env.ALLOWED_ORIGINS);
  if (origin && allowedOrigins.length > 0 && !allowedOrigins.includes(origin)) {
    return empty(403);
  }
  const cors = corsHeaders(origin);

  if (req.method === 'OPTIONS') {
    return empty(204, {
      ...cors,
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
    });
  }
  if (req.method !== 'POST') return empty(405, cors);

  // Per-IP rate limit. CF-Connecting-IP is the edge-observed client IP, not
  // spoofable via X-Forwarded-For. 'unknown' in local wrangler dev.
  const ip = req.headers.get('CF-Connecting-IP') || 'unknown';
  const { success } = await env.RATE_LIMITER.limit({ key: ip });
  if (!success) return empty(429, { ...cors, 'Retry-After': '60' });

  const maxBytes = positiveInt(
    env.MAX_ENVELOPE_BYTES,
    DEFAULT_MAX_ENVELOPE_BYTES
  );
  const body = await readBody(req, maxBytes);
  if (body === TOO_LARGE) return empty(413, cors);
  if (!body) return empty(400, cors);

  const envelope = inspectEnvelope(body);
  if (!envelope) return empty(400, cors);
  // A tunnelled envelope names its DSN in the header. That DSN is the only
  // place the project id and public key travel.
  const dsn =
    typeof envelope.header.dsn === 'string' ? envelope.header.dsn : '';
  const parts = dsn ? extractDsnParts(dsn) : null;
  if (
    !parts ||
    !isValidProjectId(parts.projectId) ||
    !isValidPublicKey(parts.publicKey)
  ) {
    return empty(400, cors);
  }
  if (!projectAllowed(parts.projectId, env.ALLOWED_PROJECT_IDS)) {
    return empty(403, cors);
  }

  if (!envelope.itemTypes.some((t) => STORED_ITEM_TYPES.has(t))) {
    // Sessions, client reports, transactions, replays: nothing GlitchTip
    // would raise an issue for. 200 so the SDK doesn't retry or back off.
    return empty(200, cors);
  }

  const maxRows = positiveInt(env.MAX_QUEUE_ROWS, DEFAULT_MAX_QUEUE_ROWS);
  await ensureSchema(env.RELAY_DB);
  const result = await env.RELAY_DB.prepare(
    'INSERT INTO envelopes (received_at, project_id, public_key, item_types, body_b64) ' +
      'SELECT ?1, ?2, ?3, ?4, ?5 WHERE (SELECT COUNT(*) FROM envelopes) < ?6'
  )
    .bind(
      new Date().toISOString(),
      parts.projectId,
      parts.publicKey,
      envelope.itemTypes.join(','),
      bytesToBase64(body),
      maxRows
    )
    .run();
  if (!result.meta.changes) {
    // Queue full: the drain has stopped. Refuse rather than evict, so the
    // backlog the operator will read is the oldest, not a random sample.
    return empty(503, { ...cors, 'Retry-After': '300' });
  }
  return empty(200, cors);
}

async function handlePending(
  req: Request,
  env: Env,
  url: URL
): Promise<Response> {
  const limit = Math.min(
    positiveInt(
      url.searchParams.get('limit') ?? undefined,
      DEFAULT_PENDING_LIMIT
    ),
    MAX_PENDING_LIMIT
  );
  const retentionDays = positiveInt(env.RETENTION_DAYS, DEFAULT_RETENTION_DAYS);
  const cutoff = new Date(
    Date.now() - retentionDays * 86_400_000
  ).toISOString();

  await ensureSchema(env.RELAY_DB);
  const [pruned, rows, count] = await env.RELAY_DB.batch([
    // ISO-8601 UTC strings order the same as the instants they name.
    env.RELAY_DB.prepare('DELETE FROM envelopes WHERE received_at < ?1').bind(
      cutoff
    ),
    env.RELAY_DB.prepare(
      'SELECT id, received_at, project_id, public_key, item_types, body_b64 ' +
        'FROM envelopes ORDER BY id LIMIT ?1'
    ).bind(limit),
    env.RELAY_DB.prepare('SELECT COUNT(*) AS backlog FROM envelopes'),
  ]);

  const envelopes = (rows.results as unknown as QueuedRow[]).map((r) => ({
    id: r.id,
    received_at: r.received_at,
    project_id: r.project_id,
    public_key: r.public_key,
    item_types: r.item_types ? r.item_types.split(',') : [],
    body: r.body_b64,
  }));
  const backlog = Number(
    (count.results[0] as { backlog?: number } | undefined)?.backlog ?? 0
  );
  return json({ envelopes, backlog, expired: pruned.meta.changes ?? 0 });
}

async function handleAck(req: Request, env: Env): Promise<Response> {
  let body: { ids?: unknown };
  try {
    body = await req.json();
  } catch {
    return json({ error: 'invalid json' }, 400);
  }
  if (!Array.isArray(body.ids) || body.ids.length > MAX_ACK_IDS) {
    return json(
      { error: `ids must be an array of at most ${MAX_ACK_IDS}` },
      400
    );
  }
  const ids = body.ids.filter(
    (id): id is number =>
      typeof id === 'number' && Number.isInteger(id) && id > 0
  );
  if (ids.length === 0) return json({ removed: 0 });

  await ensureSchema(env.RELAY_DB);
  const statements: D1PreparedStatement[] = [];
  for (let i = 0; i < ids.length; i += ACK_CHUNK) {
    const chunk = ids.slice(i, i + ACK_CHUNK);
    const placeholders = chunk.map((_, j) => `?${j + 1}`).join(', ');
    statements.push(
      env.RELAY_DB.prepare(
        `DELETE FROM envelopes WHERE id IN (${placeholders})`
      ).bind(...chunk)
    );
  }
  const results = await env.RELAY_DB.batch(statements);
  const removed = results.reduce((sum, r) => sum + (r.meta.changes ?? 0), 0);
  return json({ removed });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const secret = env.SENTRY_RELAY_SECRET;
    if (!secret) {
      // Fail closed. Queueing what nobody can drain would only turn D1 into
      // free public storage, and an open /pending would leak error reports.
      return json({ error: 'relay not configured' }, 503);
    }

    const url = new URL(req.url);
    switch (url.pathname) {
      case '/relay':
        return handleRelay(req, env);
      case '/pending':
        if (req.method !== 'GET') return empty(405);
        if (!bearerOk(req, secret)) return json({ error: 'unauthorized' }, 401);
        return handlePending(req, env, url);
      case '/ack':
        if (req.method !== 'POST') return empty(405);
        if (!bearerOk(req, secret)) return json({ error: 'unauthorized' }, 401);
        return handleAck(req, env);
      default:
        return json({ error: 'not found' }, 404);
    }
  },
};
