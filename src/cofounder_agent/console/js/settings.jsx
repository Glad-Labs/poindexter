/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — app_settings editor (LIVE-WIRED).
   Loads via PX.api.listSettings(), saves via PX.api.updateSetting().
   Real async: loading / error / empty / in-flight / result states.
   A Connection panel configures the worker URL + token + live toggle,
   with a mock-simulation switch so these states are testable offline.
   ────────────────────────────────────────────────────────────── */

function Toggle({ value, onChange, disabled }) {
  const on = value === 'true' || value === true;
  return (
    <button
      type="button"
      className={`toggle ${on ? 'on' : ''}`}
      disabled={disabled}
      onClick={() => onChange(on ? 'false' : 'true')}
      aria-pressed={on}
    >
      <span className="toggle__track">
        <span className="toggle__knob" />
      </span>
      <span className="toggle__lbl">{on ? 'Enabled' : 'Disabled'}</span>
    </button>
  );
}

function SecretField({ value, onChange, original }) {
  const [editing, setEditing] = React.useState(false);
  if (editing) {
    return (
      <input
        type="text"
        autoFocus
        placeholder="paste new secret…"
        defaultValue=""
        onChange={(e) =>
          onChange(e.target.value ? 'enc:' + e.target.value : original)
        }
      />
    );
  }
  return (
    <div className="set-secret">
      <Icon name="settings" size={13} />
      <span style={{ flex: 1 }}>{value}</span>
      <button className="mbtn mbtn--ghost" onClick={() => setEditing(true)}>
        Set new
      </button>
    </div>
  );
}

function SettingControl({ s, onChange }) {
  switch (s.type) {
    case 'bool':
      return <Toggle value={s.value} onChange={onChange} />;
    case 'int':
      return (
        <input
          type="number"
          step="1"
          value={s.value}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case 'float':
      return (
        <input
          type="number"
          step="0.1"
          value={s.value}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case 'select':
      return (
        <select value={s.value} onChange={(e) => onChange(e.target.value)}>
          {s.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      );
    case 'textarea':
      return (
        <textarea value={s.value} onChange={(e) => onChange(e.target.value)} />
      );
    case 'secret':
      return (
        <SecretField value={s.value} onChange={onChange} original={s._orig} />
      );
    default:
      return (
        <input
          type="text"
          value={s.value}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

/* ─── Connection panel ──────────────────────────────────────── */
function ConnectionPanel({ onReload }) {
  const api = window.PX.api;
  const [live, setLive] = React.useState(api.isLive());
  const [base, setBase] = React.useState(api.config.base);
  const [token, setToken] = React.useState(api.config.token);
  const [sim, setSim] = React.useState(api.getSim());
  const [test, setTest] = React.useState({ state: 'idle', msg: '' });

  const runTest = async () => {
    api.setBase(base);
    api.setToken(token);
    setTest({ state: 'testing', msg: 'Pinging…' });
    try {
      const r = await api.health();
      setTest({
        state: 'ok',
        msg: r.mode === 'live' ? `Connected · ${r.ms}ms · ${r.base}` : r.detail,
      });
    } catch (e) {
      setTest({ state: 'err', msg: String(e.message || e) });
    }
  };
  const flipLive = (on) => {
    setLive(on);
    api.setLive(on);
    api.setBase(base);
    api.setToken(token);
    setTest({ state: 'idle', msg: '' });
    onReload();
  };
  const changeSim = (s) => {
    setSim(s);
    api.setSim(s);
    onReload();
  };

  return (
    <div className={`conn ${live ? 'conn--live' : ''}`}>
      <div className="conn__head">
        <span className={`tag ${live ? 'tag--mint' : 'tag--amber'}`}>
          {live ? '● LIVE' : '○ MOCK'}
        </span>
        <span className="conn__title">Worker connection</span>
        <span className="conn__spacer" />
        <Toggle value={String(live)} onChange={(v) => flipLive(v === 'true')} />
      </div>
      <div className="conn__grid">
        <div className="field">
          <label>Worker base URL</label>
          <input
            type="text"
            value={base}
            placeholder="http://localhost:8002  (blank = same-origin)"
            onChange={(e) => setBase(e.target.value)}
          />
          <span className="hint">
            Serve the console from the worker → leave blank (no CORS).
          </span>
        </div>
        <div className="field">
          <label>Bearer token</label>
          <input
            type="password"
            value={token}
            placeholder="verify_api_token"
            onChange={(e) => setToken(e.target.value)}
          />
          <span className="hint">
            Sent as Authorization: Bearer … · stored locally.
          </span>
        </div>
      </div>
      <div className="conn__foot">
        {!live && (
          <div className="field" style={{ margin: 0 }}>
            <label>
              Dev simulation <span className="c-dim">(mock only)</span>
            </label>
            <select value={sim} onChange={(e) => changeSim(e.target.value)}>
              <option value="normal">normal — instant, healthy</option>
              <option value="slow">slow — 1.6s latency</option>
              <option value="error">error — API fails</option>
              <option value="empty">empty — no rows returned</option>
            </select>
          </div>
        )}
        <span className="conn__spacer" />
        <div className="conn__test">
          {test.state !== 'idle' && (
            <span className={`conn__teststat ${test.state}`}>
              {test.state === 'testing' && <span className="spinner" />}
              {test.state === 'ok' && '✓ '}
              {test.state === 'err' && '✕ '}
              {test.msg}
            </span>
          )}
          <button
            className="mbtn mbtn--primary"
            onClick={runTest}
            disabled={test.state === 'testing'}
          >
            <Icon name="bolt" size={12} />
            Test connection
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── State screens ─────────────────────────────────────────── */
function LoadState() {
  return (
    <div className="empty" style={{ padding: '48px 16px' }}>
      <span className="spinner spinner--lg" />
      <div style={{ marginTop: 12 }}>
        Loading settings from <span className="c-cyan">/api/settings</span>…
      </div>
    </div>
  );
}
function ErrorState({ msg, onRetry }) {
  return (
    <div className="state-err">
      <div className="state-err__glyph" aria-hidden="true">
        ✕
      </div>
      <div className="state-err__title">Couldn’t load settings</div>
      <div className="state-err__msg">{msg}</div>
      <button className="mbtn mbtn--primary" onClick={onRetry}>
        <Icon name="retry" size={12} />
        Retry
      </button>
    </div>
  );
}
function EmptyState({ onRetry }) {
  return (
    <div className="empty" style={{ padding: '48px 16px' }}>
      <span className="glyph" aria-hidden="true">
        ∅
      </span>
      No settings returned. The table may be empty or the brain hasn’t seeded
      yet.
      <div style={{ marginTop: 12 }}>
        <button className="mbtn" onClick={onRetry}>
          <Icon name="retry" size={12} />
          Reload
        </button>
      </div>
    </div>
  );
}

/* ─── Settings editor ───────────────────────────────────────── */
function SettingsMode({ onApply, pushFeed }) {
  const api = window.PX.api;
  const [status, setStatus] = React.useState('loading'); // loading|ready|error|empty
  const [errMsg, setErrMsg] = React.useState('');
  const [meta, setMeta] = React.useState({ categories: [] });
  const [rows, setRows] = React.useState([]);
  const [cat, setCat] = React.useState(null);
  const [search, setSearch] = React.useState('');
  const [showDiff, setShowDiff] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [reloadKey, setReloadKey] = React.useState(0);
  const reload = () => setReloadKey((k) => k + 1);

  React.useEffect(() => {
    let alive = true;
    setStatus('loading');
    api
      .listSettings()
      .then((d) => {
        if (!alive) return;
        const list = (d && d.settings) || [];
        setMeta({ categories: (d && d.categories) || [] });
        setRows(list.map((s) => ({ ...s, _orig: s.value })));
        setCat(
          (c) =>
            c ||
            (d && d.categories && d.categories[0] && d.categories[0].id) ||
            null
        );
        setStatus(list.length ? 'ready' : 'empty');
      })
      .catch((e) => {
        if (alive) {
          setErrMsg(String(e.message || e));
          setStatus('error');
        }
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  const dirty = rows.filter((r) => r.value !== r._orig);
  const dirtyByCat = dirty.reduce(
    (a, r) => ((a[r.category] = (a[r.category] || 0) + 1), a),
    {}
  );
  const setVal = (id, v) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, value: v } : r)));
  const discard = () =>
    setRows((rs) => rs.map((r) => ({ ...r, value: r._orig })));

  const apply = async () => {
    setSaving(true);
    try {
      await Promise.all(dirty.map((r) => api.updateSetting(r.id, r.value)));
      const changes = dirty.map((r) => ({
        id: r.id,
        key: r.key,
        from: r._orig,
        to: r.value,
      }));
      setRows((rs) => rs.map((r) => ({ ...r, _orig: r.value })));
      setShowDiff(false);
      onApply(changes, true);
    } catch (e) {
      setShowDiff(false);
      onApply([], false, String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const q = search.toLowerCase().trim();
  const visible = q
    ? rows.filter((r) =>
        (r.key + ' ' + r.description).toLowerCase().includes(q)
      )
    : rows.filter((r) => r.category === cat);
  const fmt = (r, v) =>
    r.type === 'secret' ? '••••••••' : v === '' ? '(empty)' : v;
  const catLabel = (id) =>
    (meta.categories.find((c) => c.id === id) || {}).label || id;

  return (
    <div className="main__inner">
      <div
        className="feedmode__head"
        style={{ maxWidth: 1100, margin: '0 auto 14px' }}
      >
        <span className="panel__title">
          <span className="idx">⚙</span>APP SETTINGS
        </span>
        <span className="panel__spacer" style={{ flex: 1 }} />
        <span className="panel__meta">
          {api.isLive() ? 'LIVE · /api/settings' : 'MOCK · ' + api.getSim()}
          {status === 'ready'
            ? ` · ${rows.length} KEYS · ${dirty.length} UNSAVED`
            : ''}
        </span>
      </div>

      <div style={{ maxWidth: 1100, margin: '0 auto 16px' }}>
        <ConnectionPanel onReload={reload} />
      </div>

      {status === 'loading' && (
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <LoadState />
        </div>
      )}
      {status === 'error' && (
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <ErrorState msg={errMsg} onRetry={reload} />
        </div>
      )}
      {status === 'empty' && (
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <EmptyState onRetry={reload} />
        </div>
      )}

      {status === 'ready' && (
        <div className="settings">
          <aside className="set-side">
            <div className="set-search">
              <Icon name="search" size={13} />
              <input
                placeholder="Search keys…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            {!q && (
              <>
                <div className="set-side__head">Categories</div>
                {meta.categories.map((c) => {
                  const n = rows.filter((r) => r.category === c.id).length;
                  if (!n) return null;
                  return (
                    <button
                      key={c.id}
                      className={`set-cat ${cat === c.id ? 'is-active' : ''} ${dirtyByCat[c.id] ? 'has-dirty' : ''}`}
                      onClick={() => setCat(c.id)}
                    >
                      <span>{c.label}</span>
                      <span className="set-cat__n">
                        {dirtyByCat[c.id] ? '● ' + dirtyByCat[c.id] : n}
                      </span>
                    </button>
                  );
                })}
              </>
            )}
          </aside>

          <div>
            <div className="set-grouphead">
              {q ? `Search · “${search}”` : catLabel(cat)}
            </div>
            {visible.length === 0 && (
              <div className="empty">No settings match “{search}”.</div>
            )}
            {visible.map((r) => {
              const isDirty = r.value !== r._orig;
              return (
                <div
                  key={r.id}
                  className={`set-row ${isDirty ? 'is-dirty' : ''}`}
                >
                  <div className="set-row__info">
                    <div className="set-row__key">
                      {isDirty && <span className="dirtydot" />}
                      {r.key}
                    </div>
                    <div className="set-row__desc">{r.description}</div>
                  </div>
                  <div className="set-row__ctl">
                    <SettingControl s={r} onChange={(v) => setVal(r.id, v)} />
                  </div>
                </div>
              );
            })}

            {dirty.length > 0 && (
              <div className="savebar">
                <span className="savebar__txt">
                  <b>{dirty.length}</b> unsaved change
                  {dirty.length > 1 ? 's' : ''}
                </span>
                <span className="savebar__spacer" />
                <button
                  className="mbtn mbtn--ghost"
                  onClick={discard}
                  disabled={saving}
                >
                  Discard
                </button>
                <button
                  className="mbtn mbtn--primary"
                  onClick={() => setShowDiff(true)}
                  disabled={saving}
                >
                  <Icon name="check" size={13} />
                  Review &amp; apply
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {showDiff && (
        <div
          className="diff-scrim"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget && !saving) setShowDiff(false);
          }}
        >
          <div className="diff" role="dialog" aria-modal="true">
            <div className="diff__head">
              <div className="diff__title">
                Apply {dirty.length} change{dirty.length > 1 ? 's' : ''}?
              </div>
              <div className="diff__sub">
                {api.isLive()
                  ? 'PUT /api/settings/{id} · live'
                  : 'PUT /api/settings/{id} · ' + api.getSim() + ' (mock)'}{' '}
                · brain re-reads ≤ 5m
              </div>
            </div>
            <div className="diff__body">
              {dirty.map((r) => (
                <div key={r.id} className="diff-row">
                  <div className="diff-row__key">{r.key}</div>
                  <div className="diff-row__change">
                    <span className="diff-old">{fmt(r, r._orig)}</span>
                    <span className="diff-arrow">→</span>
                    <span className="diff-new">{fmt(r, r.value)}</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="diff__foot">
              <button
                className="mbtn mbtn--ghost"
                style={{ flex: 1, justifyContent: 'center', padding: 10 }}
                onClick={() => setShowDiff(false)}
                disabled={saving}
              >
                Cancel
              </button>
              <button
                className="mbtn mbtn--primary"
                style={{ flex: 1, justifyContent: 'center', padding: 10 }}
                onClick={apply}
                disabled={saving}
              >
                {saving ? (
                  <>
                    <span className="spinner" />
                    Applying…
                  </>
                ) : (
                  <>
                    <Icon name="check" size={13} />
                    Apply &amp; save
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

window.SettingsMode = SettingsMode;
