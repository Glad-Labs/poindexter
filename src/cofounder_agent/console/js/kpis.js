/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — live KPI mapper (PURE).
   ──────────────────────────────────────────────────────────────
   The overview KPI strip is a projection of data the rest of the console
   already loads (cost, inbox) plus two read-only surfaces (posts, analytics).
   This file owns the one non-trivial part: mapping those reads onto the strip
   in LIVE mode and rendering honest-empty ('—') for any KPI with no live route,
   so the strip never contradicts the live panels beside it (feedback_no_dummy_data —
   the same posture as the brain queueDepth → '—' / media render-rate → '—' wiring).

   Per-KPI live source (mock mode never calls this — app.jsx keeps PX.kpis):
     spend     ← the budget()-loaded `cost` state (SAME source as the Cost panel,
                 so the two cannot drift); honest-empty if the budget read failed.
     approval  ← the live pending-approval count (derived from `inbox`).
     published ← GET /api/posts: count within the trailing 30d + a real per-day
                 histogram derived from the same rows (no extra call, no mock).
     traffic   ← GET /api/analytics/views?days=1: sum of the 24h view buckets.
     quality   ← honest-empty (quality_score is NOT exposed on /api/posts).
     failed    ← honest-empty (no 24h-failed-tasks route).

   PURE + dual-mode: `nowMs` is injected so the 30d / 24h windows are
   deterministic (unit-tested in __tests__/kpis.test.js — the console-unit
   harness, `npm run test:console`). Exposed as window.PX.kpisFromLive (app.jsx)
   and module.exports (the test).
   ────────────────────────────────────────────────────────────── */
(function () {
  const DAY_MS = 86400000;

  // Honest-empty KPI: the live read is missing, or this metric has no live route
  // yet. Drop the value/spark/delta/tone AND the '$' unit so the strip renders an
  // explicit '—' rather than carrying the mock number into a live view.
  const honestEmpty = (base) => ({
    ...base,
    value: '—',
    unit: '',
    spark: [],
    delta: 0,
    deltaLabel: '',
    tone: '',
  });

  const spendKpi = (base, cost) => {
    if (!cost || cost.monthToDate == null) return honestEmpty(base);
    const pct = Number(cost.percentUsed);
    const hot = cost.status === 'warning' || (isFinite(pct) && pct >= 80);
    return {
      ...base,
      value: cost.monthToDate,
      unit: '$',
      spark: [], // budget() exposes no by-day series → honest blank, real value
      delta: 0,
      deltaLabel: cost.budget != null ? `$${cost.budget} budget` : '',
      tone: hot ? 'amber' : '',
    };
  };

  const approvalKpi = (base, pendingApproval) => {
    if (pendingApproval == null) return honestEmpty(base);
    const n = Number(pendingApproval) || 0;
    return {
      ...base,
      value: n,
      spark: [],
      delta: n > 0 ? 1 : 0,
      deltaLabel: n > 0 ? 'needs you' : 'inbox zero',
      tone: n > 0 ? 'amber' : '',
    };
  };

  const publishedKpi = (base, posts, nowMs) => {
    if (!posts || !Array.isArray(posts.posts)) return honestEmpty(base);
    // 30 day-buckets, oldest→newest (index 29 = today). The count and the
    // histogram share ONE predicate (0 ≤ daysAgo < 30) so value === sum(spark)
    // always — no off-by-one between the headline number and its sparkline.
    const buckets = new Array(30).fill(0);
    let count = 0;
    posts.posts.forEach((p) => {
      const t = p && p.published_at ? Date.parse(p.published_at) : NaN;
      const daysAgo = Math.floor((nowMs - t) / DAY_MS);
      if (daysAgo >= 0 && daysAgo < 30) {
        count += 1;
        buckets[29 - daysAgo] += 1;
      }
    });
    return {
      ...base,
      value: count,
      spark: buckets,
      delta: 0,
      deltaLabel: 'last 30d',
      tone: '',
    };
  };

  const trafficKpi = (base, views) => {
    if (!views || !Array.isArray(views.daily)) return honestEmpty(base);
    const sum = views.daily.reduce(
      (a, d) => a + (Number(d && d.views) || 0),
      0
    );
    return {
      ...base,
      value: sum,
      spark: [], // ?days=1 yields no usable 30-pt series → honest blank
      delta: 0,
      deltaLabel: 'last 24h',
      tone: '',
    };
  };

  // Map the mock KPI base array onto live values, keyed by id. Unmapped ids and
  // metrics without a live route fall through to honest-empty. Order preserved.
  function kpisFromLive(baseKpis, live, nowMs) {
    const l = live || {};
    const now = nowMs == null ? Date.now() : nowMs;
    return (baseKpis || []).map((base) => {
      switch (base.id) {
        case 'spend':
          return spendKpi(base, l.cost);
        case 'approval':
          return approvalKpi(base, l.pendingApproval);
        case 'published':
          return publishedKpi(base, l.posts, now);
        case 'traffic':
          return trafficKpi(base, l.views);
        // quality_score isn't on /api/posts; there's no 24h-failed route. Wire
        // these to honest values when a backing read lands.
        case 'quality':
        case 'failed':
        default:
          return honestEmpty(base);
      }
    });
  }

  // Dual-mode: browser global (app.jsx → PX.kpisFromLive) + CommonJS (kpis.test.js).
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { kpisFromLive };
  }
  if (typeof window !== 'undefined') {
    window.PX = window.PX || {};
    window.PX.kpisFromLive = kpisFromLive;
  }
})();
