/* Pure chart-math for the console time-series surface. Dual-mode (browser global
   + module.exports) so it unit-tests on node:test with no DOM, exactly like
   js/kpis.js. Loaded by index.html before api.js. */
(function () {
  const RANGES = { '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800 };

  // ~240 samples across the range, 15s floor (Prometheus caps at 11000 points).
  function deriveStep(rangeSeconds) {
    return Math.max(15, Math.round(rangeSeconds / 240));
  }

  // Bounds over every non-null point across all series. Empty -> null bounds.
  function seriesBounds(series) {
    let tMin = Infinity,
      tMax = -Infinity,
      vMin = Infinity,
      vMax = -Infinity;
    (series || []).forEach((s) =>
      (s.points || []).forEach(([t, v]) => {
        if (t < tMin) tMin = t;
        if (t > tMax) tMax = t;
        if (v != null && Number.isFinite(v)) {
          if (v < vMin) vMin = v;
          if (v > vMax) vMax = v;
        }
      })
    );
    if (!Number.isFinite(tMin))
      return { tMin: null, tMax: null, vMin: null, vMax: null };
    if (!Number.isFinite(vMin)) {
      vMin = 0;
      vMax = 1;
    }
    return { tMin, tMax, vMin, vMax };
  }

  function isEmptySeries(series) {
    return !(series || []).some((s) =>
      (s.points || []).some(([, v]) => v != null && Number.isFinite(v))
    );
  }

  function scaleX(t, tMin, tMax, box) {
    const span = tMax - tMin || 1;
    return box.x + ((t - tMin) / span) * box.w;
  }
  function scaleY(v, vMin, vMax, box) {
    const span = vMax - vMin || 1;
    return box.y + box.h - ((v - vMin) / span) * box.h;
  }

  // SVG `d` string. A null value ends the current sub-path; the next finite
  // value starts a fresh `M` — an honest gap, never a line drawn through nothing.
  function buildPath(points, bounds, box) {
    let d = '';
    let pen = false; // is the pen down (mid sub-path)?
    (points || []).forEach(([t, v]) => {
      if (v == null || !Number.isFinite(v)) {
        pen = false;
        return;
      }
      const x = scaleX(t, bounds.tMin, bounds.tMax, box).toFixed(1);
      const y = scaleY(v, bounds.vMin, bounds.vMax, box).toFixed(1);
      d += (pen ? 'L' : 'M') + x + ',' + y + ' ';
      pen = true;
    });
    return d.trim();
  }

  // Colorblind-safe: identity carried by dash pattern (+ a direct label + opacity
  // at the call site), never hue. Index 0 solid; the rest distinct dashes.
  const DASHES = ['', '5,3', '1.5,2.5', '7,3,1.5,3', '3,2'];
  function dashFor(i) {
    return DASHES[i % DASHES.length];
  }

  function _ticks(min, max, n) {
    if (min == null || max == null) return [];
    const span = max - min || 1;
    const out = [];
    for (let i = 0; i <= n; i++) out.push(min + (span * i) / n);
    return out;
  }
  function yTicks(vMin, vMax, n = 4) {
    return _ticks(vMin, vMax, n);
  }
  function xTicks(tMin, tMax, n = 4) {
    return _ticks(tMin, tMax, n);
  }

  // Last point in a series with a finite value — the reading a chart can
  // honestly report at idle. Null when the series has none.
  function latestPoint(points) {
    for (let i = (points || []).length - 1; i >= 0; i--) {
      const p = points[i];
      if (p && p[1] != null && Number.isFinite(p[1])) return p;
    }
    return null;
  }

  // Prometheus matrix result -> canonical series. Seconds -> ms. Label: when
  // labelBy is set and present on a series' metric, use labelPrefix+that value
  // (e.g. "GPU 0"); else the join of non-__name__ labels, else fallbackLabel.
  function matrixToSeries(result, fallbackLabel, labelBy, labelPrefix) {
    return (result || []).map((r) => {
      const m = r.metric || {};
      let label;
      if (labelBy && m[labelBy] != null) {
        label = (labelPrefix || '') + m[labelBy];
      } else {
        const parts = Object.keys(m)
          .filter((k) => k !== '__name__')
          .map((k) => k + '=' + m[k]);
        label = parts.length ? parts.join(',') : fallbackLabel || 'value';
      }
      const points = (r.values || []).map(([t, v]) => [
        Math.round(Number(t) * 1000),
        v == null ? null : Number(v),
      ]);
      return { label, points };
    });
  }

  const api = {
    RANGES,
    deriveStep,
    seriesBounds,
    isEmptySeries,
    scaleX,
    scaleY,
    buildPath,
    dashFor,
    yTicks,
    xTicks,
    latestPoint,
    matrixToSeries,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') (window.PX || (window.PX = {})).ts = api;
})();
