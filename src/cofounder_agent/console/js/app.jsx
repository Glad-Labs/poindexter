/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — app shell + orchestration.
   ────────────────────────────────────────────────────────────── */
const { useState: useS, useEffect: useE, useRef: useR, useMemo } = React;

// Poindexter version shown in the topbar eyebrow. release-please bumps the
// literal below on every release (see the `generic` extra-files entry in
// release-please-config.json) so the console tracks the real build instead of
// drifting. Keep the `// x-release-please-version` annotation on this line.
const POINDEXTER_VERSION = '0.74.0'; // x-release-please-version

const RAIL = [
  { id: 'overview', icon: 'overview', label: 'Overview' },
  { id: 'pipeline', icon: 'pipeline', label: 'Pipeline' },
  { id: 'topics', icon: 'overview', label: 'Topics' },
  { id: 'brain', icon: 'brain', label: 'Brain' },
  { id: 'gpu', icon: 'gpu', label: 'GPU' },
  { id: 'services', icon: 'services', label: 'Services' },
  { id: 'audit', icon: 'audit', label: 'Audit' },
  { id: 'findings', icon: 'bell', label: 'Findings' },
  { id: 'cost', icon: 'cost', label: 'Cost' },
  { id: 'revenue', icon: 'pulse', label: 'Revenue' },
];

function App() {
  const PX = window.PX;
  const [inbox, setInbox] = useS(PX.inbox);
  const [approved, setApproved] = useS([]); // live: staged tasks, awaiting publish
  const [services, setServices] = useS(PX.services);
  const [gpu, setGpu] = useS(PX.gpu);
  const [pipeline, setPipeline] = useS(PX.pipeline); // live: real /api/tasks
  const [topics, setTopics] = useS(PX.topics); // live: GET /api/topics/proposals
  const [cost, setCost] = useS(PX.cost); // live: GET /api/metrics/costs/budget
  const [findings, setFindings] = useS(PX.findings); // live: GET /api/findings
  const [brain, setBrain] = useS(PX.brain); // live: GET /api/memory/stats
  const [media, setMedia] = useS(PX.media); // live: GET /api/media-approval/pending
  const [schedule, setSchedule] = useS(PX.schedule); // live: GET /api/scheduling
  const [seo, setSeo] = useS(PX.seo); // live: GET /api/seo
  // live: KPI-strip reads with no home panel — GET /api/posts (published 30d +
  // a real per-day histogram) + GET /api/analytics/views (page views 24h). Mock
  // keeps PX.kpis untouched (the `kpis` memo below short-circuits in mock mode).
  const [kpiReads, setKpiReads] = useS({ posts: null, views: null });
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
  const [paletteOpen, setPaletteOpen] = useS(false);
  const [clock, setClock] = useS('14:32:00');
  const [toastNode, pushToast] = useToasts();
  const mainRef = useR(null);
  const feedKey = useR(0);

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

  // ── Live: load open topic batches into the Topics panel ───
  // Surfaces what discovery proposed but hasn't been acted on — a stuck open
  // batch is the recurring "content goes dark" failure, so this is the drain
  // valve. Mock mode keeps PX.topics. Polls on the 5-min SYNC cadence.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.listTopicProposals();
        if (!alive) return;
        // Canonical offset envelope (poindexter#745): read `.items`, not `.batches`.
        setTopics(
          res && res.items ? res : { items: [], total: 0, limit: 0, offset: 0 }
        );
      } catch (e) {
        pushToast(`Topics load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: real service health from cAdvisor + worker /api/health ──
  // Replaces the mock liveness with derived container_last_seen status (plus
  // real cpu/mem/uptime/image). Faster cadence than the 5-min data polls — a
  // down container is the thing an operator wants to see immediately. cAdvisor
  // scrapes ~15s, so 30s is two scrapes of headroom. Mock mode keeps PX.services.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const rows = await PX.api.serviceHealth();
        if (!alive) return;
        if (Array.isArray(rows)) setServices(rows);
      } catch (e) {
        pushToast(`Service health load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 30 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: real LLM/API spend vs cap (budget) ──────────────
  // The one cost read with an HTTP surface (GET /api/metrics/costs/budget). The
  // by-model / daily breakdowns aren't routed, so live mode clears them to [] and
  // the panel/drawer render an explicit "backend read pending" — honest, never
  // mocked. $0 infra + energy are facts (not reads), so they stay as-is. Mock
  // mode keeps the full PX.cost. 5-min cadence (spend moves slowly).
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const b = await PX.api.budget();
        if (!alive || !b) return;
        setCost((c) => ({
          ...c,
          monthToDate: b.amount_spent ?? c.monthToDate,
          budget: b.monthly_budget ?? c.budget,
          projected: b.projected_final_cost ?? c.projected,
          dailyBurn: b.daily_burn_rate ?? c.dailyBurn,
          percentUsed: b.percent_used ?? c.percentUsed,
          status: b.status ?? c.status,
          alerts: b.alerts ?? [],
          byModel: [],
          daily: [],
          energyKwhMonth: null,
        }));
      } catch (e) {
        pushToast(`Budget load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: probe-findings triage (#461) ────────────────────
  // Mirrors the Findings dashboard — emitted/pending counts + per-finding
  // routing status. Read-only (the brain's findings_alert_router delivers them).
  // Mock mode keeps PX.findings. 5-min cadence.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.findings();
        if (!alive || !res) return;
        setFindings(res);
      } catch (e) {
        pushToast(`Findings load failed — ${e.message}`, 'red', '✕');
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
  // Maps total + by_source_table + by_writer onto the Brain panel. queueDepth /
  // decisions / growth / recent have no HTTP route → honest-empty in live.
  // Mock mode keeps PX.brain. 60s cadence (the corpus grows slowly).
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.memoryStats();
        if (!alive || !res) return;
        setBrain(res);
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

  // ── Live: media Gate-2 queue (GET /api/media-approval/pending) ──
  // Real pending podcast/video awaiting review + the gate2Pending count. The
  // render-rate KPIs have no read → honest '—' in live. Mock keeps PX.media.
  // 60s cadence.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.mediaQueue();
        if (!alive || !res) return;
        setMedia(res);
      } catch (e) {
        pushToast(`Media queue load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: scheduled-publish queue (GET /api/scheduling) ──
  // Real posts with status='scheduled' + a future published_at. The panel
  // derives depth / next-slot / past-due / upcoming-24h. Mock keeps PX.schedule.
  // 60s cadence.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.schedule();
        if (!alive || !res) return;
        setSchedule(res);
      } catch (e) {
        pushToast(`Schedule load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: SEO refresh pipeline (GET /api/seo) ────────────
  // Real seo_opportunities — the actionable queue + recent refresh outcomes.
  // Read-only (the seo.refresh loop is autonomous). Mock keeps PX.seo. 5-min
  // cadence (opportunities move on the harvest cycle, not second-to-second).
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.seo();
        if (!alive || !res) return;
        setSeo(res);
      } catch (e) {
        pushToast(`SEO load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: KPI strip reads (GET /api/posts + /api/analytics/views) ──
  // The overview KPIs are mostly a projection of state other panels already
  // load (cost → spend, inbox → awaiting-approval); these two reads cover the
  // rest. posts → published-in-30d + a real per-day histogram from the same
  // rows; analytics(days=1) → page views over the last 24h. quality/failed have
  // no live read → honest '—' (mapped in kpis.js). On a read failure we store
  // null so that KPI renders honest-empty, never the mock value
  // (feedback_no_dummy_data). 5-min cadence — these move slowly.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      const [posts, views] = await Promise.all([
        PX.api.posts('?limit=100&published_only=true').catch(() => null),
        PX.api.analyticsViews('?days=1').catch(() => null),
      ]);
      if (!alive) return;
      setKpiReads({ posts, views });
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
      setServices((s) =>
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
    restart: (s) => {
      closeDrawer();
      pushToast(`Restarting ${s.name}…`, 'cyan', '↻');
      pushFeed(['cyan', 'REMEDIATE'], `operator restarted <b>${s.name}</b>`);
      setServices((arr) =>
        arr.map((x) =>
          x.name === s.name ? { ...x, status: 'ok', metric: 'starting…' } : x
        )
      );
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
      setMedia((m) => ({
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
        setMedia(prev);
        pushToast(`Media approve failed — ${err.message}`, 'red', '✕');
      }
    },
    mediaReject: async (it) => {
      const prev = media;
      setMedia((m) => ({
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
        setMedia(prev);
        pushToast(`Media reject failed — ${err.message}`, 'red', '✕');
      }
    },
    // Reschedule a scheduled post by a duration (PATCH /api/scheduling/shift).
    // Optimistic: shift the row's published_at locally, roll back on failure.
    scheduleShift: async (postId, byDelta) => {
      const prev = schedule;
      const neg = byDelta.trim().startsWith('-');
      const mm = byDelta.match(/(\d+)\s*hour/i);
      const dMs = (mm ? parseInt(mm[1], 10) : 0) * 3600000 * (neg ? -1 : 1);
      setSchedule((s) => ({
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
        setSchedule(prev);
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
      setTopics((t) => reRankBatch(t, b.batch_id, ordered));
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
        setTopics(prev);
        pushToast(`Rank failed — ${err.message}`, 'red', '✕');
      }
    },
    topicResolve: async (b) => {
      const prev = topics;
      setTopics((t) => removeBatch(t, b.batch_id));
      try {
        await PX.api.resolveTopicBatch(b.batch_id);
        pushToast('Batch resolved — winner queued to pipeline', 'mint', '✓');
        pushFeed(
          ['mint', 'TOPICS'],
          `operator resolved batch <b>${String(b.batch_id).slice(0, 8)}</b> → pipeline`
        );
      } catch (err) {
        setTopics(prev);
        pushToast(`Resolve failed — ${err.message}`, 'red', '✕');
      }
    },
    topicReject: async (b) => {
      const prev = topics;
      setTopics((t) => removeBatch(t, b.batch_id));
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
        setTopics(prev);
        pushToast(`Reject failed — ${err.message}`, 'red', '✕');
      }
    },
    launch: (t) => pushToast(`Opening ${t.name}…`, 'cyan', '↗'),
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
  };

  const open = (type, data) => setEntity({ type, data });

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
      ['feed', 'Operations feed', null],
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
              : m === 'feed'
                ? 'audit'
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
  // from the live `inbox`, published/traffic from the kpiReads effect, and an
  // honest '—' for anything with no live route (kpis.js / feedback_no_dummy_data).
  const kpis = useMemo(() => {
    if (!PX.api.isLive()) return PX.kpis;
    const pendingApproval = inbox.filter((i) => i.kind === 'approve').length;
    return PX.kpisFromLive(
      PX.kpis,
      { cost, pendingApproval, posts: kpiReads.posts, views: kpiReads.views },
      Date.now()
    );
  }, [cost, inbox, kpiReads]);

  return (
    <div className={`app gl-atmosphere mode-${mode}`}>
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
            ['feed', 'audit', 'Feed'],
            ['map', 'gpu', 'Map'],
            ['wall', 'overview', 'Wall'],
          ].map(([m, ic, lbl]) => (
            <button
              key={m}
              className={mode === m ? 'is-active' : ''}
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
            <span className="live-dot" style={{ verticalAlign: 'middle' }} /> 5m
          </span>
          <span className="tnum">{clock}</span>
        </div>
      </header>

      {/* Main */}
      <main className="main" ref={mainRef}>
        {mode === 'console' && (
          <div className="main__inner">
            <div id="sec-overview">
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
                  onRetry={A.retry}
                  onAck={A.ack}
                  onFix={A.fix}
                />
              </div>
              {approved.length > 0 && (
                <div id="sec-publish">
                  <PublishQueue items={approved} onPublish={A.publish} />
                </div>
              )}
              <div id="sec-schedule">
                <SchedulePanel schedule={schedule} onShift={A.scheduleShift} />
              </div>
              <div id="sec-services">
                <ServiceGrid
                  services={services}
                  onOpen={(s) => open('service', s)}
                  onRestart={A.restart}
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
                />
              </div>
              <div id="sec-gpu">
                <GpuHud gpu={gpu} onOpen={() => open('gpu', gpu)} />
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
                <SeoPanel seo={seo} />
              </div>
              <div id="sec-cost">
                <CostPanel cost={cost} onOpen={() => open('cost', cost)} />
              </div>
              <div id="sec-launch">
                <LauncherPanel
                  tools={PX.launcher}
                  onLaunch={A.launch}
                  onVoice={A.voice}
                />
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
                />
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

        {mode === 'feed' && (
          <div className="main__inner">
            <FeedMode
              inbox={inbox}
              feed={feed}
              filter={feedFilter}
              setFilter={setFeedFilter}
              onOpen={(it) => open('inbox', it)}
              A={A}
            />
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

// Map a /api/tasks/pending-approval row → the Action Inbox item shape.
function approvalToInbox(t) {
  const PX = window.PX;
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
          <span className="idx">▲</span>READY TO PUBLISH
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
