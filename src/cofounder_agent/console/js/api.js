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
     health/svc    Prometheus GET /api/v1/query  (cAdvisor container_* :9091) + /api/health
     gpu           Prometheus GET /api/v1/query  (nvidia_gpu_* :9091)
   NOTE: /api/modules/probes returns {count:0,probes:[]} today — it is module
   discovery, NOT service health. Service health = cAdvisor container_last_seen
   (covers all ~39 containers; up{} only has the ~12 scrape targets) + /api/health.
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

  // Prometheus instant query → full vector: [{labels, value:Number}]. Used when
  // one query carries a value PER series (e.g. per-container liveness) and we
  // need to key the results by a label instead of taking result[0].
  async function promVector(promql) {
    const u =
      cfg.prometheus + '/api/v1/query?query=' + encodeURIComponent(promql);
    const j = await (await fetch(u)).json();
    return (j?.data?.result || []).map((r) => ({
      labels: r.metric || {},
      value: r.value ? Number(r.value[1]) : null,
    }));
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

    // ── service health (real liveness from cAdvisor) ────────
    // Service health is NOT /api/modules/probes (that's module discovery and
    // returns {count:0}). The real per-container signal is cAdvisor's
    // container_last_seen — it covers ALL ~39 containers, whereas Prometheus
    // up{} only has the ~12 scrape targets. From one instant query per metric
    // (keyed by the `name` label) we derive:
    //   status  ← age = time() - container_last_seen (<60s ok · ≥60s stale · absent down)
    //   img     ← the series' `image` label
    //   uptime  ← time() - container_start_time_seconds
    //   cpu     ← rate(container_cpu_usage_seconds_total[1m]) * 100
    //   mem     ← container_memory_usage_bytes / 1e6 (MB)
    // plus a worker /api/health overlay — the container can be up while FastAPI
    // is wedged. host:true rows (ollama at :11434) have no cAdvisor series, so
    // they're shown neutral, never faked.
    serviceHealth() {
      return pick(
        async () => {
          const byName = (vec) => {
            const m = {};
            vec.forEach((s) => {
              if (s.labels.name) m[s.labels.name] = s;
            });
            return m;
          };
          const sel = '{name=~"poindexter.+"}';
          const [age, cpu, mem, up] = await Promise.all([
            promVector('time() - container_last_seen' + sel)
              .then(byName)
              .catch(() => ({})),
            promVector(
              'rate(container_cpu_usage_seconds_total' + sel + '[1m]) * 100'
            )
              .then(byName)
              .catch(() => ({})),
            promVector('container_memory_usage_bytes' + sel)
              .then(byName)
              .catch(() => ({})),
            promVector('time() - container_start_time_seconds' + sel)
              .then(byName)
              .catch(() => ({})),
          ]);
          let workerOk = null;
          try {
            await http('GET', '/api/health');
            workerOk = true;
          } catch (e) {
            workerOk = false;
          }
          const fmtUptime = (secs) => {
            if (secs == null) return '—';
            const d = Math.floor(secs / 86400);
            const h = Math.floor((secs % 86400) / 3600);
            const m = Math.floor((secs % 3600) / 60);
            return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
          };
          return mock().services.map((s) => {
            if (s.host) {
              // cAdvisor can't see host processes — don't fabricate liveness.
              return { ...s, status: 'off', metric: 'host · not scraped' };
            }
            const a = age[s.container];
            let status, metric;
            if (!a || a.value == null) {
              status = 'err';
              metric = 'down';
            } else if (a.value < 60) {
              status = 'ok';
              metric = 'up · ' + Math.round(a.value) + 's';
            } else {
              status = 'warn';
              metric = 'stale · ' + Math.round(a.value) + 's';
            }
            if (workerOk === false && s.container === 'poindexter-worker') {
              status = 'warn';
              metric = 'api unreachable';
            }
            const cpuV = cpu[s.container]?.value;
            const memV = mem[s.container]?.value;
            return {
              ...s,
              status,
              metric,
              img: (a && a.labels.image) || s.img,
              uptime: fmtUptime(up[s.container]?.value),
              cpu: cpuV != null ? Math.round(cpuV) : 0,
              mem: memV != null ? Math.round(memV / 1e6) : 0,
              probe:
                status === 'ok'
                  ? 'cAdvisor ✓'
                  : status === 'warn'
                    ? 'cAdvisor ⚠'
                    : 'absent ✕',
            };
          });
        },
        () => mock().services
      );
    },
    // Restart is a brain/docker.sock action — the worker container has NO
    // docker.sock mount (only poindexter-brain-daemon does), so it CANNOT
    // restart containers directly. Phase 5.3 wires this through the brain via
    // the DB spinal cord (write a restart intent the brain-daemon claims).
    // Until then the live branch has no endpoint; mock stays a no-op. See
    // docs/superpowers/plans/2026-06-13-operator-console.md.
    restartService(name) {
      return pick(
        () => http('POST', `/api/admin/restart`, { service: name }),
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

    // ── GPU (Prometheus :9091, not the worker) ──────────────
    // Verified against the local poindexter-gpu-exporter series (the same ones
    // the Hardware & Power dashboard reads). VRAM is exported in MiB → /1024 for
    // the GB the gauges expect. driver/procs aren't in nvidia_gpu_* so they're
    // left empty in live rather than carrying mock values (no fabricated data).
    // utilHist/tempHist seed FLAT at the current real reading; the GPU poll in
    // app.jsx shifts real samples in each tick. clockMax/name are display
    // scaffolding (the card really is an RTX 5090).
    async gpu() {
      if (!cfg.live) return mock().gpu;
      const g = mock().gpu;
      const [util, temp, power, powerMax, vu, vt, fan, clock] =
        await Promise.all([
          promScalar('nvidia_gpu_utilization_percent').catch(() => null),
          promScalar('nvidia_gpu_temperature_celsius').catch(() => null),
          promScalar('nvidia_gpu_power_draw_watts').catch(() => null),
          promScalar('nvidia_gpu_power_limit_watts').catch(() => null),
          promScalar('nvidia_gpu_memory_used_mib').catch(() => null),
          promScalar('nvidia_gpu_memory_total_mib').catch(() => null),
          promScalar('nvidia_gpu_fan_speed_percent').catch(() => null),
          promScalar('nvidia_gpu_clock_graphics_mhz').catch(() => null),
        ]);
      const mibToGb = (m) =>
        m == null ? null : Math.round((m / 1024) * 10) / 10;
      const u = Math.round(util ?? g.util);
      const t = Math.round(temp ?? g.temp);
      return {
        ...g,
        driver: '', // not exported by nvidia_gpu_* — don't fabricate a version
        procs: [], // no per-process VRAM series — empty beats fake rows
        util: u,
        temp: t,
        power: power != null ? Math.round(power) : g.power,
        powerMax: powerMax != null ? Math.round(powerMax) : g.powerMax,
        vramUsed: mibToGb(vu) ?? g.vramUsed,
        vramTotal: mibToGb(vt) ?? g.vramTotal,
        fan: fan != null ? Math.round(fan) : g.fan,
        clock: clock != null ? Math.round(clock) : g.clock,
        utilHist: util != null ? Array(g.utilHist.length).fill(u) : g.utilHist,
        tempHist: temp != null ? Array(g.tempHist.length).fill(t) : g.tempHist,
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
