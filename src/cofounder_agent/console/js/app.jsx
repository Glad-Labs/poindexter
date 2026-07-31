/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — app shell + orchestration.
   ────────────────────────────────────────────────────────────── */
const { useState: useS, useEffect: useE, useRef: useR, useMemo } = React;

// Poindexter version shown in the topbar eyebrow. release-please bumps the
// literal below on every release (see the `generic` extra-files entry in
// release-please-config.json) so the console tracks the real build instead of
// drifting. Keep the `// x-release-please-version` annotation on this line.
const POINDEXTER_VERSION = '0.114.0'; // x-release-please-version

const RAIL = [
  { id: 'overview', icon: 'overview', label: 'Overview' },
  { id: 'pipeline', icon: 'pipeline', label: 'Pipeline' },
  { id: 'topics', icon: 'overview', label: 'Topics' },
  { id: 'social', icon: 'pulse', label: 'Social' },
  { id: 'brain', icon: 'brain', label: 'Brain' },
  { id: 'gpu', icon: 'gpu', label: 'GPU' },
  { id: 'services', icon: 'services', label: 'Services' },
  { id: 'audit', icon: 'audit', label: 'Audit' },
  { id: 'findings', icon: 'bell', label: 'Findings' },
  { id: 'cost', icon: 'cost', label: 'Cost' },
  { id: 'revenue', icon: 'pulse', label: 'Revenue' },
  { id: 'telemetry', icon: 'audit', label: 'Telemetry' },
];

function App() {
  const PX = window.PX;
  const [inbox, setInbox] = useS(PX.inbox);
  const [approved, setApproved] = useS([]); // live: staged tasks, awaiting publish
  const [gpu, setGpu] = useS(PX.gpu);
  const [pipeline, setPipeline] = useS(PX.pipeline); // live: real /api/tasks
  // Migrated to usePolledResource (Task 9 — see the "Live poll resources"
  // block below): services, cost, logs, findings, seo, newsletter, traces,
  // media, schedule, topics.
  // Brain state has TWO live writers (memory-stats + brain-activity effects
  // below), each owning a disjoint slice. Live starts the brain-daemon fields
  // honest-empty instead of the mock's fabricated rows (feedback_no_dummy_data):
  // decisions belong to /api/brain/stats from its first resolve; growth /
  // recent / queueDepth / lastCycle have no HTTP route at all. This one-time
  // wipe replaces the stub fields the memoryStats mapper used to emit — those
  // re-blanked `decisions` on every 60s resolve, clobbering the real rows the
  // 5-min brain-activity effect wrote ("no decisions yet" ~80% of the time).
  const [brain, setBrain] = useS(() =>
    PX.api.isLive()
      ? {
          ...PX.brain,
          decisions: [],
          growth: [],
          recent: [],
          queueDepth: null,
          lastCycle: null,
        }
      : PX.brain
  );
  const [social, setSocial] = useS({ drafts: [] }); // live: GET /api/social/drafts
  const [logFilter, setLogFilter] = useS({ service: '', level: '' });
  // live: KPI-strip reads with no home panel — GET /api/posts (published 30d
  // histogram + avg quality 30d) + GET /api/analytics/views (page views 24h)
  // + GET /api/tasks?status=failed (failed 24h, windowed in kpis.js). Mock
  // keeps PX.kpis untouched (the `kpis` memo below short-circuits in mock mode).
  const [kpiReads, setKpiReads] = useS({
    posts: null,
    views: null,
    failedTasks: null,
  });
  const [feed, setFeed] = useS(() =>
    // Live starts empty — the real /api/pipeline/events poll fills it (never
    // show the mock seed on live, feedback_no_dummy_data). Mock seeds the demo.
    PX.api.isLive()
      ? []
      : PX.auditSeed.map((l, i) => ({ ...l, key: 'seed' + i }))
  );
  const [entity, setEntity] = useS(null);
  const [filter, setFilter] = useS('all');
  const [feedFilter, setFeedFilter] = useS('all');
  const [active, setActive] = useS('overview');
  const [mode, setMode] = useS('console');
  const [traceTaskId, setTraceTaskId] = useS(null);
  const [paletteOpen, setPaletteOpen] = useS(false);
  const [clock, setClock] = useS('14:32:00');
  const [toastNode, pushToast] = useToasts();
  const mainRef = useR(null);
  const feedKey = useR(0);
  // Connection heartbeat — the ONLY resource that drives the global banner.
  // Called unconditionally (Rules of Hooks); mock mode resolves ok so it never
  // trips. 30s cadence: fast enough to notice an outage, cheap enough to always
  // poll. Each poll re-renders App, so the topbar SYNC age stays current too.
  const health = window.PXR.usePolledResource(
    () => (PX.api.isLive() ? PX.api.health() : Promise.resolve({ ok: true })),
    { intervalMs: 30_000, key: 'health' }
  );

  // ── Live poll resources (Task 9: clean single-state effects migrated onto
  // usePolledResource). Each derives `x = xR.data || PX.x` and passes `fresh={xR}`
  // to its panel for the per-panel <Freshness> badge. On a failed poll the hook
  // retains last-good data + flips stale (the badge is the signal); the old
  // per-poll error toast is dropped as popup-spam (the global ConnectionBanner
  // covers a full outage). Bespoke effects with transforms / multi-state /
  // dedup+animation (feed, approvals, tasks, brain, social, kpiExtras) stay
  // hand-rolled below — see the "Task 9 exception" comments there.
  const servicesR = window.PXR.usePolledResource(
    () =>
      PX.api.isLive()
        ? PX.api.serviceHealth().then((rows) => {
            if (!Array.isArray(rows))
              throw new Error('serviceHealth: unexpected shape');
            return rows;
          })
        : Promise.resolve(PX.services),
    { intervalMs: 30_000, key: 'serviceHealth' }
  );
  const services = servicesR.data || PX.services;

  // GPU scheduler queue (poindexter#914 P0) — holder + waiters + hold stats
  // for the GPU panel's "holder / waiting" strip. 10s: queue movement is
  // operator-watchable but not sub-second.
  const GPU_QUEUE_EMPTY = { holder: null, waiters: [], stats: [] };
  const gpuQueueR = window.PXR.usePolledResource(
    () =>
      PX.api.isLive() ? PX.api.gpuQueue() : Promise.resolve(GPU_QUEUE_EMPTY),
    { intervalMs: 10_000, key: 'gpuQueue' }
  );
  const gpuQueue = gpuQueueR.data || GPU_QUEUE_EMPTY;

  const costR = window.PXR.usePolledResource(
    () => {
      if (!PX.api.isLive()) return Promise.resolve(PX.cost);
      return PX.api.budget().then((b) => {
        if (!b) throw new Error('budget: empty read');
        // Merge the live spend read onto the PX.cost base: static facts
        // ($0 infra, notes) come from the base; byModel/daily stay empty
        // until those reads are routed (honest-empty, not mocked).
        // electricity_* now rides the same budget() read (cost_aggregation_
        // service surfaces cost_ledger's measured ledger) — real, not
        // Prometheus-estimated.
        return {
          ...PX.cost,
          monthToDate: b.amount_spent ?? PX.cost.monthToDate,
          budget: b.monthly_budget ?? PX.cost.budget,
          projected: b.projected_final_cost ?? PX.cost.projected,
          dailyBurn: b.daily_burn_rate ?? PX.cost.dailyBurn,
          percentUsed: b.percent_used ?? PX.cost.percentUsed,
          status: b.status ?? PX.cost.status,
          alerts: b.alerts ?? [],
          byModel: [],
          daily: [],
          electricityUsdMonth: b.electricity_usd ?? null,
          electricitySource: b.electricity_source ?? 'none',
          electricityCoveragePct: b.electricity_coverage_pct ?? 0,
        };
      });
    },
    { intervalMs: 5 * 60 * 1000, key: 'budget' }
  );
  const cost = costR.data || PX.cost;

  // Logs re-fetch immediately when the service/level filter changes: the filter
  // is encoded in `key`, and usePolledResource re-runs its effect on key change.
  const logsR = window.PXR.usePolledResource(
    () => {
      if (!PX.api.isLive()) return Promise.resolve(PX.logs);
      const qs =
        `?since=1h&limit=300` +
        (logFilter.service
          ? `&service=${encodeURIComponent(logFilter.service)}`
          : '') +
        (logFilter.level
          ? `&level=${encodeURIComponent(logFilter.level)}`
          : '');
      return PX.api.logs(qs);
    },
    { intervalMs: 10_000, key: `logs:${logFilter.service}:${logFilter.level}` }
  );
  const logs = logsR.data || PX.logs;

  const findingsR = window.PXR.usePolledResource(
    () => (PX.api.isLive() ? PX.api.findings() : Promise.resolve(PX.findings)),
    { intervalMs: 5 * 60 * 1000, key: 'findings' }
  );
  const findings = findingsR.data || PX.findings;

  const seoR = window.PXR.usePolledResource(
    () => (PX.api.isLive() ? PX.api.seo() : Promise.resolve(PX.seo)),
    { intervalMs: 5 * 60 * 1000, key: 'seo' }
  );
  const seo = seoR.data || PX.seo;

  // newsletter has no mock base (honest-empty null in mock mode, per its
  // isLive-gated original); derive straight off the resource, no `|| PX.x`.
  const newsletterR = window.PXR.usePolledResource(
    () => (PX.api.isLive() ? PX.api.newsletter() : Promise.resolve(null)),
    { intervalMs: 5 * 60 * 1000, key: 'newsletter' }
  );
  const newsletter = newsletterR.data;

  const tracesR = window.PXR.usePolledResource(
    () =>
      PX.api.isLive()
        ? PX.api.traces('?hours=24&limit=50')
        : Promise.resolve(PX.traces),
    { intervalMs: 60 * 1000, key: 'traces' }
  );
  const traces = tracesR.data || PX.traces;

  // Task-trace board (front-door): running + recent tasks. 30s cadence like the
  // other observ polls; the deep-dive (TraceDeepDive) owns its own faster poll.
  // Passed straight to <TraceBoard> as data + fresh (retains last-good + marks
  // stale on a blip, so a blank board is never mistaken for an idle system).
  const traceR = window.PXR.usePolledResource(
    () =>
      PX.api.isLive()
        ? PX.api.traceActive()
        : Promise.resolve(PX.traceActive || { runs: [], recent: [] }),
    { intervalMs: 30_000, key: 'traceActive' }
  );

  // Live-activity pulse — feeds the SYSTEM PULSE band. ~3s so "what's running
  // now" feels live; the ledger read is two cheap indexed queries. Truly-running
  // work comes from the ledger, not a loose status filter (fixes the
  // taskStatusKind over-mapping the mock band carried).
  const activityR = window.PXR.usePolledResource(
    () => (PX.api.isLive() ? PX.api.activity() : Promise.resolve(PX.activity)),
    { intervalMs: 3000, key: 'activity' }
  );
  const activity = activityR.data || PX.activity;

  // These three poll cleanly but ALSO carry optimistic drawer actions (Gate-2
  // decide / reschedule / topic triage). The poll migrates here; the drawer
  // handlers below call `<x>R.mutate(updater)` for the optimistic patch and
  // `<x>R.mutate(() => prev)` to roll back on a failed write.
  const mediaR = window.PXR.usePolledResource(
    () => (PX.api.isLive() ? PX.api.mediaQueue() : Promise.resolve(PX.media)),
    { intervalMs: 60 * 1000, key: 'media' }
  );
  const media = mediaR.data || PX.media;

  const scheduleR = window.PXR.usePolledResource(
    () => (PX.api.isLive() ? PX.api.schedule() : Promise.resolve(PX.schedule)),
    { intervalMs: 60 * 1000, key: 'schedule' }
  );
  const schedule = scheduleR.data || PX.schedule;

  const topicsR = window.PXR.usePolledResource(
    () => {
      if (!PX.api.isLive()) return Promise.resolve(PX.topics);
      return PX.api.listTopicProposals().then((res) =>
        // Canonical offset envelope (poindexter#745): read `.items`, not `.batches`.
        res && res.items ? res : { items: [], total: 0, limit: 0, offset: 0 }
      );
    },
    { intervalMs: 5 * 60 * 1000, key: 'topics' }
  );
  const topics = topicsR.data || PX.topics;

  // ── Live simulation (subtle) ──────────────────────────────
  useE(() => {
    const feedTimer = setInterval(() => {
      // Live mode's feed is driven by the real /api/pipeline/events poll
      // (effect below); the random simulator is mock-only.
      if (PX.api.isLive()) return;
      const tpl =
        PX.liveTemplates[Math.floor(Math.random() * PX.liveTemplates.length)]();
      const line = {
        ...tpl,
        ts: PX.nextTs(),
        fresh: true,
        key: 'live' + feedKey.current++,
      };
      setFeed((f) => [line, ...f].slice(0, 40));
      setClock(PX.hhmmss(PX.now));
      setTimeout(
        () =>
          setFeed((f) =>
            f.map((x) => (x.key === line.key ? { ...x, fresh: false } : x))
          ),
        1200
      );
    }, 5200);
    const gpuTimer = setInterval(() => {
      // Live: poll the real nvidia_gpu_* gauges (api.gpu) and shift the real
      // reading into the sparkline history. Mock: subtle local jitter so the
      // gauges feel alive without a backend.
      if (PX.api.isLive()) {
        PX.api
          .gpu()
          .then((real) =>
            setGpu((g) => ({
              ...g,
              ...real,
              utilHist: [...g.utilHist.slice(1), Math.round(real.util)],
              tempHist: [...g.tempHist.slice(1), Math.round(real.temp)],
            }))
          )
          .catch(() => {});
        return;
      }
      setGpu((g) => {
        const util = Math.max(
          40,
          Math.min(96, g.util + (Math.random() * 10 - 5))
        );
        const temp = Math.max(
          58,
          Math.min(82, g.temp + (Math.random() * 2 - 1))
        );
        const power = Math.max(
          360,
          Math.min(560, g.power + (Math.random() * 30 - 15))
        );
        return {
          ...g,
          util: Math.round(util),
          temp: Math.round(temp),
          power: Math.round(power),
          utilHist: [...g.utilHist.slice(1), Math.round(util)],
        };
      });
    }, 3000);
    return () => {
      clearInterval(feedTimer);
      clearInterval(gpuTimer);
    };
  }, []);

  // ── Live: real audit feed (GET /api/pipeline/events) ──────────────────
  // Task 9 exception (stays a bespoke effect): not a clean fetch→setState —
  // it dedups against a seen-id ref, prepends+slices a rolling buffer, runs a
  // fresh-flag fade animation, and drives setClock. No <Freshness> badge (the
  // feed is a live stream, not a snapshot panel).
  // Mock keeps the local simulator above. On live we poll the real pipeline
  // events (QA decisions, rewrites, task lifecycle), map them to feed lines in
  // the adapter, and prepend new ones — deduped by audit_log event id — so the
  // feed shows ACTUAL decisions, never fabricated lines (feedback_no_dummy_data).
  useE(() => {
    if (!PX.api.isLive()) return undefined;
    let alive = true;
    const seen = new Set();
    const tick = async () => {
      try {
        const lines = await PX.api.pipelineEvents();
        if (!alive || !Array.isArray(lines)) return;
        const incoming = lines.filter(
          (l) => l && l.id != null && !seen.has(l.id)
        );
        if (!incoming.length) return;
        incoming.forEach((l) => seen.add(l.id));
        const add = incoming.map((l) => ({
          ...l,
          fresh: true,
          key: 'ev' + l.id,
        }));
        setClock(PX.hhmmss(new Date()));
        setFeed((f) => [...add, ...f].slice(0, 40));
        add.forEach((l) =>
          setTimeout(() => {
            if (alive)
              setFeed((g) =>
                g.map((x) => (x.key === l.key ? { ...x, fresh: false } : x))
              );
          }, 1200)
        );
      } catch (_e) {
        /* honest-empty: leave the feed as-is on a transient error */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // ── Live: load real pending approvals into the inbox ──────
  // Task 9 exception (stays a bespoke effect): writes TWO states — it maps the
  // approvals via approvalToInbox into `inbox` AND sets `approved` from a second
  // list call. One usePolledResource can't own two states, so it stays hand-rolled.
  // In live mode the inbox shows ONLY real /api/tasks/pending-approval rows.
  // Other inbox kinds (fail/alert/drift/media) stay empty here until their own
  // phases wire them — we never carry mock rows into a live view.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const [pending, appr] = await Promise.all([
          PX.api.listApprovals(),
          PX.api.listTasks('?status=approved&limit=50'),
        ]);
        if (!alive) return;
        // pending-approval now returns the canonical {items,…} envelope (poindexter#745).
        setInbox(((pending && pending.items) || []).map(approvalToInbox));
        // GET /api/tasks now returns the canonical {items,…} envelope (poindexter#745).
        setApproved((Array.isArray(appr) ? appr : appr && appr.items) || []);
      } catch (e) {
        pushToast(`Approvals load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: load real tasks into the Pipeline panel ─────────
  // Task 9 exception (stays a bespoke effect): not a plain setState — it maps
  // rows via taskToRow then MERGES them into the existing pipeline shape with
  // withLiveCounts (functional update over prior state), so it stays hand-rolled.
  // Maps /api/tasks rows → the panel task shape and derives per-block counts
  // from each task's current `stage` (the real graph_def node). Mock mode is
  // untouched. Polls on the same 5-min cadence as approvals.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.listTasks('?limit=50');
        if (!alive) return;
        // GET /api/tasks now returns the canonical {items,…} envelope (poindexter#745).
        const rows = ((res && res.items) || []).map(taskToRow);
        setPipeline((p) => withLiveCounts(p, rows));
      } catch (e) {
        pushToast(`Tasks load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: real embedding corpus (GET /api/memory/stats) ───
  // Task 9 exception (stays a bespoke effect): the `brain` state is fed by TWO
  // effects — this corpus fetch AND the brain-daemon-activity fetch below — each
  // a functional merge into the shared state. Two resources can't own one state,
  // so both stay hand-rolled (a future combine-into-one-resource could migrate them).
  // Maps total + by_source_table + by_writer onto the Brain panel. The spread
  // below is safe ONLY because memoryStats() maps just the embedding-corpus
  // slice (contract-pinned in contracts.manifest.js) — a key outside that slice
  // would clobber the brain-activity effect's fields on every resolve.
  // Mock mode keeps PX.brain. 60s cadence (the corpus grows slowly).
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.memoryStats();
        if (!alive || !res) return;
        setBrain((prev) => ({ ...prev, ...res }));
      } catch (e) {
        pushToast(`Memory stats load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: brain daemon activity (GET /api/brain/stats) ───
  // Task 9 exception (stays a bespoke effect): second writer of the shared
  // `brain` state (see the memory-stats effect above) — stays hand-rolled.
  // Merges decisions_24h/7d + knowledge_total + recent_decisions into the same
  // brain state the corpus fetch above populates. Functional update avoids
  // overwriting the corpus fields. 5min cadence (brain cycles are ~5 min).
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.brainActivity();
        if (!alive || !res) return;
        setBrain((prev) => ({
          ...prev,
          decisions24h: res.decisions_24h,
          decisions7d: res.decisions_7d,
          avgConfidence7d: res.avg_confidence_7d,
          lastCycleAt: res.last_cycle_at,
          knowledgeTotal: res.knowledge_total,
          decisions: (res.recent_decisions || []).map((d) => ({
            ts: d.created_at ? d.created_at.slice(11, 16) : '??:??',
            kind: d.outcome || 'decision',
            msg: d.decision,
            tone:
              d.confidence != null && d.confidence >= 0.8 ? 'cyan' : 'amber',
          })),
        }));
      } catch (_e) {
        /* honest-empty — panel keeps placeholders */
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: social draft queue (GET /api/social/drafts) ───────
  // Task 9 exception (stays a bespoke effect): writes TWO states — `social` AND
  // `inbox` (pending drafts are mapped via draftToInbox and merged into the
  // action inbox). One resource can't own both, so it stays hand-rolled.
  // Fetches recent drafts for per-post per-platform visibility — granularity
  // the Grafana aggregate Prometheus counters don't provide. Pending drafts also
  // surface in the action inbox as kind='social' for inline approve/reject.
  // ?limit is a real server-side cap (social_post_drafts only grows — one row
  // per platform per post, tombstones never pruned). It's safe for the inbox
  // because the server sorts pending/failed ahead of created_at, so the cap can
  // only drop posted/rejected rows; counts come back as status_counts/total.
  // Mock: honest-empty (no fabricated rows per feedback_no_dummy_data). 60s cadence
  // (drafts move on the same scale as publishing tasks, not second-to-second).
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.socialDrafts('?limit=50');
        if (!alive || !res) return;
        setSocial(res);
        // Action Inbox is the "act on this now" surface, so it only takes
        // drafts that would actually succeed — same condition approve_draft's
        // post-link gate checks server-side (post_status === 'published').
        // The Social tab keeps showing every draft, gated or not — approving
        // a not-yet-ready one there still works fine, it just 409s with the
        // real reason instead of the inbox pretending nothing's queued.
        const pendingDrafts = (res.drafts || []).filter(
          (d) => d.status === 'pending' && d.post_status === 'published'
        );
        setInbox((prev) => {
          const nonSocial = prev.filter((i) => i.kind !== 'social');
          return [...nonSocial, ...pendingDrafts.map(draftToInbox)];
        });
      } catch (e) {
        pushToast(`Social drafts load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: graph approval-gate queue (GET /api/gates/pending) ─
  // Task 9 exception (stays a bespoke effect): replace-by-kind merge into the
  // shared `inbox` state (same shape as the social-drafts effect above).
  // Tasks paused at an interrupt() gate — today that's seo_refresh runs at
  // seo_refresh_gate (approval-FIRST by design; the proposed title/meta wait
  // for operator sign-off before republish) — surface as kind='gate' for
  // inline approve/reject. Before this lane they only showed on the Trace
  // board with no action path, and parked runs silently accumulated. Mock:
  // honest-empty. 60s cadence (gate parks move on operator timescales).
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.gatesPending();
        if (!alive || !res) return;
        const items = (res && res.items) || [];
        setInbox((prev) => {
          const nonGate = prev.filter((i) => i.kind !== 'gate');
          return [...nonGate, ...items.map(gateToInbox)];
        });
      } catch (e) {
        pushToast(`Gate queue load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: merge the Gate-2 media queue into the Action Inbox ──
  // `media` (mediaR, above) already polls GET /api/media-approval/pending on
  // its own 60s cadence for the MediaPanel card — this closes the gap noted
  // on the approvals effect ("Other inbox kinds … stay empty … until their
  // own phases wire them"). Gate-2 items are exactly as operator-actionable
  // as approvals/social, so mirror them in too — same replace-by-kind merge
  // as the social-drafts effect above. Purely DERIVED off `media` (no extra
  // fetch): unlike the social merge, A.mediaApprove/A.mediaReject's optimistic
  // mediaR.mutate() already updates `media`, so this effect re-fires on its
  // own — those handlers don't need to also touch `inbox` directly.
  useE(() => {
    if (!PX.api.isLive()) return;
    setInbox((prev) => {
      const nonMedia = prev.filter((i) => i.kind !== 'media');
      const queue = (media && media.queue) || [];
      return [...nonMedia, ...queue.map(mediaToInbox)];
    });
  }, [media]);

  // ── Live: KPI strip reads (GET /api/posts + /api/analytics/views +
  // GET /api/tasks?status=failed) ──
  // Task 9 exception (stays a bespoke effect): a 3-way Promise.all fan-in
  // (posts + views + failedTasks, each independently .catch→null) into one
  // `kpiReads` set — the KPI strip has no single panel header to badge, so it
  // stays hand-rolled rather than becoming a usePolledResource.
  // The overview KPIs are mostly a projection of state other panels already
  // load (cost → spend, inbox → awaiting-approval); these reads cover the
  // rest. posts → published-in-30d histogram AND avg-quality-30d (the
  // quality_score field landed on /api/posts 2026-07 via the
  // pipeline_versions seam); analytics(days=1) → page views over the last
  // 24h; failed tasks → 24h count windowed client-side in kpis.js (same
  // pattern as published-30d). On a read failure we store null so that KPI
  // renders honest-empty, never the mock value (feedback_no_dummy_data).
  // 5-min cadence — these move slowly.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      const [posts, views, failedTasks] = await Promise.all([
        PX.api.posts('?limit=100&published_only=true').catch(() => null),
        PX.api.analyticsViews('?days=1').catch(() => null),
        PX.api.listTasks('?status=failed&limit=100').catch(() => null),
      ]);
      if (!alive) return;
      setKpiReads({ posts, views, failedTasks });
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── ⌘K command palette ────────────────────────────────────
  useE(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ── Scroll-spy for rail ───────────────────────────────────
  useE(() => {
    if (mode !== 'console') return;
    const el = mainRef.current;
    if (!el) return;
    const onScroll = () => {
      const cTop = el.getBoundingClientRect().top + 130;
      let cur = 'overview',
        best = -Infinity;
      for (const r of RAIL) {
        const s = document.getElementById('sec-' + r.id);
        if (!s) continue;
        const t = s.getBoundingClientRect().top;
        if (t <= cTop && t > best) {
          best = t;
          cur = r.id;
        }
      }
      setActive(cur);
    };
    el.addEventListener('scroll', onScroll);
    return () => el.removeEventListener('scroll', onScroll);
  }, [mode]);

  // ── #trace/<task_id> deep-link routing (the alert→trace target, R4) ──
  // On load + hashchange, jump straight into the deep-dive when the URL names a
  // task. Lets a Telegram/Discord alert link land the operator on the exact run.
  useE(() => {
    const applyHash = () => {
      const m = (location.hash || '').match(/^#trace\/(.+)$/);
      if (m) {
        setTraceTaskId(decodeURIComponent(m[1]));
        setMode('tracedetail');
      }
    };
    applyHash();
    window.addEventListener('hashchange', applyHash);
    return () => window.removeEventListener('hashchange', applyHash);
  }, []);

  const scrollToSec = (id) => {
    const el = mainRef.current,
      sec = document.getElementById('sec-' + id);
    if (!el || !sec) return;
    const top =
      el.scrollTop +
      (sec.getBoundingClientRect().top - el.getBoundingClientRect().top) -
      10;
    el.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  };

  const goTo = (id) => {
    if (mode !== 'console') {
      setMode('console');
      setActive(id);
      setTimeout(() => scrollToSec(id), 90);
      return;
    }
    scrollToSec(id);
    setActive(id);
  };

  // ── Action handlers ───────────────────────────────────────
  const removeInbox = (id) => setInbox((x) => x.filter((i) => i.id !== id));
  const pushFeed = (tag, html) => {
    const line = {
      tag,
      html,
      ts: PX.hhmmss(PX.now),
      fresh: true,
      key: 'act' + feedKey.current++,
    };
    setFeed((f) => [line, ...f].slice(0, 40));
    setTimeout(
      () =>
        setFeed((f) =>
          f.map((x) => (x.key === line.key ? { ...x, fresh: false } : x))
        ),
      1200
    );
  };
  const closeDrawer = () => setEntity(null);

  // ── Task-trace navigation + actions ───────────────────────
  const openTrace = (taskId) => {
    setTraceTaskId(taskId);
    setMode('tracedetail');
    location.hash = 'trace/' + encodeURIComponent(taskId);
  };
  const backFromTrace = () => {
    setMode('trace');
    if ((location.hash || '').indexOf('#trace/') === 0) location.hash = '';
  };
  // taskId-scoped approve/reject/publish for the deep-dive header (A.* takes
  // inbox entities; the deep-dive only has a task id). The deep-dive's own 6s
  // poll reconciles the header/actions after the write.
  const traceAction = async (kind, taskId) => {
    try {
      if (kind === 'approve') {
        await PX.api.approve(taskId);
        pushToast('Approved — staged (not published)', 'mint', '✓');
        pushFeed(
          ['mint', 'APPROVE'],
          `operator approved <b>#${taskId}</b> → staged`
        );
      } else if (kind === 'reject') {
        await PX.api.reject(taskId, '');
        pushToast('Rejected — sent back to edit', 'amber', '⚠');
        pushFeed(['amber', 'REVIEW'], `operator rejected <b>#${taskId}</b>`);
      } else if (kind === 'publish') {
        await PX.api.publishTask(taskId);
        pushToast('Published — is live', 'mint', '✓');
        pushFeed(['mint', 'PUBLISH'], `operator published <b>#${taskId}</b>`);
      }
    } catch (err) {
      pushToast(`${kind} failed — ${err.message}`, 'red', '✕');
    }
  };
  // Langfuse escape hatch: resolve the task's newest trace via the existing
  // /api/traces proxy (web_url is built server-side) and open it. No fabricated
  // URL — if the task has no trace yet, say so honestly.
  const traceLangfuse = async (session) => {
    if (!session) return;
    try {
      const r = await PX.api.traces(
        '?task_id=' + encodeURIComponent(session) + '&limit=1'
      );
      const t = r && r.traces && r.traces[0];
      const url = t && (t.web_url || t.url);
      if (url) window.open(url, '_blank', 'noopener');
      else pushToast('No Langfuse trace for this task yet', 'amber', '⚠');
    } catch (err) {
      pushToast(`Langfuse lookup failed — ${err.message}`, 'red', '✕');
    }
  };

  const A = {
    // Approve STAGES the task (auto_publish=false). Optimistic remove + roll
    // back on failure. Publish is a separate gate (below).
    approve: async (e) => {
      const prev = inbox;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.approve(e.id);
        pushToast(
          `Approved — “${trunc(e.title)}” staged (not published)`,
          'mint',
          '✓'
        );
        pushFeed(
          ['mint', 'APPROVE'],
          `operator approved <b>${trunc(e.title)}</b> → staged`
        );
        // Surface it in the ready-to-publish list (the poll reconciles later).
        setApproved((a) => [
          { id: e.id, title: e.title, quality: e.detail?.quality },
          ...a.filter((t) => t.id !== e.id),
        ]);
      } catch (err) {
        setInbox(prev);
        pushToast(`Approve failed — ${err.message}`, 'red', '✕');
      }
    },
    // Publish SHIPS a staged task — the deliberate second gate after approve.
    publish: async (e) => {
      closeDrawer();
      try {
        await PX.api.publishTask(e.id);
        setApproved((a) => a.filter((t) => t.id !== e.id));
        pushToast(`Published — “${trunc(e.title)}” is live`, 'mint', '✓');
        pushFeed(
          ['mint', 'PUBLISH'],
          `operator published <b>${trunc(e.title)}</b>`
        );
      } catch (err) {
        pushToast(`Publish failed — ${err.message}`, 'red', '✕');
      }
    },
    reject: async (e) => {
      const prev = inbox;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.reject(e.id, e.detail?.feedback || '');
        pushToast(`Rejected — sent back to edit`, 'amber', '⚠');
        pushFeed(
          ['amber', 'REVIEW'],
          `operator rejected <b>${trunc(e.title || '#' + (e.detail?.task || ''))}</b>`
        );
      } catch (err) {
        setInbox(prev);
        pushToast(`Reject failed — ${err.message}`, 'red', '✕');
      }
    },
    schedule: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Scheduled for 09:00 tomorrow', 'cyan', '✓');
    },
    // Real task actions (mock-safe: PX.api.* return mock {ok:true} offline).
    // retry → PUT /api/tasks/{id}/status {status:'pending'} (the flow re-claims
    // it; also clears a poisoned LangGraph checkpoint server-side). cancel →
    // DELETE /api/tasks/{id}. Optimistic pipeline update; roll a red toast on error.
    retry: async (e) => {
      const id = e.detail?.task || e.id;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.retryTask(id);
        setPipeline((p) => ({
          ...p,
          tasks: (p.tasks || []).map((t) =>
            t.id === id ? { ...t, status: 'run' } : t
          ),
        }));
        pushToast(`Task #${id} re-queued from failed stage`, 'cyan', '↻');
        pushFeed(['cyan', 'PIPELINE'], `operator retried <b>#${id}</b>`);
      } catch (err) {
        pushToast(`Retry failed — ${err.message}`, 'red', '✕');
      }
    },
    kill: async (e) => {
      const id = e.detail?.task || e.id;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.killTask(id);
        setPipeline((p) => ({
          ...p,
          tasks: (p.tasks || []).filter((t) => t.id !== id),
        }));
        pushToast(`Task #${id} cancelled`, 'red', '✕');
        pushFeed(['red', 'PIPELINE'], `operator cancelled <b>#${id}</b>`);
      } catch (err) {
        pushToast(`Cancel failed — ${err.message}`, 'red', '✕');
      }
    },
    skipStage: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Stage skipped, advancing task', 'amber', '⚠');
    },
    ack: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Alert acknowledged', 'cyan', '✓');
      pushFeed(
        ['cyan', 'ALERT'],
        `operator acked <b>${e.detail?.probe || e.title}</b>`
      );
    },
    snooze: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Snoozed 1 hour', 'cyan', '✓');
    },
    fix: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Fix applied — re-probing surface', 'mint', '✓');
      pushFeed(
        ['mint', 'REMEDIATE'],
        `operator applied URL fix · <b>${e.detail?.surface || ''}</b>`
      );
      servicesR.mutate((s) =>
        s.map((x) =>
          x.name === 'prefect-server'
            ? { ...x, status: 'ok', metric: 'starting…' }
            : x
        )
      );
    },
    runProbe: (e) => {
      closeDrawer();
      pushToast('Probe re-running…', 'cyan', '◐');
      pushFeed(
        ['cyan', 'PROBE'],
        `operator triggered <b>${e.detail?.probe || 'probe'}</b>`
      );
    },
    // Real round-trip via the operator-triggered restart intent queue
    // (poindexter#909) — the worker has no docker.sock, so this queues a
    // row brain's own poll loop claims + executes, then reflects the
    // ACTUAL outcome (never an assumed one). host:true rows (ollama) have
    // no container to restart at all — say so instead of pretending.
    restart: async (s) => {
      closeDrawer();
      if (s.host || !s.container) {
        pushToast(
          `${s.name} runs on the host, not in docker — restart it manually`,
          'amber',
          '⚠'
        );
        return;
      }
      pushToast(`Restarting ${s.name}…`, 'cyan', '↻');
      servicesR.mutate((arr) =>
        arr.map((x) =>
          x.name === s.name ? { ...x, metric: 'restarting…' } : x
        )
      );
      try {
        const row = await PX.api.restartService(s.container);
        if (row && row.status === 'done') {
          pushToast(`${s.name} restarted`, 'mint', '✓');
          pushFeed(
            ['mint', 'REMEDIATE'],
            `operator restarted <b>${s.name}</b>`
          );
        } else if (row && row.status === 'failed') {
          pushToast(
            `${s.name} restart failed — ${row.detail || 'see logs'}`,
            'red',
            '✕'
          );
          pushFeed(['red', 'REMEDIATE'], `restart FAILED for <b>${s.name}</b>`);
        } else {
          // Queued but not confirmed within the poll window — honest
          // uncertainty, not a fabricated success (the next Service Health
          // poll settles the real status either way).
          pushToast(
            `${s.name} restart still in progress — check Service Health shortly`,
            'amber',
            '◐'
          );
        }
      } catch (err) {
        pushToast(`Restart failed — ${err.message}`, 'red', '✕');
      }
    },
    probe: (s) => {
      closeDrawer();
      pushToast(`Probing ${s.name}…`, 'cyan', '◐');
    },
    logs: (s) => {
      pushToast(`Streaming ${s.name} logs`, 'cyan', '▤');
    },
    embed: () => {
      closeDrawer();
      pushToast('Embed cycle triggered', 'cyan', '⚡');
      pushFeed(
        ['cyan', 'BRAIN'],
        `operator triggered <b>embed cycle</b> · 24 queued`
      );
    },
    runPipeline: () => {
      closeDrawer();
      pushToast('Pipeline run triggered', 'cyan', '▶');
      pushFeed(['cyan', 'PIPELINE'], `operator triggered <b>pipeline run</b>`);
    },
    openPrefect: () => pushToast('Opening Prefect UI…', 'cyan', '↗'),
    editBudget: () =>
      pushToast('Budget editing — coming in detail view', 'cyan', '✎'),
    openLemon: () => pushToast('Opening Lemon Squeezy dashboard…', 'cyan', '↗'),
    // Gate-2 decide (POST /api/media-approval/{post_id}/{medium}/decide).
    // Optimistic queue removal + gate2Pending decrement, rolled back on failure.
    // approved=true clears the asset for dispatch; reject sends it to regenerate.
    mediaApprove: async (it) => {
      const prev = media;
      mediaR.mutate((m) => ({
        ...m,
        queue: (m.queue || []).filter((q) => q.id !== it.id),
        gate2Pending: Math.max(0, (m.gate2Pending || 0) - 1),
      }));
      closeDrawer();
      try {
        await PX.api.mediaDecide(it.post_id || it.id, it.medium, true);
        pushToast(
          `${it.title ? trunc(it.title) : 'Media'} approved · cleared for dispatch`,
          'mint',
          '✓'
        );
        pushFeed(
          ['mint', 'MEDIA'],
          `operator approved <b>${it.medium || 'media'}</b> · ${trunc(it.title || '', 36)}`
        );
      } catch (err) {
        mediaR.mutate(() => prev);
        pushToast(`Media approve failed — ${err.message}`, 'red', '✕');
      }
    },
    mediaReject: async (it) => {
      const prev = media;
      mediaR.mutate((m) => ({
        ...m,
        queue: (m.queue || []).filter((q) => q.id !== it.id),
        gate2Pending: Math.max(0, (m.gate2Pending || 0) - 1),
      }));
      closeDrawer();
      try {
        await PX.api.mediaDecide(it.post_id || it.id, it.medium, false);
        pushToast(
          `${it.medium || 'Media'} rejected — will regenerate`,
          'amber',
          '⚠'
        );
        pushFeed(
          ['amber', 'MEDIA'],
          `operator rejected <b>${it.medium || 'media'}</b> · ${trunc(it.title || '', 36)}`
        );
      } catch (err) {
        mediaR.mutate(() => prev);
        pushToast(`Media reject failed — ${err.message}`, 'red', '✕');
      }
    },
    // Graph approval gates (kind='gate' — POST /api/gates/pending/{id}/…).
    // Approve records the approval and the server resumes the paused graph
    // from its checkpoint in the background (202). Optimistic remove; a
    // FAILED resume rolls the approval back server-side, so the row honestly
    // reappears on the next 60s gatesPending poll — no client bookkeeping.
    gateApprove: async (e) => {
      const prev = inbox;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.gateApprove(e.id);
        pushToast(
          `Approved — resuming past ${e.detail?.gate_name || 'gate'}`,
          'mint',
          '✓'
        );
        pushFeed(
          ['mint', 'GATE'],
          `operator approved <b>${escHtml(trunc(e.title))}</b> → pipeline resuming`
        );
      } catch (err) {
        setInbox(prev);
        pushToast(`Gate approve failed — ${err.message}`, 'red', '✕');
      }
    },
    gateReject: async (e) => {
      const prev = inbox;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.gateReject(e.id, e.detail?.feedback || '');
        pushToast(`Rejected — run dismissed`, 'amber', '⚠');
        pushFeed(
          ['amber', 'GATE'],
          `operator rejected <b>${escHtml(trunc(e.title))}</b> at ${e.detail?.gate_name || 'gate'}`
        );
      } catch (err) {
        setInbox(prev);
        pushToast(`Gate reject failed — ${err.message}`, 'red', '✕');
      }
    },
    // Reschedule a scheduled post by a duration (PATCH /api/scheduling/shift).
    // Optimistic: shift the row's published_at locally, roll back on failure.
    scheduleShift: async (postId, byDelta) => {
      const prev = schedule;
      const neg = byDelta.trim().startsWith('-');
      const mm = byDelta.match(/(\d+)\s*hour/i);
      const dMs = (mm ? parseInt(mm[1], 10) : 0) * 3600000 * (neg ? -1 : 1);
      scheduleR.mutate((s) => ({
        ...s,
        rows: (s.rows || []).map((r) =>
          r.post_id === postId
            ? {
                ...r,
                published_at: new Date(
                  new Date(r.published_at).getTime() + dMs
                ).toISOString(),
              }
            : r
        ),
      }));
      try {
        await PX.api.scheduleShift(byDelta, [postId]);
        pushToast(`Slot shifted ${byDelta}`, 'cyan', '↻');
      } catch (err) {
        scheduleR.mutate(() => prev);
        pushToast(`Reschedule failed — ${err.message}`, 'red', '✕');
      }
    },
    // ── Topics triage ─────────────────────────────────────
    // Pick a winner (operator_rank #1), resolve (advance winner → pipeline),
    // or reject (discard the batch). Optimistic with honest red-toast
    // rollback. Resolve requires a prior pick — the backend 400s an unranked
    // resolve and we surface that message verbatim.
    topicPick: async (b, c) => {
      const rest = (b.candidates || [])
        .filter((x) => x.id !== c.id)
        .map((x) => x.id);
      const ordered = [c.id, ...rest];
      const prev = topics;
      topicsR.mutate((t) => reRankBatch(t, b.batch_id, ordered));
      try {
        await PX.api.rankTopicBatch(b.batch_id, ordered);
        pushToast(
          `Picked “${trunc(c.operator_edited_topic || c.title)}” as winner`,
          'cyan',
          '★'
        );
        pushFeed(
          ['cyan', 'TOPICS'],
          `operator ranked <b>${trunc(c.title)}</b> #1 · ${b.niche_slug || ''}`
        );
      } catch (err) {
        topicsR.mutate(() => prev);
        pushToast(`Rank failed — ${err.message}`, 'red', '✕');
      }
    },
    topicResolve: async (b) => {
      const prev = topics;
      topicsR.mutate((t) => removeBatch(t, b.batch_id));
      try {
        await PX.api.resolveTopicBatch(b.batch_id);
        pushToast('Batch resolved — winner queued to pipeline', 'mint', '✓');
        pushFeed(
          ['mint', 'TOPICS'],
          `operator resolved batch <b>${String(b.batch_id).slice(0, 8)}</b> → pipeline`
        );
      } catch (err) {
        topicsR.mutate(() => prev);
        pushToast(`Resolve failed — ${err.message}`, 'red', '✕');
      }
    },
    topicReject: async (b) => {
      const prev = topics;
      topicsR.mutate((t) => removeBatch(t, b.batch_id));
      try {
        await PX.api.rejectTopicBatch(b.batch_id, '');
        pushToast(
          'Batch rejected — niche freed for a fresh sweep',
          'amber',
          '⚠'
        );
        pushFeed(
          ['amber', 'TOPICS'],
          `operator rejected batch <b>${String(b.batch_id).slice(0, 8)}</b>`
        );
      } catch (err) {
        topicsR.mutate(() => prev);
        pushToast(`Reject failed — ${err.message}`, 'red', '✕');
      }
    },
    launch: (t) => {
      if (t && t.url && t.url !== '#') {
        window.open(t.url, '_blank', 'noopener,noreferrer');
        pushToast(`Opening ${t.name}…`, 'cyan', '↗');
      } else {
        pushToast(
          `${(t && t.name) || 'Tool'} has no URL configured`,
          'amber',
          '⚠'
        );
      }
    },
    // Open the REAL tap-to-join URL (operator config, fetched from
    // app_settings.voice_agent_public_join_url — never hardcoded). Honest
    // toast when it's unset rather than faking a connection.
    voice: async () => {
      let url = '';
      try {
        url = await PX.api.voiceJoinUrl();
      } catch (_e) {
        url = '';
      }
      if (url) {
        window.open(url, '_blank', 'noopener,noreferrer');
        pushToast('Opening Poindexter voice…', 'cyan', '🎙');
      } else {
        pushToast(
          'Voice not configured — set voice_agent_public_join_url',
          'amber',
          '🎙'
        );
      }
    },
    // Trigger a full static-export rebuild (POST /api/export/rebuild): re-export
    // every static JSON to the CDN + ISR-revalidate the live site.
    rebuild: async () => {
      pushToast('Rebuilding static export…', 'cyan', '⟳');
      try {
        await PX.api.rebuildExport();
        pushToast('Static export rebuilt — site refreshing', 'mint', '✓');
        pushFeed(
          ['mint', 'PUBLISH'],
          'operator triggered <b>static export rebuild</b>'
        );
      } catch (err) {
        pushToast(
          'Rebuild failed — ' + (err && err.message ? err.message : 'error'),
          'red',
          '✕'
        );
      }
    },

    // ── Social draft approve / reject ──────────────────────
    // Accepts either a raw draft object (from SocialPanel) or an inbox item
    // (from ActionInbox / FeedMode) — detects by presence of `detail.draft`.
    socialApproveDraft: async (itemOrDraft) => {
      const draft =
        (itemOrDraft.detail && itemOrDraft.detail.draft) || itemOrDraft;
      const prevSocial = social;
      setSocial((s) => ({
        ...s,
        drafts: (s.drafts || []).filter((x) => x.id !== draft.id),
      }));
      setInbox((prev) => prev.filter((i) => i.id !== draft.id));
      closeDrawer();
      try {
        await PX.api.socialDraftAction(draft.id, 'approve');
        pushToast(
          `${draft.platform} draft approved — queued for Postiz`,
          'mint',
          '✓'
        );
        pushFeed(
          ['mint', 'SOCIAL'],
          `operator approved <b>${escHtml(draft.platform)}</b> draft · enqueued`
        );
      } catch (err) {
        setSocial(prevSocial);
        setInbox((prev) => [...prev, draftToInbox(draft)]);
        pushToast(`Social approve failed — ${err.message}`, 'red', '✕');
      }
    },
    socialRejectDraft: async (itemOrDraft) => {
      const draft =
        (itemOrDraft.detail && itemOrDraft.detail.draft) || itemOrDraft;
      const prevSocial = social;
      setSocial((s) => ({
        ...s,
        drafts: (s.drafts || []).filter((x) => x.id !== draft.id),
      }));
      setInbox((prev) => prev.filter((i) => i.id !== draft.id));
      closeDrawer();
      try {
        await PX.api.socialDraftAction(draft.id, 'reject');
        pushToast(`${draft.platform} draft rejected`, 'amber', '⚠');
        pushFeed(
          ['amber', 'SOCIAL'],
          `operator rejected <b>${escHtml(draft.platform)}</b> draft`
        );
      } catch (err) {
        setSocial(prevSocial);
        setInbox((prev) => [...prev, draftToInbox(draft)]);
        pushToast(`Social reject failed — ${err.message}`, 'red', '✕');
      }
    },
  };

  const open = (type, data, extra) => setEntity({ type, data, ...extra });

  // ── Command palette commands (built from live state) ──────────────────
  const commands = useMemo(() => {
    const cmds = [];
    inbox
      .filter((i) => i.kind === 'approve')
      .forEach((i) => {
        cmds.push({
          id: 'apr-' + i.id,
          group: 'Approve',
          icon: 'check',
          label: 'Approve & publish — ' + trunc(i.title, 44),
          hint: 'Q' + (i.detail?.quality || ''),
          run: () => A.approve(i),
        });
      });
    inbox
      .filter((i) => i.kind === 'fail')
      .forEach((i) => {
        cmds.push({
          id: 'rty-' + i.id,
          group: 'Pipeline',
          icon: 'retry',
          label: 'Retry — ' + trunc(i.title, 44),
          hint: 'failed',
          run: () => A.retry(i),
        });
        cmds.push({
          id: 'kill-' + i.id,
          group: 'Pipeline',
          icon: 'kill',
          label: 'Kill — ' + trunc(i.title, 40),
          danger: true,
          run: () => A.kill(i),
        });
      });
    inbox
      .filter((i) => i.kind === 'alert')
      .forEach((i) => {
        cmds.push({
          id: 'ack-' + i.id,
          group: 'Alerts',
          icon: 'check',
          label: 'Acknowledge — ' + trunc(i.title, 42),
          run: () => A.ack(i),
        });
      });
    inbox
      .filter((i) => i.kind === 'drift')
      .forEach((i) => {
        cmds.push({
          id: 'fix-' + i.id,
          group: 'Alerts',
          icon: 'bolt',
          label: 'Apply fix — ' + trunc(i.title, 42),
          run: () => A.fix(i),
        });
      });
    services.forEach((s) => {
      cmds.push({
        id: 'rs-' + s.name,
        group: 'Services',
        icon: 'retry',
        label: 'Restart ' + s.name,
        hint:
          s.status === 'err'
            ? 'down'
            : s.status === 'warn'
              ? 'degraded'
              : 'healthy',
        danger: s.status === 'err',
        run: () => A.restart(s),
      });
    });
    cmds.push({
      id: 'embed',
      group: 'Run',
      icon: 'bolt',
      label: 'Trigger embed cycle',
      run: A.embed,
    });
    cmds.push({
      id: 'pipe',
      group: 'Run',
      icon: 'play',
      label: 'Trigger pipeline run',
      run: A.runPipeline,
    });
    [
      ['console', 'Console overview', 'overview'],
      ['trace', 'Task trace + feed', null],
      ['map', 'System map', null],
      ['wall', 'Wall display', null],
    ].forEach(([m, lbl]) => {
      cmds.push({
        id: 'view-' + m,
        group: 'Go to',
        icon:
          m === 'map'
            ? 'gpu'
            : m === 'wall'
              ? 'overview'
              : m === 'trace'
                ? 'pulse'
                : 'overview',
        label: lbl,
        hint: 'view',
        run: () => setMode(m),
      });
    });
    RAIL.forEach((r) =>
      cmds.push({
        id: 'nav-' + r.id,
        group: 'Go to',
        icon: r.icon,
        label: r.label,
        hint: 'section',
        run: () => goTo(r.id),
      })
    );
    cmds.push({
      id: 'set',
      group: 'Go to',
      icon: 'settings',
      label: 'App settings',
      hint: 'config',
      run: () => setMode('settings'),
    });
    PX.launcher.forEach((t) =>
      cmds.push({
        id: 'open-' + t.name,
        group: 'Launch',
        icon: 'link',
        label: 'Open ' + t.name,
        hint: t.sub,
        run: () => A.launch(t),
      })
    );
    cmds.push({
      id: 'voice',
      group: 'Launch',
      icon: 'play',
      label: 'Talk to Poindexter (voice)',
      hint: 'livekit',
      run: () => A.voice(),
    });
    cmds.push({
      id: 'rebuild',
      group: 'Actions',
      icon: 'refresh',
      label: 'Rebuild static export',
      hint: 'publish',
      run: () => A.rebuild(),
    });
    return cmds;
  }, [inbox, services]);

  const sysState = useMemo(() => {
    if (services.some((s) => s.status === 'err'))
      return [
        'err',
        `${services.filter((s) => s.status === 'err').length} SERVICE DOWN`,
      ];
    const open = inbox.length;
    if (open > 0) return ['warn', `${open} NEED ATTENTION`];
    return ['ok', 'ALL SYSTEMS NOMINAL'];
  }, [services, inbox]);

  // Overview KPI strip. Mock: the static PX.kpis. Live: project the real reads
  // onto the strip via the pure mapper — spend from the SAME budget()-loaded
  // `cost` the Cost panel renders (so the two can't disagree), awaiting-approval
  // from the live `inbox`, published/quality/traffic/failed from the kpiReads
  // effect, and an honest '—' for anything whose read failed
  // (kpis.js / feedback_no_dummy_data).
  const kpis = useMemo(() => {
    if (!PX.api.isLive()) return PX.kpis;
    const pendingApproval = inbox.filter((i) => i.kind === 'approve').length;
    return PX.kpisFromLive(
      PX.kpis,
      {
        cost,
        pendingApproval,
        posts: kpiReads.posts,
        views: kpiReads.views,
        failedTasks: kpiReads.failedTasks,
      },
      Date.now()
    );
  }, [cost, inbox, kpiReads]);

  return (
    <div className={`app gl-atmosphere mode-${mode}`}>
      <ConnectionBanner />
      {/* Rail */}
      <nav className="rail">
        <div className="rail__logo" title="Poindexter">
          P
        </div>
        {RAIL.map((r) => {
          const count =
            r.id === 'overview'
              ? inbox.length
              : r.id === 'services'
                ? services.filter((s) => s.status === 'err').length
                : r.id === 'topics'
                  ? ((topics && topics.items) || []).length
                  : r.id === 'social'
                    ? // Server-sent count over every row, not the returned
                      // page — a badge derived from a capped list undercounts.
                      // Falls back to deriving in mock mode (no status_counts).
                      (((social && social.status_counts) || {}).pending ??
                      ((social && social.drafts) || []).filter(
                        (d) => d.status === 'pending'
                      ).length)
                    : 0;
          return (
            <button
              key={r.id}
              className={`rail__btn ${active === r.id ? 'is-active' : ''}`}
              title={r.label}
              onClick={() => goTo(r.id)}
            >
              <Icon name={r.icon} size={19} />
              {count > 0 && <span className="rail__count">{count}</span>}
              <span className="rail__label">{r.label}</span>
            </button>
          );
        })}
        <span className="rail__spacer" />
        <button
          className={`rail__btn ${mode === 'settings' ? 'is-active' : ''}`}
          title="App settings"
          onClick={() => setMode('settings')}
        >
          <Icon name="settings" size={19} />
          <span className="rail__label">App Settings</span>
        </button>
      </nav>

      {/* Topbar */}
      <header className="topbar">
        <div className="topbar__title">
          <span className="topbar__eyebrow">
            {`// GLAD LABS · POINDEXTER V${POINDEXTER_VERSION}`}
          </span>
          <span className="topbar__crumb">
            OPERATOR <em>CONSOLE</em>
          </span>
        </div>
        <span className="topbar__spacer" />
        <div className="modeswitch">
          {[
            ['console', 'overview', 'Console'],
            ['trace', 'pulse', 'Trace'],
            ['map', 'gpu', 'Map'],
            ['wall', 'overview', 'Wall'],
          ].map(([m, ic, lbl]) => (
            <button
              key={m}
              className={
                mode === m || (m === 'trace' && mode === 'tracedetail')
                  ? 'is-active'
                  : ''
              }
              onClick={() => setMode(m)}
              title={lbl}
            >
              <Icon name={ic} size={13} />
              <span className="lbl">{lbl}</span>
            </button>
          ))}
        </div>
        <button className="kbd-hint" onClick={() => setPaletteOpen(true)}>
          <Icon name="search" size={12} />
          Command<kbd>⌘K</kbd>
        </button>
        <button
          className={`sys-chip sys-chip--${sysState[0]}`}
          onClick={() => {
            setMode('console');
            goTo('overview');
          }}
        >
          <span className="dot" />
          {sysState[1]}
        </button>
        <div className="topbar__meta">
          <span>
            <span className="k">SYNC</span>{' '}
            <span
              className="live-dot"
              style={{
                verticalAlign: 'middle',
                opacity: window.PXR.isDisconnected(
                  window.PXR.ConnectionState.getState()
                )
                  ? 0.3
                  : 1,
              }}
            />{' '}
            {health && health.lastUpdatedAt
              ? window.agoLabel(health.lastUpdatedAt)
              : '—'}
          </span>
          <span className="tnum">{clock}</span>
        </div>
      </header>

      {/* Main */}
      <main className="main" ref={mainRef}>
        {mode === 'console' && (
          <div className="main__inner">
            <div id="sec-overview">
              {/* SYSTEM PULSE band — In Production / Background / Just Happened,
                  fed by the /api/activity ledger (Phase 1). Truly-running work
                  comes from the ledger, so terminal tasks never read as "in
                  flight" (the taskStatusKind over-mapping the mock band had). */}
              <NowRunningBand
                activity={activity}
                onOpenTask={(t) => open('task', t)}
              />
              {/* Live in live mode (kpis memo → PX.kpisFromLive); PX.kpis in mock. */}
              <KpiStrip kpis={kpis} onOpen={(k) => open('kpi', k)} />
            </div>

            <div className="masonry masonry--overview">
              <div id="sec-overview-inbox">
                <ActionInbox
                  items={inbox}
                  filter={filter}
                  setFilter={setFilter}
                  onOpen={(it) => open('inbox', it)}
                  onApprove={A.approve}
                  onReject={A.reject}
                  onRejectNotes={(it) =>
                    open('inbox', it, { openReject: true })
                  }
                  onRetry={A.retry}
                  onAck={A.ack}
                  onFix={A.fix}
                  onSocialApprove={(it) => A.socialApproveDraft(it)}
                  onSocialReject={(it) => A.socialRejectDraft(it)}
                  onMediaApprove={(it) => A.mediaApprove(it.detail)}
                  onMediaReject={(it) => A.mediaReject(it.detail)}
                  onGateApprove={(it) => A.gateApprove(it)}
                  onGateReject={(it) => A.gateReject(it)}
                />
              </div>
              {approved.length > 0 && (
                <div id="sec-publish">
                  <PublishQueue items={approved} onPublish={A.publish} />
                </div>
              )}
              <div id="sec-schedule">
                <SchedulePanel
                  schedule={schedule}
                  onShift={A.scheduleShift}
                  fresh={scheduleR}
                />
              </div>
              <div id="sec-services">
                <ServiceGrid
                  services={services}
                  onOpen={(s) => open('service', s)}
                  onRestart={A.restart}
                  fresh={servicesR}
                />
              </div>
              <div id="sec-pipeline">
                <PipelinePanel
                  pipeline={pipeline}
                  onOpen={() => open('pipeline', pipeline)}
                  onOpenTask={(t) => open('task', t)}
                  onRetry={A.retry}
                />
              </div>
              <div id="sec-topics">
                <TopicsPanel
                  topics={topics}
                  onPick={A.topicPick}
                  onResolve={A.topicResolve}
                  onReject={A.topicReject}
                  fresh={topicsR}
                />
              </div>
              <div id="sec-social">
                <SocialPanel
                  social={social}
                  onApprove={A.socialApproveDraft}
                  onReject={A.socialRejectDraft}
                />
              </div>
              <div id="sec-newsletter">
                <NewsletterPanel newsletter={newsletter} fresh={newsletterR} />
              </div>
              <div id="sec-gpu">
                <GpuHud
                  gpu={gpu}
                  queue={gpuQueue}
                  onOpen={() => open('gpu', gpu)}
                />
              </div>
              <div id="sec-media">
                <MediaPanel
                  media={media}
                  onOpenItem={(it) =>
                    open('inbox', {
                      kind: 'media',
                      detail: { ...it, stage: 'gate_2_review' },
                    })
                  }
                  onApprove={A.mediaApprove}
                  onReject={A.mediaReject}
                  fresh={mediaR}
                />
              </div>
              <div id="sec-revenue">
                {/* Intentionally static (raw PX.revenue, no live effect):
                    pre-revenue, billing gated (project_monetization), and there
                    is no /api/revenue read. PX.revenue carries live:false so the
                    panel already renders an honest $0 / "billing not live yet" —
                    never a fabricated figure. Wire a live effect here when a
                    revenue route + a first real sale land. */}
                <RevenuePanel
                  revenue={PX.revenue}
                  onOpen={() => open('revenue', PX.revenue)}
                />
              </div>
              <div id="sec-brain">
                <BrainPanel
                  brain={brain}
                  onOpen={() => open('brain', brain)}
                  onEmbed={A.embed}
                />
              </div>
              <div id="sec-qa">
                {/* Intentionally static (raw PX.qa, no live effect): the rail
                    list IS the real current config (modules/content/atoms/qa_*.py
                    → qa.aggregate) and QAPanel already branches on isLive() for
                    its meta. Pass/reject rates have no console read surface;
                    graduating a rail is a qa_gates.<rail>.required_to_pass change,
                    not a console edit. Wire from qa_gates here if a read lands. */}
                <QAPanel qa={PX.qa} onOpen={() => open('qa', PX.qa)} />
              </div>
              <div id="sec-seo">
                <SeoPanel seo={seo} fresh={seoR} />
              </div>
              <div id="sec-cost">
                <CostPanel
                  cost={cost}
                  onOpen={() => open('cost', cost)}
                  fresh={costR}
                />
              </div>
              <div id="sec-launch">
                <LauncherPanel tools={PX.launcher} onVoice={A.voice} />
              </div>
              <div id="sec-audit">
                <AuditFeed
                  lines={feed}
                  onOpen={() =>
                    pushToast('Opening full audit log', 'cyan', '↗')
                  }
                />
              </div>
              <div id="sec-findings">
                <FindingsPanel
                  findings={findings}
                  onOpen={() => open('findings', findings)}
                  fresh={findingsR}
                />
              </div>
              {/* Each telemetry surface is its own masonry item. They used to
                  share one <div>, but a single 5-panel child is un-breakable
                  (break-inside: avoid) and ~5× taller than any other item, which
                  wrecks the column balancer (one lonely card in a column). The id
                  stays on the first for the Telemetry scroll-spy target. */}
              <div id="sec-telemetry">
                <HistoryPanel />
              </div>
              <div>
                <HardwarePanel />
              </div>
              <div>
                <DatabasePanel />
              </div>
              <div>
                <LogsPanel
                  logs={logs}
                  service={logFilter.service}
                  level={logFilter.level}
                  onFilter={(patch) =>
                    setLogFilter((f) => ({ ...f, ...patch }))
                  }
                  fresh={logsR}
                />
              </div>
              <div>
                <TracesPanel traces={traces} fresh={tracesR} />
              </div>
            </div>

            <footer
              style={{
                textAlign: 'center',
                padding: '22px 0 8px',
                fontFamily: 'var(--gl-font-mono)',
                fontSize: 10,
                letterSpacing: '.16em',
                color: 'var(--gl-text-dim)',
              }}
            >
              // POINDEXTER OPERATOR CONSOLE · LOCAL-FIRST · OLLAMA ONLY · NO
              PAID APIS
            </footer>
          </div>
        )}

        {mode === 'trace' && (
          <div className="main__inner">
            {/* The task-trace board IS this page now (renamed from Feed per
                Matt — the landing was too busy). The operations feed rides
                below it. A card opens the full-bleed deep-dive
                (mode='tracedetail'); back returns here. */}
            <div id="sec-trace">
              <TraceBoard
                data={traceR.data}
                fresh={traceR}
                onOpen={openTrace}
              />
            </div>
            <div style={{ marginTop: 20 }}>
              <FeedMode
                inbox={inbox}
                feed={feed}
                filter={feedFilter}
                setFilter={setFeedFilter}
                onOpen={(it) => open('inbox', it)}
                A={A}
              />
            </div>
          </div>
        )}

        {mode === 'map' && (
          <div style={{ padding: 16 }}>
            <SystemMap
              services={services}
              gpu={gpu}
              onOpen={(s) => open('service', s)}
              onOpenGpu={() => open('gpu', gpu)}
              onRestart={A.restart}
            />
          </div>
        )}

        {mode === 'tracedetail' && (
          <div style={{ padding: 16 }}>
            <TraceDeepDive
              taskId={traceTaskId}
              onBack={backFromTrace}
              onAction={traceAction}
              onLangfuse={traceLangfuse}
            />
          </div>
        )}

        {mode === 'wall' && (
          // Wall is an ambient/TV view kept on the static PX.kpis: it has its
          // own hardcoded scaffolding (date, deltas, "of $50 budget") and does a
          // numeric .toFixed on the spend value that the live honest-empty '—'
          // would throw on. The live, action-first strip is console mode above;
          // wiring Wall to live is a separate, larger change.
          <WallDisplay
            kpis={PX.kpis}
            gpu={gpu}
            pipeline={pipeline}
            brain={brain}
            services={services}
            inbox={inbox}
            clock={clock}
            sysState={sysState}
            revenue={PX.revenue}
          />
        )}

        {mode === 'settings' && (
          <SettingsMode
            onApply={(changes, ok, errMsg) => {
              if (!ok) {
                pushToast(`Save failed — ${errMsg || 'API error'}`, 'red', '✕');
                return;
              }
              pushToast(
                `Applied ${changes.length} setting${changes.length > 1 ? 's' : ''} · brain re-reads ≤ 5m`,
                'mint',
                '✓'
              );
              changes.forEach((c) =>
                pushFeed(
                  ['cyan', 'CONFIG'],
                  `operator set <span class="c-cyan">${c.key}</span> <span class="c-dim">${c.from}</span> → <b>${c.to}</b>`
                )
              );
            }}
            pushFeed={pushFeed}
          />
        )}
      </main>

      <CommandPalette
        open={paletteOpen}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
      />

      <Drawer entity={entity} onClose={closeDrawer} actions={A} />
      {toastNode}
    </div>
  );
}

// Minutes since an ISO timestamp (for relative-age display).
function minsSince(iso) {
  return Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
}

// HTML-escape a value before embedding in dangerouslySetInnerHTML feed lines.
function escHtml(s) {
  return String(s == null ? '' : s).replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ]
  );
}

// Map a social_post_drafts row → Action Inbox item shape (kind='social').
// draft.id is the draft_id; the inbox item carries detail.draft for the action
// handlers to unpack. Pending drafts only (caller filters before calling).
function draftToInbox(d) {
  const PX = window.PX;
  return {
    id: d.id,
    kind: 'social',
    priority: 2,
    title: `${d.platform}: ${trunc(d.content || '', 60)}`,
    sub: [
      ['PLATFORM', d.platform || '—'],
      ['POST', d.post_id ? String(d.post_id).slice(0, 8) : '—'],
      ['RETRIES', String(d.retry_count || 0)],
    ],
    age: d.created_at ? PX.ago(minsSince(d.created_at)) : '',
    tags: [['amber', 'SOCIAL']],
    detail: { draft: d },
  };
}

// Map a MediaPanel queue row (already shaped by PX.api.mediaQueue()) → the
// Action Inbox item shape (kind='media'). Mirrors draftToInbox — `detail`
// carries the raw queue row (id/post_id/medium/title/…) plus `stage`, the
// same shape MediaPanel's own onOpenItem hands the drawer, so one drawer
// branch serves both entry points.
const MEDIA_INBOX_TAG = {
  video: 'cyan',
  podcast: 'amber',
  video_short: 'mint',
};
function mediaToInbox(m) {
  return {
    id: m.id,
    kind: 'media',
    priority: 2,
    title: m.title,
    sub: [
      ['QUALITY', m.quality != null ? String(m.quality) : '—'],
      ['SLUG', m.slug || '—'],
    ],
    age: m.age || '',
    tags: [
      [
        MEDIA_INBOX_TAG[m.medium] || 'cyan',
        (m.medium || 'media').toUpperCase(),
      ],
    ],
    detail: { ...m, stage: 'gate_2_review' },
  };
}

// Map a /api/gates/pending row → Action Inbox item shape (kind='gate').
// `g.artifact` is the operator-review payload the approval_gate atom captured
// (gate_artifact_keys) — for seo_refresh_gate: title, post_slug, and the
// PROPOSED seo_title/seo_description under review. detail carries the whole
// row for the drawer branch.
function gateToInbox(g) {
  const PX = window.PX;
  const a = g.artifact || {};
  return {
    id: g.task_id,
    kind: 'gate',
    priority: 1,
    title:
      a.title || g.title || g.topic || `Task ${String(g.task_id).slice(0, 8)}`,
    sub: [
      ['GATE', g.gate_name || '—'],
      ['QUERY', a.target_query || '—'],
      ['TASK', String(g.task_id).slice(0, 8)],
    ],
    age: g.gate_paused_at ? PX.ago(minsSince(g.gate_paused_at)) : '',
    tags: [['amber', 'PAUSED']],
    detail: { ...g, artifact: a, task: g.task_id },
  };
}

// Map a /api/tasks/pending-approval row → the Action Inbox item shape.
function approvalToInbox(t) {
  const PX = window.PX;
  // Rendered-preview link: every awaiting-approval task mints a preview_token
  // (verify_task → content.persist_task) and the worker serves the rendered
  // draft at /preview/{token} (cms_routes) — the same link the Discord
  // notification carries. The console is served same-origin by that worker,
  // so a relative href works on localhost and over the tailnet alike.
  const previewToken = (t.metadata && t.metadata.preview_token) || '';
  const previewUrl = /^[a-f0-9]{32}$/.test(previewToken)
    ? `/preview/${previewToken}`
    : null;
  return {
    id: t.task_id,
    kind: 'approve',
    priority: 1,
    title: t.task_name || t.topic || `Task ${t.task_id}`,
    sub: [
      [
        'QUALITY',
        t.quality_score != null ? String(Math.round(t.quality_score)) : '—',
      ],
      ['TYPE', t.task_type || 'blog_post'],
      ['TOPIC', t.topic || '—'],
    ],
    age: t.created_at ? PX.ago(minsSince(t.created_at)) : '',
    tags: [['cyan', 'READY']],
    detail: {
      excerpt: t.content_preview || '',
      quality: t.quality_score,
      pipeline: t.status,
      topic: t.topic,
      featured_image_url: t.featured_image_url,
      task: t.task_id,
      preview_url: previewUrl,
    },
  };
}

// Map a /api/tasks UnifiedTaskResponse status → the panel's StatusText kind.
// TASK_STATUS only knows ok|run|fail, so collapse the richer API states.
function taskStatusKind(status) {
  const s = (status || '').toLowerCase();
  if (['failed', 'fail', 'error', 'cancelled', 'canceled'].includes(s))
    return 'fail';
  if (
    [
      'completed',
      'complete',
      'approved',
      'published',
      'awaiting_approval',
    ].includes(s)
  )
    return 'ok';
  return 'run'; // pending / queued / generating / running / in_progress …
}

// Map a /api/tasks row → the Pipeline panel task shape. `stage` is the real
// current graph_def node when the API exposes it (else the status/publish bucket).
function taskToRow(t) {
  const PX = window.PX;
  const id = t.id || t.task_id;
  return {
    id,
    topic: t.topic || t.task_name || `Task ${id}`,
    stage: t.stage || t.publish_status || t.status || '—',
    status: taskStatusKind(t.status),
    quality: t.quality_score != null ? Math.round(t.quality_score) : null,
    model: t.model_used || '—',
    age: t.created_at ? PX.ago(minsSince(t.created_at)) : '',
    _raw: t,
  };
}

// Rebuild a pipeline object from live rows: swap in the real task list and
// recount each block by how many ACTIVE tasks sit at one of its nodes. Terminal
// tasks (ok/fail) don't inflate "in-flight" block counts — honest empties result.
function withLiveCounts(p, rows) {
  const nodeToBlock = {};
  (p.stages || []).forEach((b) =>
    (b.nodes || []).forEach((n) => (nodeToBlock[n] = b.name))
  );
  const counts = {};
  let ok = 0,
    terminal = 0;
  rows.forEach((r) => {
    if (r.status === 'ok' || r.status === 'fail') {
      terminal++;
      if (r.status === 'ok') ok++;
    }
    if (r.status !== 'run') return; // only active tasks occupy a block
    const block = nodeToBlock[r.stage];
    if (block) counts[block] = (counts[block] || 0) + 1;
  });
  return {
    ...p,
    tasks: rows,
    // Honest live meta: success rate over the loaded terminal tasks. Average
    // completion isn't derivable from the list endpoint, so it reads unknown
    // rather than carrying the mock value into a live view.
    successRate: terminal ? Math.round((100 * ok) / terminal) : '—',
    avgCompletion: '—',
    stages: (p.stages || []).map((b) => ({
      ...b,
      count: counts[b.name] || 0,
      state: counts[b.name] ? 'hot' : '',
    })),
  };
}

// Topic-batch optimistic-update helpers (pure). removeBatch drops a resolved/
// rejected batch from the open list; reRankBatch stamps operator_rank by
// 1-based position so the Picked winner shows as #1 immediately, before the
// server round-trip lands (rolled back on error by the caller).
function removeBatch(topics, batchId) {
  const items = ((topics && topics.items) || []).filter(
    (b) => b.batch_id !== batchId
  );
  // Canonical offset envelope (poindexter#745): unpaginated → limit == len.
  return { items, total: items.length, limit: items.length, offset: 0 };
}
function reRankBatch(topics, batchId, orderedIds) {
  const rankById = {};
  orderedIds.forEach((id, i) => (rankById[id] = i + 1));
  const items = ((topics && topics.items) || []).map((b) => {
    if (b.batch_id !== batchId) return b;
    return {
      ...b,
      candidates: (b.candidates || []).map((c) => ({
        ...c,
        operator_rank:
          rankById[c.id] != null ? rankById[c.id] : c.operator_rank,
      })),
    };
  });
  return { ...topics, items };
}

// Ready-to-publish list — staged (approved) tasks awaiting the publish gate.
// Renders nothing when empty, so mock mode and an empty live queue show nothing
// (no fabricated rows). Accepts both server rows (task_name/quality_score) and
// the optimistic row pushed on approve (title/quality).
function PublishQueue({ items, onPublish }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="panel">
      <div className="panel__head">
        <span className="panel__title">
          <Icon name="check" size={14} className="panel__ico" />
          READY TO PUBLISH
        </span>
        <span className="panel__spacer" style={{ flex: 1 }} />
        <span className="panel__meta">{items.length} staged</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.map((t) => {
          const title = t.title || t.task_name || t.topic || t.id;
          const q = t.quality != null ? t.quality : t.quality_score;
          return (
            <div
              key={t.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 10px',
                border: '1px solid var(--gl-line, rgba(255,255,255,0.1))',
                borderRadius: 2,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontFamily: 'var(--gl-font-mono)',
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {trunc(title, 52)}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    letterSpacing: '.12em',
                    color: 'var(--gl-text-dim)',
                  }}
                >
                  {q != null ? `Q${Math.round(q)} · ` : ''}APPROVED · AWAITING
                  PUBLISH
                </div>
              </div>
              <button
                className="mbtn mbtn--primary"
                onClick={() =>
                  onPublish({
                    id: t.id,
                    title: t.title || t.task_name || t.topic || t.id,
                  })
                }
              >
                <Icon name="play" size={12} />
                Publish
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function trunc(s, n = 40) {
  return s && s.length > n ? s.slice(0, n - 1) + '…' : s;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
