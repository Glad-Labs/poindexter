/* Native time-series charts for the Telemetry surface (sub-project C). Zero-dep
   SVG, colorblind-safe (dash + end-label + opacity, never hue). Chart math lives
   in js/timeseries.js (PX.ts); this file is the thin React rendering layer.
   Loaded by index.html after panels2.jsx, before app.jsx. */
// useState/useRef are primitives.jsx globals (it loads first). Do NOT re-declare
// them: every text/babel script shares ONE global lexical scope, so a second
// `const { useState } = React` throws "already declared" and aborts this file.
const TS = () => window.PX.ts;

// Colorblind-safe stroke set (Okabe–Ito-ish); identity still carried by dash +
// label so the chart reads in grayscale. Cycles for >5 series.
const STROKES = ['#4fc3f7', '#ffb74d', '#81c784', '#ba68c8', '#e0e0e0'];

function TimeChart({ series, stale, height = 150, unit = '' }) {
  const ref = useRef(null);
  const [hoverX, setHoverX] = useState(null);
  const ts = TS();
  const list = series || [];
  const W = 320,
    H = height,
    PADL = 34,
    PADB = 16,
    PADT = 8,
    PADR = 12;
  const box = { x: PADL, y: PADT, w: W - PADL - PADR, h: H - PADT - PADB };

  if (ts.isEmptySeries(list)) {
    return (
      <div className="tc-empty" style={{ height: H, opacity: stale ? 0.5 : 1 }}>
        no data in range
      </div>
    );
  }
  const b = ts.seriesBounds(list);
  const yt = ts.yTicks(b.vMin, b.vMax, 4);
  const xt = ts.xTicks(b.tMin, b.tMax, 4);
  const fmtV = (v) =>
    Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100;
  const fmtT = (t) => {
    const d = new Date(t);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  // hover: track x within the plot box
  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    setHoverX(px >= box.x && px <= box.x + box.w ? px : null);
  };
  const hoverT =
    hoverX == null
      ? null
      : b.tMin + ((hoverX - box.x) / box.w) * (b.tMax - b.tMin);
  const nearest = (pts) => {
    let best = null,
      bd = Infinity;
    (pts || []).forEach(([t, v]) => {
      if (v == null) return;
      const dd = Math.abs(t - hoverT);
      if (dd < bd) {
        bd = dd;
        best = [t, v];
      }
    });
    return best;
  };

  return (
    <div className="tc" style={{ opacity: stale ? 0.5 : 1 }}>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        style={{ display: 'block' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHoverX(null)}
      >
        {yt.map((v, i) => {
          const y = ts.scaleY(v, b.vMin, b.vMax, box).toFixed(1);
          return (
            <g key={'y' + i}>
              <line
                x1={box.x}
                y1={y}
                x2={box.x + box.w}
                y2={y}
                stroke="var(--gl-line, rgba(255,255,255,.08))"
                strokeWidth="0.5"
              />
              <text
                x={box.x - 4}
                y={y}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="7"
                fill="var(--gl-dim, #8a94a6)"
              >
                {fmtV(v)}
              </text>
            </g>
          );
        })}
        {xt.map((t, i) => (
          <text
            key={'x' + i}
            x={ts.scaleX(t, b.tMin, b.tMax, box).toFixed(1)}
            y={H - 4}
            textAnchor="middle"
            fontSize="7"
            fill="var(--gl-dim, #8a94a6)"
          >
            {fmtT(t)}
          </text>
        ))}
        {list.map((s, i) => (
          <path
            key={s.label + i}
            d={ts.buildPath(s.points, b, box)}
            fill="none"
            stroke={STROKES[i % STROKES.length]}
            strokeWidth="1.5"
            strokeDasharray={ts.dashFor(i)}
          />
        ))}
        {hoverX != null && (
          <line
            x1={hoverX}
            y1={box.y}
            x2={hoverX}
            y2={box.y + box.h}
            stroke="var(--gl-dim, #8a94a6)"
            strokeWidth="0.5"
            strokeDasharray="2,2"
          />
        )}
      </svg>
      {list.length >= 2 && (
        <ul className="tc-legend">
          {list.map((s, i) => (
            <li className="tc-legend__item" key={s.label + i}>
              <svg
                className="tc-legend__sw"
                viewBox="0 0 18 6"
                width="18"
                height="6"
                aria-hidden="true"
              >
                <line
                  x1="0"
                  y1="3"
                  x2="18"
                  y2="3"
                  stroke={STROKES[i % STROKES.length]}
                  strokeWidth="2"
                  strokeDasharray={ts.dashFor(i)}
                />
              </svg>
              {s.label}
            </li>
          ))}
        </ul>
      )}
      {hoverX != null && (
        <div className="tc-readout">
          {fmtT(hoverT)}
          {' · '}
          {list.map((s, i) => {
            const n = nearest(s.points);
            return n ? (
              <span key={i} style={{ marginRight: 8 }}>
                {s.label}{' '}
                <b>
                  {fmtV(n[1])}
                  {unit}
                </b>
              </span>
            ) : null;
          })}
        </div>
      )}
    </div>
  );
}

function RangeControl({ value, onChange }) {
  return (
    <div className="rangectl" role="group" aria-label="time range">
      {Object.keys(window.PX.ts.RANGES).map((r) => (
        <button
          key={r}
          type="button"
          className={'rangectl__btn' + (r === value ? ' is-active' : '')}
          aria-pressed={r === value}
          onClick={() => onChange(r)}
        >
          {r}
        </button>
      ))}
    </div>
  );
}

// One history chart: its own poll keyed by (title, range) so a range change is a
// fresh resource (A's AbortController prevents a stale response clobbering it).
function HistoryChart({ title, fetchSeries, range, unit }) {
  const r = window.PXR.usePolledResource(() => fetchSeries(range), {
    intervalMs: 30000,
    key: title + ':' + range,
  });
  return (
    <div className="panel tc-panel">
      <div className="panel__head">
        <span className="panel__title">{title}</span>
        <span style={{ flex: 1 }} />
        <Freshness lastUpdatedAt={r.lastUpdatedAt} stale={r.stale} />
      </div>
      <TimeChart
        series={r.data && r.data.series}
        stale={r.stale}
        unit={unit || ''}
      />
    </div>
  );
}

// A titled RangeControl over a responsive grid of HistoryCharts. Each group
// owns its own range state, so HISTORY and GPU & POWER range independently.
function TrendGroup({ id, heading, charts, icon = 'pulse' }) {
  const [range, setRange] = useState('6h');
  return (
    <div id={id}>
      <div className="panel__head" style={{ marginBottom: 8 }}>
        <span className="panel__title">
          <Icon name={icon} size={14} className="panel__ico" />
          {heading}
        </span>
        <span style={{ flex: 1 }} />
        <RangeControl value={range} onChange={setRange} />
      </div>
      <div className="tc-grid">
        {charts.map((c) => (
          <HistoryChart
            key={c.title}
            title={c.title}
            fetchSeries={c.fn}
            range={range}
            unit={c.unit}
          />
        ))}
      </div>
    </div>
  );
}

function HistoryPanel() {
  const api = window.PX.api;
  const charts = [
    { title: 'API request rate', fn: (x) => api.httpRateSeries(x), unit: '' },
    { title: 'API error rate', fn: (x) => api.httpErrorSeries(x), unit: '%' },
    {
      title: 'API latency (p95/p99)',
      fn: (x) => api.httpLatencySeries(x),
      unit: 's',
    },
    {
      title: 'Pipeline throughput',
      fn: (x) => api.throughputSeries(x),
      unit: '',
    },
    {
      title: 'Spend (API vs electricity)',
      fn: (x) => api.costSeries(x),
      unit: '$',
    },
    { title: 'QA pass-rate', fn: (x) => api.qaTrend(x), unit: '%' },
    {
      title: 'Findings by severity',
      fn: (x) => api.findingsTrend(x),
      unit: '',
    },
  ];
  return <TrendGroup id="sec-history" heading="HISTORY" charts={charts} />;
}

function HardwarePanel() {
  const api = window.PX.api;
  const charts = [
    { title: 'GPU utilization', fn: (x) => api.gpuUtilSeries(x), unit: '%' },
    { title: 'GPU temperature', fn: (x) => api.gpuTempSeries(x), unit: '°C' },
    { title: 'VRAM used', fn: (x) => api.vramUsedSeries(x), unit: ' GB' },
    { title: 'GPU power draw', fn: (x) => api.gpuPowerSeries(x), unit: ' W' },
    { title: 'System power', fn: (x) => api.systemPowerSeries(x), unit: ' W' },
    {
      title: 'Electricity cost',
      fn: (x) => api.electricityCostSeries(x),
      unit: '$/day',
    },
  ];
  return (
    <TrendGroup
      id="sec-hardware"
      heading="GPU & POWER"
      charts={charts}
      icon="gpu"
    />
  );
}

function DatabasePanel() {
  const api = window.PX.api;
  const charts = [
    { title: 'Connections', fn: (x) => api.dbConnectionsSeries(x), unit: '' },
    {
      title: 'Connections by state',
      fn: (x) => api.dbConnStateSeries(x),
      unit: '',
    },
    { title: 'Cache hit ratio', fn: (x) => api.dbCacheHitSeries(x), unit: '%' },
    {
      title: 'Transaction rate',
      fn: (x) => api.dbTxnRateSeries(x),
      unit: '/s',
    },
    { title: 'Database size', fn: (x) => api.dbSizeSeries(x), unit: ' GB' },
    { title: 'Dead tuples', fn: (x) => api.dbDeadTuplesSeries(x), unit: '' },
  ];
  return (
    <TrendGroup
      id="sec-database"
      heading="DATABASE"
      charts={charts}
      icon="services"
    />
  );
}

Object.assign(window, {
  TimeChart,
  RangeControl,
  TrendGroup,
  HistoryPanel,
  HardwarePanel,
  DatabasePanel,
});
