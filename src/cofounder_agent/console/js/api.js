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
     brain         GET  /api/brain/stats  (decisions_24h/7d + knowledge_total + recent 10)
     posts         GET  /api/posts
     analytics     GET  /api/analytics/views
     budget        GET  /api/metrics/costs/budget  (spend vs cap; by-model NOT routed)
     findings      GET  /api/findings  (probe-routing triage, #461; read-only)
     media         GET  /api/media-approval/pending  · POST /{post_id}/{medium}/decide (Gate-2)
                   · GET /{post_id}/{medium}/preview (raw asset bytes, for the drawer player)
     schedule      GET  /api/scheduling  · PATCH /api/scheduling/shift (reschedule)
     seo           GET  /api/seo  (SEO-refresh queue + outcomes, #1466; read-only)
     gates         GET  /api/gates/pending  (tasks paused at a graph gate, e.g.
                   seo_refresh_gate) · POST /pending/{task_id}/{approve|reject}
                   (approve = 202: records approval + background checkpoint resume)
     social        GET  /api/social/drafts (filterable; per-post per-platform breakdown)
                   POST /api/social/drafts/{id}/{approve|reject}
     voice         GET  /api/settings (voice_agent_public_join_url; operator config)
     rebuild       POST /api/export/rebuild  (full static re-export + ISR revalidate)
     restart       POST /api/services/{container}/restart  (queue) · GET /api/services/restart/{id}  (poll, #909)
     health/svc    Prometheus GET /api/v1/query  (cAdvisor container_* :9091) + /api/health
     gpu           Prometheus GET /api/v1/query  (nvidia_gpu_* :9091)
   NOTE: /api/modules/probes is Module-v1 probe discovery — brain probes
   registered by installed modules, so count follows the module set (0 only
   when no installed module registers one) — NOT service health. Service
   health = cAdvisor container_last_seen (covers all ~39 containers; up{}
   only has the ~12 scrape targets) + /api/health.
   ══════════════════════════════════════════════════════════════ */
(function () {
  const LS = window.localStorage;
  const cfg = {
    // Same-origin when served from the worker. Else e.g. 'http://localhost:8002'
    base: LS.getItem('px_base') ?? '',
    // Prometheus is a different service/port; GPU + some rates come from here.
    // NOTE: the local stack runs Prometheus on :9091 (not the upstream default :9090).
    // Default to the CURRENT host (not a literal localhost) so the queries resolve
    // from the tailnet IP too — localhost would point at the viewer's own device.
    prometheus:
      LS.getItem('px_prom') ??
      `http://${(window.location && window.location.hostname) || 'localhost'}:9091`,
    // Grafana deeplink base — same host-relative default as prometheus. (No
    // embeds remain — the Telemetry charts are native; see console README §4.)
    grafana:
      LS.getItem('px_grafana') ??
      `http://${(window.location && window.location.hostname) || 'localhost'}:3000`,
    // OAuth2 client-credentials. Static Bearer was removed in #249 — every
    // request now rides a short-lived JWT minted from POST /token. Provision a
    // dedicated client with `poindexter auth register-client --name
    // poindexter-console` (it prints the secret once — paste it below).
    clientId: LS.getItem('px_client_id') ?? '',
    clientSecret: LS.getItem('px_client_secret') ?? '',
    scope: LS.getItem('px_scope') ?? '',
    // LIVE is the DEFAULT when the console is served from the worker (the
    // /console/ mount — page origin == API origin). A fresh browser profile
    // must show real state or honest per-panel errors, never the silently
    // ticking mock simulation: on Windows→Pop the operator's new browser had
    // an empty localStorage and the console quietly rendered fake data as if
    // connected (2026-07-23). Mock/demo now requires explicit opt-in —
    // PX.api.setLive(false) (persists px_live='0'), or window.PX_API_LIVE =
    // false, or opening the files outside the worker mount (OSS demo case:
    // file:// or a static host serves mock by default, unchanged).
    live: (() => {
      if (window.PX_API_LIVE !== undefined && window.PX_API_LIVE !== null)
        return !!window.PX_API_LIVE;
      const ls = LS.getItem('px_live');
      if (ls === '1') return true;
      if (ls === '0') return false;
      const loc = window.location;
      return !!(
        loc &&
        /^https?:$/.test(loc.protocol || '') &&
        (loc.pathname || '').startsWith('/console')
      );
    })(),
    // DEV-ONLY: simulate real-world async on the MOCK branch so we can test
    // loading / error / empty states without a backend. Ignored when live.
    sim: LS.getItem('px_sim') ?? 'normal', // normal | slow | error | empty
  };

  // Client-side request ceiling. A hung backend rejects here instead of leaving
  // a panel's poll pending forever (the poll cadence is the retry).
  const HTTP_TIMEOUT_MS = 8000;

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
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), HTTP_TIMEOUT_MS);
      try {
        return await fetch(url, {
          method,
          headers: {
            'Content-Type': 'application/json',
            Authorization: 'Bearer ' + tok,
          },
          signal: ctrl.signal,
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
      } catch (e) {
        if (e && e.name === 'AbortError')
          throw new Error(
            `${method} ${path} → timed out after ${HTTP_TIMEOUT_MS}ms`
          );
        throw e;
      } finally {
        clearTimeout(timer);
      }
    };
    let res = await doFetch();
    if (res.status === 401) {
      _tok = { value: '', exp: 0 };
      res = await doFetch();
    }
    if (!res.ok) {
      // Surface the response body's detail (FastAPI's HTTPException shape:
      // {detail: "..."}, or this app's own {error: "..."}) so a toast shows
      // the actual reason instead of a bare "409 Conflict". One read of the
      // body via text() — calling json() first and falling back to text()
      // on parse failure isn't safe, the stream can only be consumed once.
      const bodyText = await res.text().catch(() => '');
      let detail = bodyText;
      try {
        const body = JSON.parse(bodyText);
        const d = body && (body.detail ?? body.error);
        if (d != null) detail = typeof d === 'string' ? d : JSON.stringify(d);
      } catch {
        // Not JSON — use the raw body text as-is.
      }
      throw new Error(
        `${method} ${path} → ${res.status} ${res.statusText}${detail ? ' — ' + detail : ''}`
      );
    }
    return res.status === 204 ? null : res.json();
  }

  // Prometheus instant query → single scalar (best-effort).
  async function promScalar(promql) {
    try {
      const u =
        cfg.prometheus + '/api/v1/query?query=' + encodeURIComponent(promql);
      const j = await (await fetch(u)).json();
      const v = j?.data?.result?.[0]?.value?.[1];
      return v != null ? Number(v) : null;
    } catch {
      return null; // Prometheus unreachable → honest-empty, never throw.
    }
  }

  // Prometheus instant query → full vector: [{labels, value:Number}]. Used when
  // one query carries a value PER series (e.g. per-container liveness) and we
  // need to key the results by a label instead of taking result[0].
  async function promVector(promql) {
    try {
      const u =
        cfg.prometheus + '/api/v1/query?query=' + encodeURIComponent(promql);
      const j = await (await fetch(u)).json();
      return (j?.data?.result || []).map((r) => ({
        labels: r.metric || {},
        value: r.value ? Number(r.value[1]) : null,
      }));
    } catch {
      return []; // Prometheus unreachable → honest-empty, never throw.
    }
  }

  // Prometheus RANGE query → canonical series {series:[{label,points:[[tMs,v|null]]}]}.
  // The reusable history primitive (sub-projects C/D/E). Best-effort like its
  // instant-query siblings: Prometheus unreachable / non-200 / abort → {series:[]},
  // never throws. Own AbortController — a hung Prometheus can't hang a poll.
  async function promRange(promql, opts) {
    const o = opts || {};
    const end = Math.floor(Date.now() / 1000);
    const start = end - (o.rangeSeconds || 3600);
    const step = o.stepSeconds || 60;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), HTTP_TIMEOUT_MS);
    try {
      const u =
        cfg.prometheus +
        '/api/v1/query_range?query=' +
        encodeURIComponent(promql) +
        '&start=' +
        start +
        '&end=' +
        end +
        '&step=' +
        step;
      const res = await fetch(u, { signal: ctrl.signal });
      if (!res.ok) return { series: [] };
      const j = await res.json();
      const result = (j && j.data && j.data.result) || [];
      return {
        series: window.PX.ts.matrixToSeries(
          result,
          undefined,
          o.labelBy,
          o.labelPrefix
        ),
      };
    } catch {
      return { series: [] }; // unreachable / aborted → honest-empty, never throw.
    } finally {
      clearTimeout(timer);
    }
  }

  // Range key → {rangeSeconds, stepSeconds}: the bucket grid, derived ONCE
  // client-side (PX.ts) so the worker trend endpoints never re-derive it.
  function rangeOpts(range) {
    const rs = window.PX.ts.RANGES[range] || 3600;
    return { rangeSeconds: rs, stepSeconds: window.PX.ts.deriveStep(rs) };
  }
  // Rate/quantile window for rate()/…_bucket — never below 60s.
  function winFor(o) {
    return Math.max(o.stepSeconds, 60) + 's';
  }
  // promRange + force one human label on a single-series aggregate result.
  async function labelledRange(promql, opts, label) {
    const r = await promRange(promql, opts);
    return { series: r.series.map((s) => ({ ...s, label })) };
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
      // ── Modern graph_def pipeline types (post atom-cutover #355) —
      // shapes documented per-emitter; qa_pass_completed is the one
      // schema-validated type (services/audit_event_schemas.py). Keep in
      // sync with the /pipeline HTML dashboard's renderEvent branches.
      case 'qa_pass_completed': {
        const ok = d.approved !== false;
        tag = [ok ? 'mint' : 'red', 'QA'];
        const score = d.final_score != null ? Math.round(d.final_score) : '?';
        const n =
          Array.isArray(d.reviews) && d.reviews.length
            ? d.reviews.length
            : d.reviewer_count;
        const rescued = d.rescued
          ? ' · <span class="c-amber">rescued</span>'
          : '';
        html = `QA <span class="c-${ok ? 'mint' : 'red'}">${ok ? 'APPROVED' : 'REJECTED'}</span> · <b>${escHtml(score)}</b>/100${n ? ` · ${escHtml(n)} reviewers` : ''}${rescued}${tail}`;
        break;
      }
      case 'qa_rescue_scheduled': {
        tag = ['amber', 'REWRITE'];
        const att = `attempt <b>${escHtml(d.attempt ?? '?')}</b>${d.max_attempts ? '/' + escHtml(d.max_attempts) : ''}`;
        const veto =
          Array.isArray(d.vetoed_by) && d.vetoed_by.length
            ? ` · vetoed by ${escHtml(d.vetoed_by.join(', '))}`
            : '';
        html = `QA rescue ${att} · score <b>${escHtml(d.final_score ?? '?')}</b>${veto}${tail}`;
        break;
      }
      case 'qa_flagged_surfaced':
        tag = ['amber', 'QA'];
        html = `flagged for operator review · score <b>${escHtml(d.final_score ?? '?')}</b>${tail}`;
        break;
      case 'writer_self_review_pass': {
        const c = d.contradictions_found;
        tag = [d.revised ? 'amber' : 'cyan', 'WRITER'];
        html = `self-review <span class="c-${d.revised ? 'amber' : 'mint'}">${d.revised ? 'revised' : 'clean'}</span>${c ? ` · ${escHtml(c)} contradictions` : ''}${tail}`;
        break;
      }
      case 'ragas_score': {
        tag = ['cyan', 'QA'];
        const f = (v) => (typeof v === 'number' ? v.toFixed(2) : '?');
        html = `ragas <b>${escHtml(f(d.score))}</b> · faith ${escHtml(f(d.faithfulness))} · relevancy ${escHtml(f(d.answer_relevancy))}${tail}`;
        break;
      }
      case 'image_style_picked':
        tag = ['cyan', 'IMAGE'];
        html = `style <b>“${escHtml(d.style || '?')}”</b>${tail}`;
        break;
      case 'image_ocr_gate_result': {
        const ok = d.passed !== false;
        tag = [ok ? 'mint' : 'amber', 'IMAGE'];
        html = `OCR gate <span class="c-${ok ? 'mint' : 'amber'}">${ok ? 'PASS' : 'FAIL'}</span>${d.attempts != null ? ` · ${escHtml(d.attempts)} attempt(s)` : ''}${tail}`;
        break;
      }
      case 'video_shot_rendered': {
        const ok = d.success !== false;
        tag = [ok ? 'mint' : 'red', 'VIDEO'];
        const shot =
          d.shot_idx != null ? `shot <b>${escHtml(d.shot_idx)}</b>` : 'shot';
        html = `${shot} ${ok ? `<span class="c-mint">${escHtml(d.qa_outcome || 'rendered')}</span>` : '<span class="c-red">failed</span>'}${d.source ? ` · ${escHtml(d.source)}` : ''}${tail}`;
        break;
      }
      case 'template_completed': {
        const ok = d.ok !== false;
        tag = [ok ? 'mint' : 'red', 'PIPELINE'];
        const nodes = Array.isArray(d.records)
          ? ` · ${d.records.length} nodes`
          : '';
        html = `template <span class="c-${ok ? 'mint' : 'red'}">${ok ? 'complete' : 'failed'}</span>${nodes}${tail}`;
        break;
      }
      case 'auto_publish_gate': {
        const fire = d.would_fire === true;
        tag = [fire ? 'mint' : 'cyan', 'PUBLISH'];
        html = `auto-publish <span class="c-${fire ? 'mint' : 'cyan'}">${fire ? 'fires' : 'holds'}</span>${d.gate_state ? ` · ${escHtml(d.gate_state)}` : ''}${d.dry_run ? ' · dry-run' : ''}${tail}`;
        break;
      }
      case 'approval_gate_paused':
        tag = ['amber', 'GATE'];
        html = `paused at <b>${escHtml(d.gate_name || 'gate')}</b> — needs you${tail}`;
        break;
      case 'approval_gate_approved':
        tag = ['mint', 'GATE'];
        html = `<b>${escHtml(d.gate_name || 'gate')}</b> <span class="c-mint">approved</span>${tail}`;
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
  // Reuse the curated labels + canonical order from PX_SETTINGS.categories
  // (mirror of services/settings_categories.py); Title-Case any unknown id and
  // sink it to the end.
  function deriveCategories(rows) {
    const canon = (window.PX_SETTINGS && window.PX_SETTINGS.categories) || [];
    const known = {};
    canon.forEach((c) => (known[c.id] = c.label));
    const order = canon.map((c) => c.id);
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
    // Canonical sidebar order; unknown ids (not in the taxonomy) sort last.
    out.sort((a, b) => {
      const ia = order.indexOf(a.id);
      const ib = order.indexOf(b.id);
      return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib);
    });
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
    // Prometheus instant-query helpers (reused by native hardware/DB panels).
    promScalar,
    promVector,
    promRange,
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
      // Body contract: RejectionRequest (routes/approval_routes.py) REQUIRES
      // {reason, feedback} — a missing key 400s (VALIDATION_ERROR). The
      // `human_feedback` field this used to send belongs to the APPROVE
      // route's schema, not reject. allow_revisions=true → rejected_retry:
      // the console's reject IS "send back to edit" (drawer copy), unlike
      // the MCP reject_post tool's terminal allow_revisions=false.
      return pick(
        () =>
          http('POST', `/api/tasks/${id}/reject`, {
            reason: 'operator_rejected',
            feedback: human_feedback || '',
            allow_revisions: true,
          }),
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

    // ── telemetry: logs + traces + grafana base ─────────────
    // Loki log proxy (worker GET /api/logs). Mock → PX.logs; empty → no lines.
    logs(params = '') {
      return pick(
        () => http('GET', '/api/logs' + params),
        () => pair(mock().logs, { lines: [], stats: { count: 0, query: '' } })
      );
    },
    // Langfuse trace proxy (worker GET /api/traces). Mock → PX.traces.
    traces(params = '') {
      return pick(
        () => http('GET', '/api/traces' + params),
        () => pair(mock().traces, { traces: [], stats: { count: 0 } })
      );
    },
    // Grafana embed base (client-side, like prometheus). Read + set + persist.
    grafanaBase() {
      return cfg.grafana;
    },
    setGrafanaEmbed(u) {
      cfg.grafana = u || '';
      LS.setItem('px_grafana', cfg.grafana);
    },

    // ── task-trace (console board + per-task deep-dive) ─────
    // Three reads behind the worker's /api/trace/* (services/trace_read.py):
    //   /active  → {runs, recent}   the front-door board
    //   /summary → 24h health strip KPIs
    //   /{id}    → the full per-task deep-dive (spine·corpus·qa·cost·halt)
    // Deliberately mirrors findings()/traces(): the LIVE branch lets http()
    // THROW on transport failure so usePolledResource keeps the last-good board
    // and marks it STALE. Swallowing to an empty shape here would repaint the
    // board blank on every blip — indistinguishable from an idle system, which
    // is the one thing an operator must never be shown as fact. Honest-empty is
    // the MOCK branch's job (sim=empty), and the freshness chip's in live.
    traceActive() {
      return pick(
        () => http('GET', '/api/trace/active'),
        () => pair(mock().traceActive, { runs: [], recent: [] })
      );
    },
    traceSummary() {
      const empty = {
        window_hours: 24,
        tasks: 0,
        by_status: {},
        pass_rate: null,
        avg_quality: null,
        avg_cost_usd: null,
      };
      return pick(
        () => http('GET', '/api/trace/summary'),
        () => pair(mock().traceSummary, empty)
      );
    },
    // taskId → the assembled deep-dive; runId (optional) scopes to one run of
    // the request (default server-side: the latest content run). Both ids are
    // opaque → encodeURIComponent. Mock empty path is a fully honest-empty
    // deep-dive (no fabricated spine, no fabricated cost).
    traceDetail(taskId, runId) {
      const empty = {
        task_id: taskId || '',
        run_id: runId || null,
        runs: [],
        task: null,
        nodes: [],
        corpus: '',
        decisions: [],
        qa: [],
        cost_rollup: { by_model: [], total_usd: 0 },
        final: null,
        halt: null,
        langfuse: { session_id: taskId || '', run_id: runId || null },
      };
      return pick(
        () => {
          const q = runId ? '?run_id=' + encodeURIComponent(runId) : '';
          return http('GET', '/api/trace/' + encodeURIComponent(taskId) + q);
        },
        () => pair(mock().traceDetail, empty)
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
    // by_writer[]}. Map it onto the BrainPanel shape. This mapper owns ONLY the
    // embedding-corpus slice (totalEmbeddings / model / dim / bySource /
    // byWriter): the shared `brain` state has a second writer — brainActivity()
    // below owns decisions/decisions24h/… from /api/brain/stats — and app.jsx
    // spreads this result into that state, so any key emitted outside this
    // slice clobbers the other writer on every 60s resolve. (Stub decisions:[]
    // here predated /api/brain/stats and kept blanking the Brain panel's real
    // decisions; the live-mode wipe of the mock's daemon-internal fields now
    // happens once at state init in app.jsx.) Key set is contract-pinned in
    // contracts.manifest.js — extend both together, deliberately.
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
          };
        },
        () => mock().brain
      );
    },

    // ── brain daemon activity (brain_routes.py) ──────────────
    // GET /api/brain/stats → { decisions_24h, decisions_7d, avg_confidence_7d,
    //   last_cycle_at, knowledge_total,
    //   recent_decisions: [{id, decision, outcome, confidence, created_at}] }
    // Reads brain_decisions + brain_knowledge — NOT brain_queue (dropped 2026-04-21).
    // Mock returns honest-empty (no fabricated decision rows).
    brainActivity() {
      const empty = {
        decisions_24h: null,
        decisions_7d: null,
        avg_confidence_7d: null,
        last_cycle_at: null,
        knowledge_total: null,
        recent_decisions: [],
      };
      return pick(
        () => http('GET', '/api/brain/stats'),
        () => pair(empty, empty)
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
    // Live-activity pulse for the NOW RUNNING band: running work + recent trail
    // + per-kind summary. Live → GET /api/activity; mock → PX.activity.
    activity() {
      return pick(
        async () => {
          const r = await http('GET', '/api/activity');
          return (
            r || { running: [], recent: [], summary: { running_by_kind: {} } }
          );
        },
        () => PX.activity
      );
    },
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

    // GET /api/media-approval/{post_id}/{medium}/preview — the on-disk render,
    // streamed straight from the worker (the asset hasn't reached object
    // storage yet; Gate-2 is the gate that decides whether it's allowed to).
    // Bypasses http() (which always calls res.json()) — this needs the raw
    // bytes as a Blob so the drawer can play it via an inline <video>/<audio>
    // element. Same OAuth auth + 401-retry-once as http(), kept standalone
    // rather than refactored out of it (~30 call sites, JSON-only). Mock mode
    // has no real file to stream, so it rejects with a clear message instead
    // of fabricating one (feedback_no_dummy_data) — the caller shows that as
    // an "unavailable" state, not a fake player.
    async mediaPreviewBlob(postId, medium) {
      if (!cfg.live)
        throw new Error('Preview requires a live worker connection.');
      const path = `/api/media-approval/${encodeURIComponent(postId)}/${encodeURIComponent(medium)}/preview`;
      const doFetch = async () => {
        const tok = await getToken();
        return fetch((cfg.base || '') + path, {
          headers: { Authorization: 'Bearer ' + tok },
        });
      };
      let res = await doFetch();
      if (res.status === 401) {
        _tok = { value: '', exp: 0 };
        res = await doFetch();
      }
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw new Error(
          `GET ${path} → ${res.status} ${res.statusText}${detail ? ' — ' + detail : ''}`
        );
      }
      return res.blob();
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

    // ── graph approval gates (gates_routes.py) ──────────────
    // GET /api/gates/pending → canonical {items,…} envelope of tasks paused
    // at an interrupt() gate (awaiting_gate IS NOT NULL): task_id, gate_name,
    // artifact (the operator-review payload — for seo_refresh_gate that's the
    // proposed seo_title/seo_description), gate_paused_at, status, topic,
    // title. Feeds the NEEDS YOU kind='gate' lane. Mock: honest-empty.
    gatesPending() {
      return pick(
        () => http('GET', '/api/gates/pending?limit=50'),
        () => pair({ items: [] }, { items: [] })
      );
    },

    // POST /api/gates/pending/{task_id}/approve — records the approval and
    // resumes the paused LangGraph from its checkpoint in the background
    // (202 accepted; a failed resume rolls the approval back, so the row
    // reappears on the next gatesPending poll). Body: optional feedback note.
    gateApprove(taskId, feedback = '') {
      return pick(
        () =>
          http(
            'POST',
            `/api/gates/pending/${encodeURIComponent(taskId)}/approve`,
            { feedback: feedback || '' }
          ),
        () => ({ ok: true })
      );
    },

    // POST /api/gates/pending/{task_id}/reject — rejects the paused task.
    // Body: optional reason. For seo_refresh_gate the server also dismisses
    // the linked seo_opportunities row so the post isn't re-proposed.
    gateReject(taskId, reason = '') {
      return pick(
        () =>
          http(
            'POST',
            `/api/gates/pending/${encodeURIComponent(taskId)}/reject`,
            { reason: reason || '' }
          ),
        () => ({ ok: true })
      );
    },

    // ── social / Postiz draft queue (social_routes.py) ───────
    // GET /api/social/drafts → {drafts:[…]} — filterable by post_id/task_id/
    // status. Returns id, pipeline_task_id, post_id, platform, content,
    // platform_config, status, postiz_post_id, error, retry_count, title,
    // resolved_post_id, and three timestamps (created_at / approved_at /
    // posted_at). Per-post + per-platform granularity the aggregate
    // Prometheus counters can't provide. Mock returns honest-empty (no
    // fabricated draft rows).
    socialDrafts(params = '') {
      return pick(
        () => http('GET', '/api/social/drafts' + params),
        () => pair({ drafts: [] }, { drafts: [] })
      );
    },

    // POST /api/social/drafts/{id}/approve — enqueues the draft for Postiz
    //   /api/social/drafts/{id}/reject  — marks rejected (won't retry)
    // action is literally 'approve' or 'reject'.
    socialDraftAction(draftId, action) {
      return pick(
        () =>
          http(
            'POST',
            `/api/social/drafts/${encodeURIComponent(draftId)}/${action}`
          ),
        () => ({ ok: true })
      );
    },

    // ── newsletter (newsletter_routes.py) ────────────────────
    // GET /api/newsletter/stats → { subscriber_count, unsubscribed_count,
    //   last_30d: {sent, failed, total, delivery_rate, last_send_at},
    //   recent_campaigns: [{subject, date, sent, failed, total}, ...] }
    // Data from newsletter_subscribers + campaign_email_logs tables.
    // Mock returns honest-empty (no fabricated subscriber counts).
    newsletter() {
      const empty = {
        subscriber_count: 0,
        unsubscribed_count: 0,
        last_30d: {
          sent: 0,
          failed: 0,
          total: 0,
          delivery_rate: null,
          last_send_at: null,
        },
        recent_campaigns: [],
      };
      return pick(
        () => http('GET', '/api/newsletter/stats'),
        () => pair(empty, empty)
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
              // Show how long it's been UP (container_start_time), not the
              // scrape-freshness age — the latter is always ~2-15s and tells
              // the operator nothing when the service is healthy. Freshness
              // still drives ok/stale below and the LED, so nothing is lost.
              metric = 'up ' + fmtUptime(up[s.container]?.value);
            } else {
              status = 'warn';
              // Stale: here the scrape-age IS the point — surface how stale.
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
    // ── operator-triggered container restart (poindexter#909) ──
    // Restart is a brain/docker.sock action — the worker container has NO
    // docker.sock mount (only poindexter-brain-daemon does), so it can't
    // restart containers directly. POST queues an intent row; brain's own
    // poll loop (services/service_restart_requests.py ->
    // brain/service_restart.py) claims + executes it via the SAME
    // docker_restart_container helper the self-healing firefighter uses.
    // The live branch polls GET /api/services/restart/{id} until the row
    // reaches a terminal status (or times out — the restart may still be
    // in flight; the caller's own honest-'pending' handling covers that),
    // so the caller gets a REAL outcome instead of an optimistic guess.
    restartService(container) {
      return pick(
        async () => {
          const queued = await http(
            'POST',
            `/api/services/${encodeURIComponent(container)}/restart`
          );
          const id = queued && queued.id;
          if (!id) return queued;
          const POLL_MS = 1500;
          const MAX_ATTEMPTS = 12; // ~18s — comfortably past the 10s brain poll
          for (let i = 0; i < MAX_ATTEMPTS; i++) {
            await wait(POLL_MS);
            let row;
            try {
              row = await http(
                'GET',
                `/api/services/restart/${encodeURIComponent(id)}`
              );
            } catch {
              continue; // transient poll failure — try again, don't abandon
            }
            if (row && (row.status === 'done' || row.status === 'failed')) {
              return row;
            }
          }
          // Still pending/claimed after the poll window — honest "not sure
          // yet" rather than a fabricated success. The next serviceHealth()
          // poll will reflect reality regardless.
          return { id, container, status: 'pending' };
        },
        () => ({
          id: 'mock',
          container,
          status: 'done',
          detail: `restarted ${container} (mock)`,
        })
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

    // ── time-series trends (Prometheus, via promRange) ──────────
    // Verified metric names/labels: poindexter_http_requests_total {method,route,
    // status}; poindexter_http_request_duration_seconds_bucket {le};
    // poindexter_posts_total Gauge by status; poindexter_daily_spend_usd.
    // pick-wrapped like gpu(): mock mode shows "no data" (never hits Prometheus).
    httpRateSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          labelledRange(
            `sum(rate(poindexter_http_requests_total[${winFor(o)}]))`,
            o,
            'req/s'
          ),
        () => ({ series: [] })
      );
    },
    httpErrorSeries(range) {
      const o = rangeOpts(range);
      const w = winFor(o);
      return pick(
        () =>
          labelledRange(
            `sum(rate(poindexter_http_requests_total{status=~"5.."}[${w}])) ` +
              `/ sum(rate(poindexter_http_requests_total[${w}])) * 100`,
            o,
            '5xx %'
          ),
        () => ({ series: [] })
      );
    },
    httpLatencySeries(range) {
      const o = rangeOpts(range);
      const ql = (q) =>
        `histogram_quantile(${q}, sum(rate(` +
        `poindexter_http_request_duration_seconds_bucket[${winFor(o)}])) by (le))`;
      return pick(
        async () => {
          const [p95, p99] = await Promise.all([
            labelledRange(ql('0.95'), o, 'p95'),
            labelledRange(ql('0.99'), o, 'p99'),
          ]);
          return { series: [...p95.series, ...p99.series] };
        },
        () => ({ series: [] })
      );
    },
    throughputSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          labelledRange(
            'poindexter_posts_total{status="published"}',
            o,
            'published'
          ),
        () => ({ series: [] })
      );
    },
    costSeries(range) {
      // Two axes, never blended: on the total gauge a paid-provider leak
      // hides under the ~$1.4/day electricity baseline.
      const o = rangeOpts(range);
      return pick(
        async () => {
          const [apiUsd, elecUsd] = await Promise.all([
            labelledRange('poindexter_daily_api_spend_usd', o, 'API'),
            labelledRange(
              'poindexter_daily_electricity_spend_usd',
              o,
              'electricity'
            ),
          ]);
          return { series: [...apiUsd.series, ...elecUsd.series] };
        },
        () => ({ series: [] })
      );
    },

    // ── time-series trends (GPU / hardware / power — Prometheus) ─
    // Gauges: no rate() window, so query_range samples them at each step and
    // the PromQL is range-independent. max by (gpu) strips instance/job so the
    // per-GPU label seam legends them "GPU 0" / "GPU 1" (colorblind-safe via
    // TimeChart's dash + end-label).
    gpuUtilSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_utilization_percent)', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    gpuTempSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_temperature_celsius)', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    vramUsedSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_memory_used_mib) / 1024', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    gpuPowerSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_power_draw_watts)', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    systemPowerSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          labelledRange(
            'psu_total_power_watts or system_total_power_estimate_watts',
            o,
            'total'
          ),
        () => ({ series: [] })
      );
    },
    // Live electricity rate ($/kWh), same settings-read pattern as
    // voiceJoinUrl(). Returns null (never a fabricated rate) when the
    // setting is missing or non-numeric.
    electricityRateKwh() {
      return pick(
        async () => {
          const r = await http(
            'GET',
            '/api/settings?search=electricity_rate_kwh&limit=10'
          );
          const hit = ((r && r.items) || []).find(
            (s) => s.key === 'electricity_rate_kwh'
          );
          const v = hit && Number(hit.value);
          return v && isFinite(v) && v > 0 ? v : null;
        },
        () => null
      );
    },
    // $/day time series, Shelly-first (same fallback as systemPowerSeries),
    // scaled by the live rate. Honest-empty when the rate is unavailable —
    // never assumes $0.
    electricityCostSeries(range) {
      const o = rangeOpts(range);
      return pick(
        async () => {
          const rate = await this.electricityRateKwh();
          if (!rate) return { series: [] };
          return labelledRange(
            `(psu_total_power_watts or system_total_power_estimate_watts) / 1000 * ${rate} * 24`,
            o,
            'total'
          );
        },
        () => ({ series: [] })
      );
    },
    // Maps cost_ledger's electricity_source (+ coverage) to an
    // operator-legible note for the Cost Control card's Energy row.
    electricitySourceNote(source, coveragePct) {
      if (source === 'measured') return 'measured, live wall power';
      if (source === 'estimated' || source === 'mixed') {
        const pct = coveragePct != null ? Math.round(coveragePct) : 0;
        return `estimated — ${pct}% sensor coverage this window`;
      }
      return '— pending';
    },

    // ── time-series trends (Postgres internals — postgres_exporter) ─
    // Cluster-wide sums except size (per real DB) + state (per state). The
    // two 2-series merges mirror httpLatencySeries. datname is matched exactly
    // to skip ephemeral unit/e2e test DBs.
    dbConnectionsSeries(range) {
      const o = rangeOpts(range);
      return pick(
        async () => {
          const [inUse, max] = await Promise.all([
            labelledRange('sum(pg_stat_database_numbackends)', o, 'in use'),
            labelledRange('pg_settings_max_connections', o, 'max'),
          ]);
          return { series: [...inUse.series, ...max.series] };
        },
        () => ({ series: [] })
      );
    },
    dbConnStateSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange(
            'sum by (state) (pg_stat_activity_count{state=~"active|idle|idle in transaction"})',
            { ...o, labelBy: 'state' }
          ),
        () => ({ series: [] })
      );
    },
    dbCacheHitSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          labelledRange(
            'sum(pg_stat_database_blks_hit) / ' +
              '(sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100',
            o,
            'hit %'
          ),
        () => ({ series: [] })
      );
    },
    dbTxnRateSeries(range) {
      const o = rangeOpts(range);
      const w = winFor(o);
      return pick(
        async () => {
          const [c, r] = await Promise.all([
            labelledRange(
              `sum(rate(pg_stat_database_xact_commit[${w}]))`,
              o,
              'commits/s'
            ),
            labelledRange(
              `sum(rate(pg_stat_database_xact_rollback[${w}]))`,
              o,
              'rollbacks/s'
            ),
          ]);
          return { series: [...c.series, ...r.series] };
        },
        () => ({ series: [] })
      );
    },
    dbSizeSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange(
            'pg_database_size_bytes{datname=~"poindexter|poindexter_brain"} / 1073741824',
            { ...o, labelBy: 'datname' }
          ),
        () => ({ series: [] })
      );
    },
    dbDeadTuplesSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          labelledRange(
            'sum(pg_stat_user_tables_n_dead_tup)',
            o,
            'dead tuples'
          ),
        () => ({ series: [] })
      );
    },

    // ── time-series trends (worker / audit_log) ─────────────────
    // Frontend derives the bucket grid once (rangeOpts) and passes it explicitly,
    // so the grid is never re-derived server-side (no drift). The route clamps.
    qaTrend(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          http(
            'GET',
            `/api/qa/trend?range_seconds=${o.rangeSeconds}&step_seconds=${o.stepSeconds}`
          ),
        () => ({ series: [] })
      );
    },
    findingsTrend(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          http(
            'GET',
            `/api/findings/trend?range_seconds=${o.rangeSeconds}&step_seconds=${o.stepSeconds}`
          ),
        () => ({ series: [] })
      );
    },
  };

  // Tiny boot hint in the console for whoever wires this up.
  if (!cfg.live) {
    console.info(
      '[PX.api] MOCK mode (demo data — NOT your stack). Served-from-worker pages default to live; this page is either hosted elsewhere or explicitly opted out (px_live="0"). PX.api.setClient("client_id","client_secret"); PX.api.setLive(true) to go live.'
    );
  } else if (!cfg.clientId || !cfg.clientSecret) {
    console.warn(
      '[PX.api] LIVE mode without OAuth creds — panels will error until you set them: App Settings → Connection (client from `poindexter auth register-client --name poindexter-console`).'
    );
  }
})();
