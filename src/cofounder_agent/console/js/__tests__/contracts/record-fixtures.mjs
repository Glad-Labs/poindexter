#!/usr/bin/env node
// Refresh contract fixtures + the pruned OpenAPI snapshot from a live worker.
// Reads (GET) only — write surfaces are never invoked (a real POST would mutate
// prod).
//
// Exit codes (the nightly workflow branches on these):
//   0  artifacts match the live backend — no drift
//   1  schema DRIFT — snapshot + fixtures refreshed; open a review PR
//   2  operational — worker unreachable or bad creds; NOT drift, green-skip
//
// A connection failure must never surface as exit 1: the workflow would misread
// it as drift and try to open an empty PR. Every fetch path is guarded so a
// connection error maps to exit 2 (see the connErrCode classifier + main catch).
//
//   node record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091
//   (in a container — e.g. the CI runner — use --base http://host.docker.internal:8002)
//   (creds via --client-id/--client-secret or CONSOLE_CONTRACT_CLIENT_ID/_SECRET env)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { isDeepStrictEqual } from 'node:util';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const {
  loadApiWithFetch,
  pruneOpenApiSnapshot,
} = require('./contract-runtime.js');
const manifest = require('./contracts.manifest.js');

const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i >= 0 ? process.argv[i + 1] : d;
};
const BASE = arg('--base', 'http://localhost:8002');
const PROM = arg('--prometheus', 'http://localhost:9091');
// This app relocates the OpenAPI schema under /api (the default /openapi.json is
// disabled, alongside /docs) — see the /api/docs Swagger UI.
const OPENAPI_PATH = arg('--openapi-path', '/api/openapi.json');
const CLIENT_ID = arg('--client-id', process.env.CONSOLE_CONTRACT_CLIENT_ID);
const CLIENT_SECRET = arg(
  '--client-secret',
  process.env.CONSOLE_CONTRACT_CLIENT_SECRET
);
const FIX_DIR = path.join(__dirname, 'fixtures');

if (!CLIENT_ID || !CLIENT_SECRET) {
  console.error(
    'Missing OAuth client creds (--client-id/--client-secret or env).'
  );
  process.exit(2);
}
fs.mkdirSync(FIX_DIR, { recursive: true });

// A read row is one that declares a fixture/shape/openapi and whose (single or
// first) expected request is a GET — i.e. a surface whose response we replay.
function firstReq(row) {
  return Array.isArray(row.request) ? row.request[0] : row.request;
}
function isReadRow(row) {
  const r = firstReq(row);
  return (
    (row.fixture || row.shape || row.openapi) &&
    r &&
    (r.method || 'GET') === 'GET'
  );
}

// Record one row: load api.js with a forwarding fetch that hits the real worker
// / Prometheus (via cfg.base / cfg.prometheus) and captures the FIRST non-/token
// raw JSON response, then run the row's invoke.
async function recordRow(row) {
  let raw = null;
  const forwarding = async (url, opts) => {
    const res = await fetch(url, opts); // absolute URLs — api.js built them from cfg
    if (!String(url).endsWith('/token') && raw === null) {
      try {
        raw = await res.clone().json();
      } catch {
        raw = null;
      }
    }
    return res;
  };
  const api = loadApiWithFetch(forwarding, {
    px_base: BASE,
    px_prom: PROM,
    px_client_id: CLIENT_ID,
    px_client_secret: CLIENT_SECRET,
    px_live: '1',
  });
  await row.invoke(api);
  return raw;
}

async function mintToken() {
  const form = new URLSearchParams({
    grant_type: 'client_credentials',
    client_id: CLIENT_ID,
    client_secret: CLIENT_SECRET,
  });
  const r = await fetch(BASE + '/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  });
  if (!r.ok) throw new Error(`/token → ${r.status}`);
  return (await r.json()).access_token;
}

// Dump the full OpenAPI schema and prune it to the console's drift surface —
// the manifest-anchored paths, their SUCCESS (2xx) responses only, and the
// transitive component closure of those. The pruning (and its rationale) lives
// in contract-runtime.pruneOpenApiSnapshot so it is unit-tested against a fake
// doc; here we just fetch + hand it the manifest's anchored paths.
async function snapshot(tok) {
  const full = await (
    await fetch(BASE + OPENAPI_PATH, {
      headers: { Authorization: 'Bearer ' + tok },
    })
  ).json();
  const wantPaths = new Set();
  manifest.forEach((row) => {
    if (row.openapi) wantPaths.add(row.openapi.path);
  });
  return pruneOpenApiSnapshot(full, wantPaths);
}

// Print a human-readable path-level schema diff (added / removed / changed).
function printSchemaDiff(oldSnap, newSnap) {
  const op = Object.keys(oldSnap.paths || {});
  const np = Object.keys(newSnap.paths || {});
  const added = np.filter((p) => !op.includes(p));
  const removed = op.filter((p) => !np.includes(p));
  const changed = np.filter(
    (p) =>
      op.includes(p) && !isDeepStrictEqual(oldSnap.paths[p], newSnap.paths[p])
  );
  if (added.length) console.log('  + added:   ' + added.join(', '));
  if (removed.length) console.log('  - removed: ' + removed.join(', '));
  if (changed.length) console.log('  ~ changed: ' + changed.join(', '));
  if (!added.length && !removed.length && !changed.length) {
    console.log(
      '  ~ components/schemas changed (referenced model shape moved)'
    );
  }
}

// ── The OpenAPI snapshot is the drift ANCHOR ──────────────────────────────
// "Drift" means the backend changed a declared shape — NOT that a fixture's
// live value (a timestamp, a spend figure) moved. So the verdict compares the
// freshly-dumped snapshot to the committed one SEMANTICALLY (isDeepStrictEqual
// is order- and format-insensitive: it survives FastAPI reordering its schema
// keys and prettier reformatting the committed file). Fixtures are re-recorded
// only when the schema drifts (or --force), so a volatile `last_updated` never
// churns a nightly PR.
const FORCE = process.argv.includes('--force');
const snapPath = path.join(__dirname, 'openapi.snapshot.json');

// A connection failure (worker down, DNS, timeout) is NOT schema drift — it's
// infrastructure. Walk the error's `cause` chain (undici wraps the socket error
// under TypeError: fetch failed) for a known connect-level code. main()'s catch
// maps a hit to exit 2 so the nightly green-skips instead of misreading a
// down worker as drift and opening an empty PR.
const CONN_CODES = new Set([
  'ECONNREFUSED',
  'ENOTFOUND',
  'EAI_AGAIN',
  'ETIMEDOUT',
  'ECONNRESET',
  'UND_ERR_CONNECT_TIMEOUT',
  'UND_ERR_SOCKET',
]);
function connErrCode(err, seen = new Set()) {
  // Walk BOTH the .cause chain (undici wraps the socket error one level down)
  // AND .errors[] (an AggregateError — what you get when the host resolves to
  // several addresses, e.g. localhost → ::1 + 127.0.0.1, or host.docker.internal
  // on the CI runner — and every attempt is refused). The seen-set guards cycles.
  let e = err;
  while (e && typeof e === 'object' && !seen.has(e)) {
    seen.add(e);
    if (typeof e.code === 'string' && CONN_CODES.has(e.code)) return e.code;
    if (Array.isArray(e.errors)) {
      for (const sub of e.errors) {
        const c = connErrCode(sub, seen);
        if (c) return c;
      }
    }
    e = e.cause;
  }
  return null;
}

async function main() {
  const fresh = await snapshot(await mintToken());
  const committed = fs.existsSync(snapPath)
    ? JSON.parse(fs.readFileSync(snapPath, 'utf8'))
    : null;
  const schemaDrift = !committed || !isDeepStrictEqual(fresh, committed);

  if (schemaDrift && committed) {
    console.log('── SCHEMA DRIFT vs committed snapshot ──');
    printSchemaDiff(committed, fresh);
  } else if (schemaDrift) {
    console.log('No committed snapshot yet — recording a fresh baseline.');
  }

  if (schemaDrift || FORCE) {
    const readRows = manifest.filter(isReadRow);
    console.log(`Recording ${readRows.length} read fixtures from ${BASE} …`);
    const skipped = [];
    for (const row of readRows) {
      const file = path.join(FIX_DIR, row.fixture || `${row.name}.json`);
      try {
        const raw = await recordRow(row);
        fs.writeFileSync(file, JSON.stringify(raw, null, 2) + '\n');
        console.log(`  ✓ ${path.basename(file)}`);
      } catch (e) {
        // A connection error mid-run means the worker went away — abort as
        // infrastructure (rethrow → exit 2) rather than half-write fixtures and
        // then report "drift". A per-row APP error (one slow/unreachable
        // endpoint) is still just warned + skipped so one flaky read never
        // aborts the whole nightly.
        if (connErrCode(e)) throw e;
        skipped.push(row.name);
        console.warn(`  ⚠ ${row.name} skipped: ${e.message}`);
      }
    }
    fs.writeFileSync(snapPath, JSON.stringify(fresh, null, 2) + '\n');
    console.log('  ✓ openapi.snapshot.json');
    if (skipped.length) {
      console.warn(`Skipped ${skipped.length}: ${skipped.join(', ')}`);
    }
  }

  // Set exitCode and let the event loop drain naturally. Calling process.exit()
  // here crashes libuv on Windows (UV_HANDLE_CLOSING) because fetch's keep-alive
  // sockets are still tearing down; a hard exit mid-teardown is the bug.
  if (schemaDrift) {
    console.log(
      '\nSchema drift detected → artifacts refreshed. Review + commit.'
    );
    process.exitCode = 1;
  } else {
    console.log('\nNo schema drift — snapshot matches the live backend.');
    process.exitCode = 0;
  }
}

main().catch((err) => {
  const code = connErrCode(err);
  if (code) {
    // Worker unreachable — a normal state when the local stack is down. Exit 2
    // (operational, NOT drift): the workflow green-skips the PR/ping path and
    // retries next cycle. Never exit 1 here — that reads as schema drift.
    console.error(
      `Worker unreachable at ${BASE} (${code}) — skipping drift check ` +
        '(exit 2, not drift).'
    );
  } else {
    // Any other unexpected failure is still operational, not drift.
    console.error('Recorder error (exit 2, not drift):', err && err.message);
    if (err && err.stack) console.error(err.stack);
  }
  process.exitCode = 2;
});
