/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — UI primitives.
   Icons, gauges, sparklines, meters, toasts. Exposes to window.
   ────────────────────────────────────────────────────────────── */
const { useState, useEffect, useRef, useCallback } = React;

/* ─── Icon set (stroked line glyphs, currentColor) ──────────── */
const ICON_PATHS = {
  overview: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
  pipeline: 'M4 7h6m4 0h6M4 12h16M4 17h6m4 0h6',
  brain:
    'M12 4a4 4 0 0 0-4 4 3 3 0 0 0-1 5 3 3 0 0 0 3 4 3 3 0 0 0 4 1 3 3 0 0 0 4-1 3 3 0 0 0 3-4 3 3 0 0 0-1-5 4 4 0 0 0-4-4 3 3 0 0 0-4 0zM12 8v9',
  gpu: 'M5 5h14v14H5zM9 9h6v6H9zM5 2v3M19 2v3M5 19v3M19 19v3M2 9h3M2 14h3M19 9h3M19 14h3',
  audit: 'M4 5h16M4 10h16M4 15h10M4 20h7',
  cost: 'M12 2v20M16 6.5c0-1.9-1.8-3-4-3s-4 1-4 3 1.8 2.7 4 3.2 4 1.3 4 3.3-1.8 3-4 3-4-1.1-4-3',
  services: 'M4 4h16v5H4zM4 13h16v5H4zM7 6.5h.01M7 15.5h.01',
  check: 'M5 12l5 5L20 6',
  x: 'M6 6l12 12M18 6L6 18',
  retry: 'M3 12a9 9 0 1 0 3-6.7M3 4v4h4',
  kill: 'M9 3h6l1 3H8zM6 6h12l-1 14H7zM10 10v6M14 10v6',
  bell: 'M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6M10 21h4',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  close: 'M6 6l12 12M18 6L6 18',
  chevron: 'M9 6l6 6-6 6',
  alert: 'M12 3l9 16H3zM12 9v5M12 17h.01',
  settings:
    'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M5 5l2 2M17 17l2 2M2 12h3M19 12h3M5 19l2-2M17 7l2-2',
  play: 'M6 4l14 8-14 8z',
  link: 'M9 15l6-6M10 6l1-1a4 4 0 0 1 6 6l-1 1M14 18l-1 1a4 4 0 0 1-6-6l1-1',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4',
  refresh: 'M3 12a9 9 0 1 0 3-6.7M3 4v4h4',
  cpu: 'M5 5h14v14H5zM9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3',
  doc: 'M6 2h8l4 4v16H6zM14 2v4h4',
  pulse: 'M3 12h4l2-7 4 14 2-7h6',
};

function Icon({ name, size = 18, style, className = '' }) {
  const d = ICON_PATHS[name] || ICON_PATHS.overview;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={style}
      className={className}
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

/* ─── Sparkline (filled area) ───────────────────────────────── */
function Sparkline({
  data,
  color = 'var(--gl-cyan)',
  height = 22,
  fill = true,
  strokeW = 1.4,
}) {
  const w = 100,
    h = height;
  const min = Math.min(...data),
    max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 3) - 1.5;
    return [x, y];
  });
  const line = pts
    .map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`)
    .join(' ');
  const area = `${line} L${w},${h} L0,${h} Z`;
  const gid = useRef('sg' + Math.random().toString(36).slice(2, 8)).current;
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      width="100%"
      height={h}
      style={{ display: 'block' }}
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill && <path d={area} fill={`url(#${gid})`} />}
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={strokeW}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/* ─── Arc gauge (270° HUD dial, gap at bottom) ──────────────── */
function ArcGauge({
  value,
  max = 100,
  label,
  unit = '',
  color,
  warnAt = 0.75,
  dangerAt = 0.9,
}) {
  const pct = Math.max(0, Math.min(1, value / max));
  const auto =
    pct >= dangerAt
      ? 'var(--gl-red)'
      : pct >= warnAt
        ? 'var(--gl-amber)'
        : 'var(--gl-cyan)';
  const stroke = color || auto;
  // Fixed 100×88 viewBox so the dial always fits its box (no clipping).
  const cx = 50,
    cy = 46,
    r = 37,
    sw = 7;
  // Screen angles (0°=3 o'clock, +y down). Gap is the bottom 90°.
  const START = 135,
    SWEEP = 270;
  const pol = (deg) => {
    const a = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const arcPath = (from, to) => {
    const [x1, y1] = pol(from),
      [x2, y2] = pol(to);
    const large = to - from > 180 ? 1 : 0;
    return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  };
  return (
    <div className="gauge">
      <svg
        viewBox="0 0 100 88"
        style={{
          width: '100%',
          maxWidth: 112,
          height: 'auto',
          display: 'block',
        }}
      >
        <path
          d={arcPath(START, START + SWEEP)}
          fill="none"
          stroke="var(--gl-hairline-strong)"
          strokeWidth={sw}
          strokeLinecap="round"
        />
        {pct > 0 && (
          <path
            d={arcPath(START, START + SWEEP * pct)}
            fill="none"
            stroke={stroke}
            strokeWidth={sw}
            strokeLinecap="round"
            style={{ transition: 'stroke .3s' }}
          />
        )}
        <text
          x="50"
          y="52"
          textAnchor="middle"
          fontFamily="var(--gl-font-display)"
          fontWeight="700"
          fontSize="22"
          fill="var(--gl-text)"
          style={{ fontVariantNumeric: 'tabular-nums' }}
        >
          {value}
          {unit && (
            <tspan fontSize="11" fill="var(--gl-text-muted)">
              {unit}
            </tspan>
          )}
        </text>
      </svg>
      <div className="gauge__label">{label}</div>
    </div>
  );
}

/* ─── Mini bar chart ────────────────────────────────────────── */
function MiniBars({ data, color = 'amber', labels, height = 90 }) {
  const max = Math.max(...data) || 1;
  return (
    <div>
      <div className="barchart" style={{ height }}>
        {data.map((v, i) => (
          <div
            key={i}
            className={`barchart__bar ${color === 'amber' ? 'amber' : ''}`}
            style={{ height: `${(v / max) * 100}%` }}
            title={String(v)}
          />
        ))}
      </div>
      {labels && (
        <div className="barchart-axis">
          <span>{labels[0]}</span>
          <span>{labels[1]}</span>
        </div>
      )}
    </div>
  );
}

/* ─── Meter bar ─────────────────────────────────────────────── */
function Meter({ value, max = 100, color = '' }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="meter">
      <div className={`meter__fill ${color}`} style={{ width: pct + '%' }} />
    </div>
  );
}

/* ─── Status glyph (colorblind-safe: glyph carries signal) ──── */
const STATUS_GLYPH = { ok: '✓', warn: '⚠', err: '✕', run: '◐', off: '○' };
function StatusText({ kind = 'ok', children }) {
  const cls =
    {
      ok: 'c-mint',
      warn: 'c-amber',
      err: 'c-red',
      run: 'c-cyan',
      off: 'c-dim',
    }[kind] || 'c-muted';
  return (
    <span className={`mono ${cls}`} style={{ fontSize: 11 }}>
      <span aria-hidden="true">{STATUS_GLYPH[kind]}</span> {children}
    </span>
  );
}

/* ─── Toast system ──────────────────────────────────────────── */
function useToasts() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((msg, tone = 'cyan', glyph = '✓') => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, msg, tone, glyph }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3400);
  }, []);
  const node = (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast--${t.tone}`}>
          <span className="glyph" aria-hidden="true">
            {t.glyph}
          </span>
          <span>{t.msg}</span>
        </div>
      ))}
    </div>
  );
  return [node, push];
}

/* ─── Panel wrapper ─────────────────────────────────────────── */
function Panel({
  idx,
  title,
  meta,
  action,
  onAction,
  accent,
  flush,
  children,
  style,
}) {
  return (
    <section
      className={`panel ${accent === 'amber' ? 'panel--amber' : ''}`}
      style={style}
    >
      <header className="panel__head">
        <span className="panel__title">
          {idx && <span className="idx">{idx}</span>}
          {title}
        </span>
        <span className="panel__spacer" />
        {meta && <span className="panel__meta">{meta}</span>}
        {action && (
          <button className="head-act" onClick={onAction}>
            {action}
            <Icon name="chevron" size={11} />
          </button>
        )}
      </header>
      <div className={`panel__body ${flush ? 'panel__body--flush' : ''}`}>
        {children}
      </div>
    </section>
  );
}

Object.assign(window, {
  Icon,
  Sparkline,
  ArcGauge,
  MiniBars,
  Meter,
  StatusText,
  StatusGlyph: STATUS_GLYPH,
  useToasts,
  Panel,
});
