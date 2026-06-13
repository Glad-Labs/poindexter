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

   Endpoint map (VERIFIED against src/cofounder_agent/routes/):
     token         POST /token   (grant_type=client_credentials → JWT)
     health        GET  /api/health
     settings      GET  /api/settings           · PUT /api/settings/{id}
     approvals     GET  /api/tasks/pending-approval
                   POST /api/tasks/{id}/{approve|reject|publish}  (approve != publish)
     tasks         GET  /api/tasks, /{id}        · PUT /api/tasks/{id}/status  (retry→pending)
                   DELETE /api/tasks/{id}  (cancel)
     events        GET  /api/pipeline/events
     memory        GET  /api/memory/stats, /api/memory/search
     posts         GET  /api/posts
     analytics     GET  /api/analytics/views
     gpu           Prometheus GET /api/v1/query  (separate origin, :9091)
   NOTE: /api/modules/probes returns {count:0,probes:[]} today — it is module
   discovery, NOT service health. Service health = Prometheus up{} + /api/health.
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

  // ── settings shape adapter ────────────────────────────────
  // GET /api/settings returns SettingListResponse {total,page,per_page,pages,
  // items:[SettingResponse]} — NOT the {categories,settings} shape the panel
  // reads. Map a SettingResponse row → the console row shape. Secrets arrive
  // pre-masked '********' (is_encrypted / enc: ciphertext) and must never be
  // treated as a real value. data_type ∈ string|int|float|bool|json.
  function adaptSetting(it) {
    const secret = !!it.is_encrypted || it.value === '********';
    let type = 'text';
    if (secret) type = 'secret';
    else if (it.data_type === 'bool') type = 'bool';
    else if (it.data_type === 'int') type = 'int';
    else if (it.data_type === 'float') type = 'float';
    else if (it.data_type === 'json') type = 'textarea';
    return {
      id: it.id,
      key: it.key,
      value: it.value == null ? '' : String(it.value),
      category: it.category || 'general',
      description: it.description || '',
      type,
      is_secret: secret,
      readOnly: !!it.is_read_only,
    };
  }

  // Derive the category sidebar from the distinct categories actually present.
  // Reuse the curated mock labels where known; else Title-Case the raw id.
  function deriveCategories(rows) {
    const known = {};
    ((window.PX_SETTINGS && window.PX_SETTINGS.categories) || []).forEach(
      (c) => (known[c.id] = c.label)
    );
    const seen = {};
    const out = [];
    rows.forEach((r) => {
      if (seen[r.category]) return;
      seen[r.category] = 1;
      out.push({
        id: r.category,
        label:
          known[r.category] ||
          r.category
            .replace(/[_.]/g, ' ')
            .replace(/\b\w/g, (m) => m.toUpperCase()),
      });
    });
    out.sort((a, b) => a.label.localeCompare(b.label));
    return out;
  }

  // Page through GET /api/settings (per_page caps at 100) until all `total`
  // rows are loaded — the console needs the full ~685-key set, not page 1.
  // Page 1 is awaited to learn `total`; the rest fetch in parallel.
  async function loadAllSettings(category) {
    const PAGE = 100;
    const qs = (off) =>
      '/api/settings?limit=' +
      PAGE +
      '&offset=' +
      off +
      (category ? '&category=' + encodeURIComponent(category) : '');
    const first = await http('GET', qs(0));
    const items = (first && first.items) || [];
    const total = (first && first.total) || items.length;
    const offsets = [];
    for (let off = PAGE; off < total && off < 5000; off += PAGE)
      offsets.push(off);
    const more = await Promise.all(offsets.map((off) => http('GET', qs(off))));
    more.forEach((p) => items.push(...((p && p.items) || [])));
    const settings = items.map(adaptSetting);
    return { settings, categories: deriveCategories(settings), total };
  }

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
      // Live: page through + adapt SettingListResponse → {settings,categories}.
      return pick(
        () => loadAllSettings(category),
        () =>
          pair(window.PX_SETTINGS, {
            categories: window.PX_SETTINGS.categories,
            settings: [],
          })
      );
    },
    // PUT /api/settings/{key} — the path segment is the setting KEY. The numeric
    // row id is unreliable (often just a pagination index server-side), so saves
    // MUST key off `key`. Body = SettingUpdate { value }.
    updateSetting(key, value) {
      return pick(
        () =>
          http('PUT', `/api/settings/${encodeURIComponent(key)}`, {
            value: String(value),
          }),
        () => ({ key, value, ok: true }) // mock: pretend it persisted
      );
    },

    // ── approvals (Action Inbox · approve kind) ─────────────
    // Real endpoints live under /api/tasks — there is NO /api/approvals.
    // pending-approval → {total, count, tasks:[…]}. approve / reject / publish
    // are three distinct operator gates: approve only STAGES (auto_publish
    // defaults false), publish ships. (See feedback_human_approval.)
    listApprovals() {
      return pick(
        () => http('GET', '/api/tasks/pending-approval?limit=50'),
        () => ({ tasks: mock().inbox.filter((i) => i.kind === 'approve') })
      );
    },
    approve(id, opts = {}) {
      // Stage only — never auto-publish from the approve action.
      return pick(
        () =>
          http('POST', `/api/tasks/${id}/approve`, {
            approved: true,
            auto_publish: false,
            ...opts,
          }),
        () => ({ ok: true })
      );
    },
    reject(id, human_feedback = '') {
      return pick(
        () => http('POST', `/api/tasks/${id}/reject`, { human_feedback }),
        () => ({ ok: true })
      );
    },
    publishTask(id) {
      // Separate gate after approve. Ships the staged task.
      return pick(
        () => http('POST', `/api/tasks/${id}/publish`),
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
      // No dedicated /retry route — reset to pending so the flow re-claims it
      // (also the moment to clear any poisoned LangGraph checkpoint).
      return pick(
        () => http('PUT', `/api/tasks/${id}/status`, { status: 'pending' }),
        () => ({ ok: true })
      );
    },
    killTask(id) {
      // Cancel == DELETE the task row (there is no POST /cancel).
      return pick(
        () => http('DELETE', `/api/tasks/${id}`),
        () => ({ ok: true })
      );
    },

    // ── topics triage (open discovery batches) ──────────────
    // GET /api/topics/proposals + POST /api/topics/{batch_id}/{rank|resolve|reject}.
    // {batch_id} is a topic BATCH id: resolve advances the operator-ranked
    // rank-1 candidate into the content pipeline; reject discards the batch and
    // frees the niche's one-open-batch slot. (See routes/topics_routes.py.)
    listTopicProposals() {
      return pick(
        () => http('GET', '/api/topics/proposals'),
        () => pair(mock().topics, { count: 0, batches: [] })
      );
    },
    rankTopicBatch(batchId, orderedCandidateIds) {
      return pick(
        () =>
          http('POST', `/api/topics/${batchId}/rank`, {
            ordered_candidate_ids: orderedCandidateIds,
          }),
        () => ({ ok: true, ranked: orderedCandidateIds.length })
      );
    },
    resolveTopicBatch(batchId) {
      // Advances the rank-1 winner; 400s if the batch wasn't ranked first.
      return pick(
        () => http('POST', `/api/topics/${batchId}/resolve`),
        () => ({ ok: true, status: 'resolved' })
      );
    },
    rejectTopicBatch(batchId, reason = '') {
      return pick(
        () => http('POST', `/api/topics/${batchId}/reject`, { reason }),
        () => ({ ok: true, status: 'expired' })
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
