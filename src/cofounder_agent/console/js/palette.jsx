/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — Command palette (⌘K).
   Keyboard-first ops. Commands are built from live state in app.jsx.
   ────────────────────────────────────────────────────────────── */

function CommandPalette({ open, commands, onClose }) {
  const [query, setQuery] = React.useState('');
  const [sel, setSel] = React.useState(0);
  const inputRef = React.useRef(null);
  const listRef = React.useRef(null);

  React.useEffect(() => {
    if (open) {
      setQuery('');
      setSel(0);
      setTimeout(() => inputRef.current && inputRef.current.focus(), 30);
    }
  }, [open]);

  const filtered = React.useMemo(() => {
    const q = query.toLowerCase().trim();
    if (!q) return commands;
    return commands.filter((c) =>
      (c.label + ' ' + (c.hint || '') + ' ' + c.group).toLowerCase().includes(q)
    );
  }, [query, commands]);

  React.useEffect(() => {
    setSel(0);
  }, [query]);

  // group preserving order
  const groups = React.useMemo(() => {
    const g = [];
    filtered.forEach((c) => {
      let grp = g.find((x) => x.name === c.group);
      if (!grp) {
        grp = { name: c.group, items: [] };
        g.push(grp);
      }
      grp.items.push(c);
    });
    return g;
  }, [filtered]);

  const flat = filtered;

  const onKey = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, flat.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const c = flat[sel];
      if (c) {
        onClose();
        c.run();
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  React.useEffect(() => {
    if (!listRef.current) return;
    const el = listRef.current.querySelector('.is-sel');
    if (el) el.scrollIntoView ? el.scrollIntoView({ block: 'nearest' }) : null;
  }, [sel]);

  if (!open) return null;
  let idx = -1;
  return (
    <div
      className="cmdk-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div className="cmdk__input-row">
          <Icon name="search" size={18} />
          <input
            ref={inputRef}
            className="cmdk__input"
            placeholder="Type a command — approve, restart, probe, go to…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
          />
          <span className="cmdk__hint mono">{flat.length}</span>
        </div>
        <div className="cmdk__list" ref={listRef}>
          {groups.length === 0 && (
            <div className="cmdk__empty">No commands match “{query}”.</div>
          )}
          {groups.map((grp) => (
            <div key={grp.name}>
              <div className="cmdk__group">{grp.name}</div>
              {grp.items.map((c) => {
                idx++;
                const isSel = idx === sel;
                const mySel = idx;
                return (
                  <div
                    key={c.id}
                    className={`cmdk__item ${c.danger ? 'danger' : ''} ${isSel ? 'is-sel' : ''}`}
                    onMouseEnter={() => setSel(mySel)}
                    onClick={() => {
                      onClose();
                      c.run();
                    }}
                  >
                    <span className="cmdk__glyph">
                      <Icon name={c.icon || 'bolt'} size={13} />
                    </span>
                    <span className="cmdk__label">{c.label}</span>
                    {c.hint && <span className="cmdk__hint">{c.hint}</span>}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div className="cmdk__foot">
          <span>
            <kbd>↑↓</kbd>navigate
          </span>
          <span>
            <kbd>↵</kbd>run
          </span>
          <span>
            <kbd>esc</kbd>close
          </span>
        </div>
      </div>
    </div>
  );
}

window.CommandPalette = CommandPalette;
