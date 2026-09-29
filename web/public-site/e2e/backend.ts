/**
 * The FastAPI backend that the backend-calling E2E specs talk to.
 * ==============================================================
 *
 * The one place the API base URL is resolved. auth, task-workflow,
 * workflow-capability and manual-publish-pipeline read API_URL from here;
 * integration-tests and fixtures-validation reach it through fixtures.ts's
 * APIClient; global-setup.ts reads it too. Before this module, five of those
 * specs hardcoded http://localhost:8000 and ignored PLAYWRIGHT_API_URL, so
 * the CI full lane could not be aimed at a stack, and three of them skipped
 * when :8000 did not answer.
 *
 * requireBackend() is the precondition each of those specs runs before every
 * test that calls the backend. An unusable backend FAILS that test whenever
 * the run names a target (PLAYWRIGHT_API_URL or SKIP_SERVER_START) or runs in
 * CI. Only a bare local run, with none of those set, skips it, so a skip can
 * never turn a configured or CI run green without checking anything.
 *
 * Gate per test (test.beforeEach, or a fixture), not in test.beforeAll. When
 * a beforeAll throws, Playwright fails one test and records the rest of the
 * group as skipped, which is the signal this module exists to remove.
 */

import { request, test, type APIRequestContext } from '@playwright/test';

/**
 * The documented local API. `npm run dev:cofounder` serves uvicorn on :8002,
 * and the Docker worker publishes 8002:8002. Nothing in this repo listens on
 * :8000, the old default, so a run that relied on it always missed the
 * backend. On the operator PC :8002 is the production worker. It refuses
 * `Bearer dev-token`, so requireBackend({ devToken: true }) stops the dev-token
 * specs there before they send a request.
 */
export const DEFAULT_API_URL = 'http://localhost:8002';

/**
 * The Authorization value of the dev-token specs. Only a DEVELOPMENT_MODE
 * backend accepts it (src/cofounder_agent/middleware/api_token_auth.py), and
 * production refuses it by design. A spec that sends it must call
 * requireBackend({ devToken: true }) first.
 */
export const DEV_TOKEN_AUTH = 'Bearer dev-token';

// CI and SKIP_SERVER_START are read with the same `!!` test as the root
// playwright.config.ts, so a run this module treats as configured is one the
// config treats the same way (any non-empty value counts). The config can't
// import this module (see its comment), so keep the two in step.

/** GitHub Actions sets CI=true. */
export const IS_CI = !!process.env.CI;

/**
 * SKIP_SERVER_START: the site is already running, so the config boots no
 * webServer. A run that says its stack is already up expects the backend to
 * be up too.
 */
export const SKIP_SERVER_START = !!process.env.SKIP_SERVER_START;

const API_URL_OVERRIDE = (process.env.PLAYWRIGHT_API_URL ?? '').trim();

/**
 * PLAYWRIGHT_API_URL, else DEFAULT_API_URL. Empty counts as unset, which is
 * what an unset GitHub repo variable passes through as.
 */
export const API_URL = (API_URL_OVERRIDE || DEFAULT_API_URL).replace(
  /\/+$/,
  ''
);

/** The variables that make this run strict, e.g. "SKIP_SERVER_START and CI". */
const STRICT_VARS = [
  API_URL_OVERRIDE && 'PLAYWRIGHT_API_URL',
  SKIP_SERVER_START && 'SKIP_SERVER_START',
  IS_CI && 'CI',
].filter(Boolean) as string[];

/**
 * Whether an unusable backend fails a backend test (true) or may skip it
 * (false). True when the run names a target or runs in CI.
 */
export const BACKEND_REQUIRED = STRICT_VARS.length > 0;

export interface BackendNeeds {
  /**
   * The test authenticates as `Bearer dev-token`, so the backend must accept
   * it. That means a DEVELOPMENT_MODE backend, never production.
   */
  devToken?: boolean;
}

/** What one probe of API_URL found. Each field is null when that check passed. */
export interface BackendProbe {
  /** GET /health: why the backend is not usable at all. */
  health: string | null;
  /** GET /api/tasks as dev-token: why a dev-token test can't run. */
  devToken: string | null;
}

const PROBE_TIMEOUT_MS = 5_000;

/**
 * Probe the backend: GET /health, then, if that answers, one read-only
 * GET /api/tasks?limit=1 as dev-token. Never throws.
 */
export async function probeBackend(): Promise<BackendProbe> {
  const context = await request.newContext();
  try {
    const health = await probeHealth(context);
    return { health, devToken: health ?? (await probeDevToken(context)) };
  } finally {
    await context.dispose();
  }
}

/**
 * global-setup.ts probes once per run and hands the result to every worker in
 * this env var (Playwright's documented way to pass data from global setup to
 * tests). A failed test restarts its worker, which loses any in-process memo,
 * so without this every failing test would probe again: one timeout each
 * against a dead backend, one refused dev-token request each against a live
 * one.
 */
const PROBE_ENV = 'PLAYWRIGHT_E2E_BACKEND_PROBE';

export function recordBackendProbe(probe: BackendProbe): void {
  process.env[PROBE_ENV] = JSON.stringify({ url: API_URL, ...probe });
}

/** The recorded probe of this API_URL, or undefined if there is none. */
function recordedBackendProbe(): BackendProbe | undefined {
  const raw = process.env[PROBE_ENV];
  if (!raw) return undefined;
  try {
    const { url, health, devToken } = JSON.parse(raw);
    return url === API_URL ? { health, devToken } : undefined;
  } catch {
    return undefined;
  }
}

/** Probed at most once per worker when global setup recorded nothing. */
let localProbe: Promise<BackendProbe> | undefined;

/**
 * Check the backend before a test calls it. Call it from test.beforeEach or
 * from a fixture, so it runs for each test (see the module comment).
 *
 * When the backend is unreachable, unhealthy, or (with devToken) refuses
 * `Bearer dev-token`, this throws, failing the test, whenever
 * BACKEND_REQUIRED. On a bare local run it skips the test instead and says
 * why, since there is nothing configured to check.
 */
export async function requireBackend(needs: BackendNeeds = {}): Promise<void> {
  const probe =
    recordedBackendProbe() ?? (await (localProbe ??= probeBackend()));
  const problem = probe.health ?? (needs.devToken ? probe.devToken : null);
  if (!problem) return;

  if (BACKEND_REQUIRED) {
    const names = STRICT_VARS.join(', ').replace(/, ([^,]*)$/, ' and $1');
    throw new Error(
      `${problem}\nThis run requires the backend because ${names} ` +
        `${STRICT_VARS.length > 1 ? 'are' : 'is'} set. API base: ${API_URL} (${
          API_URL_OVERRIDE
            ? 'from PLAYWRIGHT_API_URL'
            : 'the default; PLAYWRIGHT_API_URL is unset'
        }).`
    );
  }
  test.skip(
    true,
    `${problem} Skipped only because this local run names no target ` +
      '(PLAYWRIGHT_API_URL, SKIP_SERVER_START and CI are unset); set ' +
      'PLAYWRIGHT_API_URL to run it.'
  );
}

async function probeHealth(context: APIRequestContext): Promise<string | null> {
  try {
    const health = await context.get(`${API_URL}/health`, {
      timeout: PROBE_TIMEOUT_MS,
    });
    return health.ok()
      ? null
      : `Backend at ${API_URL} is not answering as the Poindexter API: GET /health returned ${health.status()}.`;
  } catch (error) {
    return `Backend unreachable: GET ${API_URL}/health failed (${firstLine(error)}).`;
  }
}

async function probeDevToken(
  context: APIRequestContext
): Promise<string | null> {
  let tasks;
  try {
    tasks = await context.get(`${API_URL}/api/tasks?limit=1`, {
      headers: { Authorization: DEV_TOKEN_AUTH },
      timeout: PROBE_TIMEOUT_MS,
    });
  } catch (error) {
    return `Backend at ${API_URL} failed GET /api/tasks with dev-token (${firstLine(error)}).`;
  }
  if (tasks.status() === 401) {
    return (
      `Backend at ${API_URL} refuses Bearer dev-token (GET /api/tasks returned 401: ` +
      `${await detailOf(tasks)}). This test authenticates as dev-token, so it needs a ` +
      'DEVELOPMENT_MODE backend: app_settings development_mode=true and ' +
      'environment=development (middleware/api_token_auth.py). Production refuses ' +
      'dev-token by design; never point this test at it.'
    );
  }
  if (!tasks.ok()) {
    return `Backend at ${API_URL} did not accept Bearer dev-token: GET /api/tasks returned ${tasks.status()} (${await detailOf(tasks)}).`;
  }
  return null;
}

function firstLine(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.split('\n')[0];
}

/**
 * The reason in an error response: `message` from the API's error envelope
 * ({error_code, message, request_id}), or `detail` from a middleware refusal.
 */
async function detailOf(response: {
  text(): Promise<string>;
}): Promise<string> {
  const text = await response.text().catch(() => '');
  try {
    const body = JSON.parse(text);
    const reason = body?.message ?? body?.detail;
    if (typeof reason === 'string') return reason;
  } catch {
    // Not JSON: fall through to the raw body.
  }
  return text.slice(0, 200) || 'empty body';
}
