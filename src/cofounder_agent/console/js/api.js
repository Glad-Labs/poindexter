/* ══════════════════════════════════════════════════════════════
   Poindexter Operator Console — API ADAPTER (PX.api)
   ──────────────────────────────────────────────────────────────
   This is the ONE place that talks to your stack. Today every method
   returns the mock data the console already uses, so nothing breaks.
   Flip it to live incrementally — endpoint by endpoint — without
   touching any UI code.

   ── How to go live ────────────────────────────────────────────
   1. SERVE THIS CONSOLE FROM THE WORKER (recommended): mount the
      static files behind FastAPI so the page origin == the API
      origin. Then BASE can stay '' (same-origin) and there is no
      CORS to configure. Otherwise add your console's origin to the
      worker's CORSMiddleware allow_origins and set BASE below.
   2. SET OAUTH CREDS: routes use verify_api_token, which now accepts ONLY
      an OAuth2 JWT (static Bearer was removed in #249). Provision a DEDICATED
      console client — `poindexter auth register-client --name
      poindexter-console --scopes "api:read api:write" --grant-type
      client_credentials` (prints the secret once) — and call
      PX.api.setClient('client_id','client_secret'). The adapter mints a
      short-lived JWT from POST /token and refreshes it automatically.
   3. FLIP THE SWITCH: PX.api.setLive(true)  (persists). Or set
      window.PX_API_LIVE = true before this script loads.
   4. GO ONE AT A TIME: each method below has a `live:` branch and a
      `mock:` branch. Implement/verify the live branch for one
      surface, leave the rest on mock, repeat. Search "TODO(live)".

   Endpoint map (all confirmed in src/cofounder_agent/routes/):
     settings      GET  /api/settings           · PUT /api/settings/{id}
     approvals     GET  /api/approvals          · POST /api/approvals/{id}/{approve|reject}
     tasks         GET  /api/tasks, /{id}        · POST /api/tasks/{id}/{retry|cancel}
     events        GET  /api/pipeline/events
     memory        GET  /api/memory/stats, /api/memory/search
     probes        GET  /probes
     posts         GET  /api/posts
     analytics     GET  /api/analytics/views
     gpu           Prometheus GET /api/v1/query  (separate origin, :9090)
   ══════════════════════════════════════════════════════════════ */
(function () {
  const LS = window.localStorage;
  const cfg = {
    // Same-origin when served from the worker. Else e.g. 'http://localhost:8002'
    base: LS.getItem('px_base') ?? '',
    // Prometheus is a different service/port; GPU + some rates come from here.
    // NOTE: the local stack runs Prometheus on :9091 (not the upstream default :9090).
    prometheus: LS.getItem('px_prom') ?? 'http://localhost:9091',
    // OAuth2 client-credentials. Static Bearer was removed in #249 — every
    // request now rides a short-lived JWT minted from POST /token. Provision a
    // dedicated client with `poindexter auth register-client --name
    // poindexter-console` (it prints the secret once — paste it below).
    clientId: LS.getItem('px_client_id') ?? '',
    clientSecret: LS.getItem('px_client_secret') ?? '',
    scope: LS.getItem('px_scope') ?? '',
    live: (window.PX_API_LIVE ?? false) || LS.getItem('px_live') === '1',
    // DEV-ONLY: simulate real-world async on the MOCK branch so we can test
    // loading / error / empty states without a backend. Ignored when live.
    sim: LS.getItem('px_sim') ?? 'normal', // normal | slow | error | empty
  };

  // In-memory OAuth token cache (never persisted — short-lived JWT).
  let _tok = { value: '', exp: 0 };

  // Mint (or reuse) a client-credentials JWT. Refreshes ~60s before expiry.
  async function getToken() {
    const now = Date.now();
    if (_tok.value && now < _tok.exp - 60_000) return _tok.value;
    if (!cfg.clientId || !cfg.clientSecret)
      throw new Error(
        'No OAuth client configured. Set client_id + client_secret in App Settings → Connection.'
      );
    const form = new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: cfg.clientId,
      client_secret: cfg.clientSecret,
    });
    if (cfg.scope) form.set('scope', cfg.scope);
    const res = await fetch((cfg.base || '') + '/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      throw new Error(
        `/token → ${res.status} ${res.statusText} ${detail}`.trim()
      );
    }
    const j = await res.json();
    _tok = {
      value: j.access_token,
      exp: Date.now() + (Number(j.expires_in) || 3600) * 1000,
    };
    return _tok.value;
  }

  // Thin fetch wrapper with sane errors + OAuth. Used only by live branches.
  // Mints a JWT, and on a 401 clears the cache and retries once (token rotated
  // or expired early).
  async function http(method, path, body, root) {
    const url = (root ?? cfg.base) + path;
    const doFetch = async () => {
      const tok = await getToken();
      return fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + tok,
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
    };
    let res = await doFetch();
    if (res.status === 401) {
      _tok = { value: '', exp: 0 };
      res = await doFetch();
    }
    if (!res.ok)
      throw new Error(`${method} ${path} → ${res.status} ${res.statusText}`);
    return res.status === 204 ? null : res.json();
  }

  // Prometheus instant query → single scalar (best-effort).
  async function promScalar(promql) {
    const u =
      cfg.prometheus + '/api/v1/query?query=' + encodeURIComponent(promql);
    const j = await (await fetch(u)).json();
    const v = j?.data?.result?.[0]?.value?.[1];
    return v != null ? Number(v) : null;
  }

  const PX = window.PX || (window.PX = {});
  const mock = () => PX; // mock data already lives on window.PX

  // Simulated mock: honors cfg.sim so loading/error/empty states are testable.
  // `empty` is the value returned for the empty case (per-method shape).
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  async function simMock(value, emptyVal) {
    await wait(cfg.sim === 'slow' ? 1600 : 280);
    if (cfg.sim === 'error')
      throw new Error('Simulated API error (dev sim = error)');
    if (cfg.sim === 'empty') return emptyVal !== undefined ? emptyVal : value;
    return value;
  }

  // Choose live vs mock per call. mockFn may return a value or [value, emptyVal].
  const pick = (liveFn, mockFn) => {
    if (cfg.live) return liveFn();
    const m = mockFn();
    return Array.isArray(m) && m.length === 2 && m.__pair
      ? simMock(m[0], m[1])
      : simMock(m);
  };
  // wrap a [value, empty] pair so pick can tell it apart from a real array value
  const pair = (value, emptyVal) => {
    const a = [value, emptyVal];
    a.__pair = true;
    return a;
  };

  PX.api = {
    // ── config ──────────────────────────────────────────────
    config: cfg,
    isLive: () => cfg.live,
    setLive(on) {
      cfg.live = !!on;
      LS.setItem('px_live', on ? '1' : '0');
      return cfg.live;
    },
    setClient(id, secret) {
      cfg.clientId = id || '';
      cfg.clientSecret = secret || '';
      LS.setItem('px_client_id', cfg.clientId);
      // Same-origin local operator tool; the secret lives in this browser's
      // localStorage. Document the trade-off; rotate via `poindexter auth`.
      LS.setItem('px_client_secret', cfg.clientSecret);
      _tok = { value: '', exp: 0 };
    },
    setScope(s) {
      cfg.scope = s || '';
      LS.setItem('px_scope', cfg.scope);
    },
    setBase(b) {
      cfg.base = b || '';
      LS.setItem('px_base', cfg.base);
    },
    setPrometheus(u) {
      cfg.prometheus = u || '';
      LS.setItem('px_prom', cfg.prometheus);
    },
    setSim(s) {
      cfg.sim = s || 'normal';
      LS.setItem('px_sim', cfg.sim);
    },
    getSim() {
      return cfg.sim;
    },

    // ── health check (Test connection) ──────────────────────
    // Live: hits the worker. Mock: resolves OK (or fails under sim=error).
    async health() {
      if (!cfg.live) {
        await wait(cfg.sim === 'slow' ? 1200 : 350);
        if (cfg.sim === 'error')
          throw new Error('Simulated: worker unreachable');
        return {
          ok: true,
          mode: 'mock',
          detail: 'mock data (not connected to a worker)',
        };
      }
      const t0 = performance.now();
      // Prefer a dedicated health route; fall back to a cheap settings read.
      try {
        await http('GET', '/api/health');
      } catch (e) {
        await http('GET', '/api/settings?limit=1');
      }
      return {
        ok: true,
        mode: 'live',
        ms: Math.round(performance.now() - t0),
        base: cfg.base || '(same-origin)',
      };
    },

    // ── settings ────────────────────────────────────────────
    listSettings(category) {
      return pick(
        () =>
          http(
            'GET',
            '/api/settings' +
              (category ? `?category=${category}&limit=100` : '?limit=100')
          ),
        () =>
          pair(window.PX_SETTINGS, {
            categories: window.PX_SETTINGS.categories,
            settings: [],
          })
      );
    },
    // PUT /api/settings/{id} — body shape = SettingUpdate { value, ... }
    updateSetting(id, value) {
      return pick(
        () => http('PUT', `/api/settings/${id}`, { value: String(value) }),
        () => ({ id, value, ok: true }) // mock: pretend it persisted
      );
    },

    // ── approvals (Action Inbox · approve kind) ─────────────
    listApprovals() {
      return pick(
        () => http('GET', '/api/approvals'),
        () => mock().inbox.filter((i) => i.kind === 'approve')
      );
    },
    approve(id) {
      return pick(
        () => http('POST', `/api/approvals/${id}/approve`),
        () => ({ ok: true })
      );
    },
    reject(id) {
      return pick(
        () => http('POST', `/api/approvals/${id}/reject`),
        () => ({ ok: true })
      );
    },

    // ── tasks (pipeline) ────────────────────────────────────
    listTasks(params = '') {
      return pick(
        () => http('GET', '/api/tasks' + params),
        () => mock().pipeline.tasks
      );
    },
    getTask(id) {
      return pick(
        () => http('GET', `/api/tasks/${id}`),
        () => mock().pipeline.tasks.find((t) => t.id === id)
      );
    },
    retryTask(id) {
      return pick(
        () => http('POST', `/api/tasks/${id}/retry`),
        () => ({ ok: true })
      );
    },
    killTask(id) {
      return pick(
        () => http('POST', `/api/tasks/${id}/cancel`),
        () => ({ ok: true })
      );
    },

    // ── live event stream ───────────────────────────────────
    // Worker exposes GET /api/pipeline/events. For a true live tail,
    // swap to SSE/WebSocket if you add one; polling works today.
    pipelineEvents() {
      return pick(
        () => http('GET', '/api/pipeline/events'),
        () => mock().auditSeed
      );
    },

    // ── brain / memory ──────────────────────────────────────
    memoryStats() {
      return pick(
        () => http('GET', '/api/memory/stats'),
        () => mock().brain
      );
    },

    // ── service health / probes ─────────────────────────────
    probes() {
      return pick(
        () => http('GET', '/probes'),
        () => mock().services
      );
    },
    // No public "restart" route on the worker — restarts are a brain/docker
    // action. Wire this to your own admin endpoint or a brain webhook.
    // TODO(live): point at your restart mechanism.
    restartService(name) {
      return pick(
        () => http('POST', `/api/admin/restart`, { service: name }),
        () => ({ ok: true })
      );
    },
    runAllProbes() {
      return pick(
        () => http('POST', '/api/admin/run-probes'),
        () => ({ ok: true })
      );
    },

    // ── posts / analytics (KPIs) ────────────────────────────
    posts() {
      return pick(
        () => http('GET', '/api/posts'),
        () => ({ items: [] })
      );
    },
    analyticsViews() {
      return pick(
        () => http('GET', '/api/analytics/views'),
        () => ({ items: [] })
      );
    },

    // ── GPU (Prometheus, not the worker) ────────────────────
    // Metric names depend on your exporter (nvidia_gpu_exporter / DCGM).
    // TODO(live): match these to your actual series names.
    async gpu() {
      if (!cfg.live) return mock().gpu;
      const [util, temp, power] = await Promise.all([
        promScalar('nvidia_smi_utilization_gpu_ratio * 100').catch(() => null),
        promScalar('nvidia_smi_temperature_gpu').catch(() => null),
        promScalar('nvidia_smi_power_draw_watts').catch(() => null),
      ]);
      return {
        ...mock().gpu,
        util: util ?? mock().gpu.util,
        temp: temp ?? mock().gpu.temp,
        power: power ?? mock().gpu.power,
      };
    },
  };

  // Tiny boot hint in the console for whoever wires this up.
  if (!cfg.live) {
    console.info(
      '[PX.api] MOCK mode. PX.api.setClient("client_id","client_secret"); PX.api.setBase("http://localhost:8002"); PX.api.setLive(true) to go live.'
    );
  }
})();
