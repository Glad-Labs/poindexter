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
     budget        GET  /api/metrics/costs/budget  (spend vs cap; by-model NOT routed)
     findings      GET  /api/findings  (probe-routing triage, #461; read-only)
     media         GET  /api/media-approval/pending  · POST /{post_id}/{medium}/decide (Gate-2)
     schedule      GET  /api/scheduling  · PATCH /api/scheduling/shift (reschedule)
     seo           GET  /api/seo  (SEO-refresh queue + outcomes, #1466; read-only)
     voice         GET  /api/settings (voice_agent_public_join_url; operator config)
     rebuild       POST /api/export/rebuild  (full static re-export + ISR revalidate)
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
  // De-dupes concurrent mints. Going LIVE mounts ~11 panel effects at once, each
  // calling http() -> getToken() against an empty cache; without coalescing,
  // every one would POST /token and trip the worker's rate limiter (429). The
  // first caller starts the mint and parks the promise here; the rest await it.
  // Cleared in finally(), so the next mint after success/expiry/failure is fresh.
  let _tokInflight = null;

  // Mint (or reuse) a client-credentials JWT. Refreshes ~60s before expiry.
  async function getToken() {
    const now = Date.now();
    if (_tok.value && now < _tok.exp - 60_000) return _tok.value;
    if (!cfg.clientId || !cfg.clientSecret)
      throw new Error(
        'No OAuth client configured. Set client_id + client_secret in App Settings → Connection.'
      );
    // A mint is already in flight — ride it instead of starting a second one.
    if (_tokInflight) return _tokInflight;
    _tokInflight = (async () => {
      const postToken = () => {
        const form = new URLSearchParams({
          grant_type: 'client_credentials',
          client_id: cfg.clientId,
          client_secret: cfg.clientSecret,
        });
        if (cfg.scope) form.set('scope', cfg.scope);
        return fetch((cfg.base || '') + '/token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: form.toString(),
        });
      };
      let res = await postToken();
      // Rate-limited (e.g. a burst the in-flight dedup can't cover — multiple
      // tabs, or a token-rotation storm). Back off briefly and retry ONCE
      // before surfacing it, so a transient 429 self-heals instead of leaving
      // a panel red-toasted until the next 5-min poll.
      if (res.status === 429) {
        await new Promise((r) => setTimeout(r, 400));
        res = await postToken();
      }
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
    })().finally(() => {
      _tokInflight = null;
    });
    return _tokInflight;
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

  // ISO timestamp → compact relative age ('22m', '3h', '2d'). Used to render
  // server-side created_at columns the same way the mock's ago() helper does.
  function relAge(iso) {
    if (!iso) return '—';
    const secs = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!isFinite(secs) || secs < 0) return '—';
    if (secs < 60) return Math.round(secs) + 's';
    if (secs < 3600) return Math.floor(secs / 60) + 'm';
    if (secs < 86400) return Math.floor(secs / 3600) + 'h';
    return Math.floor(secs / 86400) + 'd';
  }

  // HTML-escape an interpolated value. The audit feed renders each line's
  // `html` via dangerouslySetInnerHTML, so any value pulled from audit_log
  // details (reviewer feedback, topic titles, exception text — all
  // LLM/research-derived, NOT trusted markup) MUST pass through here before
  // being embedded. The surrounding <b>/<span class="c-*"> tags are ours.
  function escHtml(s) {
    return String(s == null ? '' : s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[c]
    );
  }

  // Map one /api/pipeline/events row (a flattened audit_log entry) onto the
  // console's feed-line shape {id, ts, tag:[tone,label], html}. Mirrors the
  // mobile pipeline dashboard's colour logic (approved→mint, rejected→red,
  // rewrite→amber) but as a single escaped line. `id` lets the live poll
  // dedup against what's already on the feed.
  function eventToFeedLine(ev) {
    const d = (ev && ev.details) || {};
    const type = (ev && ev.event_type) || 'event';
    const task =
      ev && ev.task_id ? escHtml(String(ev.task_id).slice(0, 8)) : '';
    const ts =
      ev && ev.timestamp
        ? new Date(ev.timestamp).toTimeString().slice(0, 8)
        : '';
    const tail = task ? ` · <b>#${task}</b>` : '';
    let tag, html;
    switch (type) {
      case 'qa_decision': {
        const ok = d.approved !== false;
        tag = [ok ? 'mint' : 'red', 'QA'];
        html = `<b>${escHtml(d.reviewer || 'reviewer')}</b> <span class="c-${ok ? 'mint' : 'red'}">${ok ? 'PASS' : 'FAIL'}</span> · score <b>${escHtml(d.score ?? '?')}</b>${tail}`;
        break;
      }
      case 'qa_aggregate': {
        const ok = d.approved !== false;
        tag = [ok ? 'mint' : 'red', 'QA'];
        const failed =
          Array.isArray(d.failed_reviewers) && d.failed_reviewers.length
            ? ` · failed ${escHtml(d.failed_reviewers.join(', '))}`
            : '';
        html = `multi-model <span class="c-${ok ? 'mint' : 'red'}">${ok ? 'APPROVED' : 'REJECTED'}</span> · <b>${escHtml(d.final_score ?? '?')}</b>/100${failed}${tail}`;
        break;
      }
      case 'qa_passed':
        tag = ['mint', 'QA'];
        html = `<span class="c-mint">passed</span>${tail}`;
        break;
      case 'qa_failed':
        tag = ['red', 'QA'];
        html = `<span class="c-red">failed</span>${tail}`;
        break;
      case 'rewrite_decision':
      case 'qa_rewrite_triggered': {
        tag = ['amber', 'REWRITE'];
        const att =
          d.attempt != null
            ? `attempt <b>${escHtml(d.attempt)}</b>${d.max_attempts ? '/' + escHtml(d.max_attempts) : ''}`
            : 'triggered';
        const iss =
          d.issue_count != null ? ` · ${escHtml(d.issue_count)} issues` : '';
        html = `rewrite ${att}${iss}${tail}`;
        break;
      }
      case 'task_started':
      case 'task_created': {
        tag = ['cyan', 'TASK'];
        const topic = d.topic || d.title;
        const t = topic ? ` · “${escHtml(topic)}”` : '';
        html = `task <span class="c-cyan">${type === 'task_created' ? 'created' : 'started'}</span>${tail}${t}`;
        break;
      }
      case 'pipeline_complete':
      case 'generation_complete':
        tag = ['mint', 'PIPELINE'];
        html = `<span class="c-mint">${type === 'pipeline_complete' ? 'pipeline complete' : 'generation complete'}</span>${tail}`;
        break;
      default: {
        const sev = (ev && ev.severity) || 'info';
        const tone =
          sev === 'error' ? 'red' : sev === 'warning' ? 'amber' : 'cyan';
        tag = [tone, 'EVENT'];
        html = `${escHtml(type)}${tail}`;
      }
    }
    return { id: ev && ev.id, ts, tag, html };
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
    // pending-approval → {items:[…], total, limit, offset} (canonical envelope,
    // poindexter#745). approve / reject / publish are three distinct operator
    // gates: approve only STAGES (auto_publish defaults false), publish ships.
    // (See feedback_human_approval.)
    listApprovals() {
      return pick(
        () => http('GET', '/api/tasks/pending-approval?limit=50'),
        () => ({ items: mock().inbox.filter((i) => i.kind === 'approve') })
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
        // Canonical offset envelope (poindexter#745): {items, total, limit, offset}.
        () => pair(mock().topics, { items: [], total: 0, limit: 0, offset: 0 })
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

    // ── findings (probe-routing triage, #461) ───────────────
    // GET /api/findings → {findings[], counts{emitted,pending}, by_kind[],
    // by_severity[], delivery_by_kind{}, watermark, hours}. READ-ONLY: the
    // brain's findings_alert_router delivers findings autonomously (watermark-
    // based), so there is no ack/route HTTP surface to wire — the panel is a
    // triage view, not a mutation surface.
    findings(params = '') {
      return pick(
        () => http('GET', '/api/findings' + params),
        () =>
          pair(mock().findings, {
            counts: { emitted: 0, pending: 0 },
            by_kind: [],
            by_severity: [],
            delivery_by_kind: {},
            findings: [],
            hours: 168,
            watermark: 0,
          })
      );
    },

    // ── live event stream ───────────────────────────────────
    // Worker exposes GET /api/pipeline/events → {count, events[], server_time}.
    // On live we map each event onto the feed-line shape (newest-first; the
    // route orders timestamp DESC) so the audit feed shows REAL QA decisions /
    // rewrites / task lifecycle instead of the mock simulator's fabricated
    // lines. For a true live tail, swap to SSE/WebSocket if you add one;
    // polling works today. Mock keeps the seed (the simulator drives the feed
    // in mock mode, so this branch is only hit if something polls offline).
    pipelineEvents() {
      return pick(
        async () => {
          const r = await http(
            'GET',
            '/api/pipeline/events?limit=50&since_minutes=120'
          );
          const evs = (r && r.events) || [];
          return evs.map(eventToFeedLine);
        },
        () => mock().auditSeed
      );
    },

    // ── voice (operator config, NOT hardcoded) ──────────────
    // The tap-to-join URL is operator-specific tailnet infra, so it lives in
    // app_settings.voice_agent_public_join_url (empty on fresh installs / the
    // public mirror, set on the operator's stack). Hardcoding it would leak
    // operator infra AND trip the mirror redact filter. Returns '' when unset
    // so the caller renders an honest "voice not configured" state.
    voiceJoinUrl() {
      return pick(
        async () => {
          const r = await http(
            'GET',
            '/api/settings?search=voice_agent_public_join_url&limit=10'
          );
          const hit = ((r && r.items) || []).find(
            (s) => s.key === 'voice_agent_public_join_url'
          );
          return (hit && hit.value) || '';
        },
        () => '' // mock: no operator voice URL (honest-empty)
      );
    },

    // ── static-export rebuild ───────────────────────────────
    // POST /api/export/rebuild — full re-export of every static JSON to the
    // CDN + ISR revalidation. The operator "ship it" button.
    rebuildExport() {
      return pick(
        () => http('POST', '/api/export/rebuild'),
        () => ({ ok: true }) // mock: no-op
      );
    },

    // ── brain / memory ──────────────────────────────────────
    // GET /api/memory/stats → {total, embed_model, embed_dim, by_source_table[],
    // by_writer[]}. Map it onto the BrainPanel shape (totalEmbeddings / bySource
    // / byWriter). queueDepth / lastCycle / decisions / growth / recent are
    // brain-daemon internals (brain_queue / brain_decisions) with NO HTTP route,
    // so live mode leaves them null/[] and the panel renders an honest-empty
    // state — never the mock's queue/decisions (feedback_no_dummy_data).
    memoryStats() {
      return pick(
        async () => {
          const s = await http('GET', '/api/memory/stats');
          const src = (s && s.by_source_table) || [];
          const wr = (s && s.by_writer) || [];
          return {
            totalEmbeddings: (s && s.total) || 0,
            model: (s && s.embed_model) || 'nomic-embed-text',
            dim: (s && s.embed_dim) || null,
            bySource: src.map((r) => [r.key, r.count]),
            byWriter: wr.map((r) => ({
              key: r.key,
              count: r.count,
              age: r.age_seconds,
              stale: !!r.stale,
            })),
            // brain-daemon internals — no HTTP route → honest-empty in live.
            queueDepth: null,
            lastCycle: null,
            decisions: [],
            growth: [],
            recent: [],
          };
        },
        () => mock().brain
      );
    },

    // GET /api/memory/search?q=&source_table=&limit= → {query, count, hits[]}.
    // Semantic recall over the pgvector corpus — also the "recall decision"
    // surface (scope source_table to memory/brain). Read-only. `opts` is an
    // already-encoded query-string tail (e.g. '&source_table=memory&limit=10').
    memorySearch(q, opts = '') {
      return pick(
        () =>
          http('GET', '/api/memory/search?q=' + encodeURIComponent(q) + opts),
        () => ({
          query: q,
          count: mock().brain.recent.length,
          hits: mock().brain.recent.map((r, i) => ({
            source_table: r.src,
            source_id: r.id,
            similarity: Number((0.82 - i * 0.06).toFixed(3)),
            writer: 'worker',
            text_preview: r.preview,
            metadata: {},
          })),
        })
      );
    },

    // ── media Gate-2 (podcast / video approval, #1343) ──────
    // GET /api/media-approval/pending → {items:[{post_id, medium, title, slug,
    // quality_score, created_at}], total, limit, offset} (canonical envelope,
    // poindexter#745). gate2Pending (= total) + the queue are real; render-rate
    // KPIs (renderSuccess24h / dispatched / videosPersisted) have no read here →
    // null, and the panel shows '—' (feedback_no_dummy_data).
    mediaQueue() {
      return pick(
        async () => {
          const r = await http('GET', '/api/media-approval/pending');
          const rows = (r && r.items) || [];
          return {
            gate2Pending: r && r.total != null ? r.total : rows.length,
            renderSuccess24h: null,
            dispatched: null,
            videosPersisted: null,
            queue: rows.map((m) => ({
              id: m.post_id + ':' + m.medium,
              post_id: m.post_id,
              medium: m.medium,
              title: m.title || m.slug || m.post_id,
              slug: m.slug,
              quality:
                m.quality_score != null ? Math.round(m.quality_score) : null,
              dur: null,
              age: relAge(m.created_at),
            })),
          };
        },
        () => mock().media
      );
    },

    // POST /api/media-approval/{post_id}/{medium}/decide {approved, notes?}.
    // approved=true clears the post for dispatch; approved=false marks it
    // rejected so it regenerates. The Gate-2 mutation (write surface).
    mediaDecide(postId, medium, approved, notes = '') {
      return pick(
        () =>
          http(
            'POST',
            `/api/media-approval/${encodeURIComponent(postId)}/${encodeURIComponent(medium)}/decide`,
            { approved: !!approved, notes: notes || null }
          ),
        () => ({ ok: true, post_id: postId, medium, approved: !!approved })
      );
    },

    // ── scheduled-publish queue (scheduling_routes.py, #1343) ──
    // GET /api/scheduling → {rows:[{post_id, slug, title, published_at, status}],
    // count}. Read; the panel derives depth / next-slot / past-due / upcoming-24h
    // from published_at (calculated, not stored — feedback_calculated_vs_generated).
    schedule() {
      return pick(
        async () => {
          const r = await http('GET', '/api/scheduling');
          const rows = (r && r.rows) || [];
          return { rows, count: r && r.count != null ? r.count : rows.length };
        },
        () => mock().schedule
      );
    },

    // PATCH /api/scheduling/shift {by_delta, post_ids?} — nudge slot(s) by a
    // duration string ('1 hour', '-1 hour'). post_ids null = shift the whole
    // schedule. The reschedule mutation (write surface).
    scheduleShift(byDelta, postIds) {
      return pick(
        () =>
          http('PATCH', '/api/scheduling/shift', {
            by_delta: byDelta,
            post_ids: postIds && postIds.length ? postIds : null,
          }),
        () => ({ ok: true, by_delta: byDelta, post_ids: postIds || [] })
      );
    },

    // ── SEO refresh pipeline (seo_routes.py, #1466) ─────────
    // GET /api/seo → {queue, refreshes, by_status, by_tier}. The live shape
    // already matches the panel, so no mapping. Read-only — the seo.refresh
    // loop runs autonomously; the console observes the opportunity queue + the
    // baseline→outcome position deltas.
    seo() {
      return pick(
        () => http('GET', '/api/seo'),
        () => mock().seo
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
    // `params` is an already-encoded query tail (e.g. '?limit=100' /
    // '?days=1'). Live shapes (VERIFIED in cms_routes.py): posts →
    // {posts:[{published_at,…}], total, offset, limit}; analyticsViews →
    // {period_days, daily:[{day, views}], top_posts, top_referrers}. Mock
    // returns the same empty shapes (honest-empty, never fabricated rows) —
    // the KPI strip only consumes the live branch (kpisFromLive).
    posts(params = '') {
      return pick(
        () => http('GET', '/api/posts' + params),
        () => ({ posts: [], total: 0, offset: 0, limit: 0 })
      );
    },
    analyticsViews(params = '') {
      return pick(
        () => http('GET', '/api/analytics/views' + params),
        () => ({ period_days: 0, daily: [], top_posts: [], top_referrers: [] })
      );
    },

    // ── cost / budget (real LLM/API spend vs cap) ───────────
    // GET /api/metrics/costs/budget → {amount_spent, monthly_budget,
    // percent_used, daily_burn_rate, projected_final_cost, alerts, status}.
    // This is the ONE cost read with an HTTP surface. The by-model + daily-series
    // breakdowns (CostAggregationService.get_breakdown_by_model / get_daily) are
    // NOT routed, so the live CostPanel renders those as "backend read pending"
    // (empty, not mocked — feedback_no_dummy_data).
    budget() {
      return pick(
        () => http('GET', '/api/metrics/costs/budget'),
        () => {
          const c = mock().cost;
          return {
            amount_spent: c.monthToDate,
            monthly_budget: c.budget,
            percent_used: (c.monthToDate / c.budget) * 100,
            daily_burn_rate: c.dailyBurn,
            projected_final_cost: c.projected,
            alerts: c.alerts,
            status: c.status,
          };
        }
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
