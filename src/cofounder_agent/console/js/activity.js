'use strict';

// Pure, dual-mode mapper (browser global + module.exports) that turns the
// /api/activity payload into the three columns the NOW RUNNING band renders:
// In Production (content/media), Background (jobs/brain), and the Just-Happened
// trail. Kept pure so the band stays a thin renderer and the kind→column split
// + elapsed math is unit-tested (feedback_no_dummy_data: an idle system maps to
// empty columns, never a fabricated row). Run `npm run test:console`.
(function () {
  function elapsedS(startIso, nowMs) {
    const t = Date.parse(startIso);
    return Number.isFinite(t)
      ? Math.max(0, Math.round((nowMs - t) / 1000))
      : null;
  }

  function mapActivity(raw, nowMs) {
    const running = (raw && raw.running) || [];
    const recent = (raw && raw.recent) || [];
    const decorate = (r) => ({ ...r, elapsedS: elapsedS(r.started_at, nowMs) });
    return {
      inProduction: running
        .filter((r) => r.kind === 'content' || r.kind === 'media')
        .map(decorate),
      background: running
        .filter((r) => r.kind === 'job' || r.kind === 'brain')
        .map(decorate),
      trail: recent.map((r) => ({ ...r, durationMs: r.duration_ms })),
      summary: (raw && raw.summary) || { running_by_kind: {} },
    };
  }

  // Freshness presentation for the band. `fresh` is the whole polled-resource
  // object (app.jsx passes fresh={activityR}); this turns it into the band's
  // className + <Freshness> badge props. Null-safe over a missing resource so
  // older callers render un-annotated instead of crashing. Stale keeps the
  // retained rows VISIBLE — panels hold their last values, and a retained row
  // relabeled is honest where a fabricated idle is not (feedback_no_dummy_data);
  // it only changes how the band is dressed. Without this the elapsed ages
  // (mapActivity above recomputes them from started_at every render) keep
  // counting up on a dead payload, indistinguishable from live work.
  function mapPulseFreshness(fresh) {
    const stale = !!(fresh && fresh.stale);
    return {
      stale,
      lastUpdatedAt:
        fresh && fresh.lastUpdatedAt != null ? fresh.lastUpdatedAt : null,
      bandClass: 'nowrun' + (stale ? ' nowrun--stale' : ''),
      showBadge: fresh != null,
    };
  }

  // Dual-mode: browser global (app.jsx → PX.mapActivity) + CommonJS (activity.test.js).
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { mapActivity, mapPulseFreshness };
  }
  if (typeof window !== 'undefined') {
    window.PX = window.PX || {};
    window.PX.mapActivity = mapActivity;
    window.PX.mapPulseFreshness = mapPulseFreshness;
  }
})();
