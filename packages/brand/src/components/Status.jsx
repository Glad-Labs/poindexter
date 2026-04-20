/*
  <Status> — icon + label status indicator.
  Color reinforces the signal; the ✓/⚠/✕ glyph carries it. Matt is
  red-green colorblind — color-alone status indicators literally do
  not register to him. Always pass `kind` to get the right glyph.
*/
const GLYPHS = {
  ok: '✓',
  warn: '⚠',
  err: '✕',
};

export function Status({ kind = 'ok', children, className = '', ...rest }) {
  const glyph = GLYPHS[kind] || GLYPHS.ok;
  return (
    <span className={`gl-status gl-status--${kind} ${className}`} {...rest}>
      <span aria-hidden="true" className="gl-status__glyph">
        {glyph}
      </span>
      <span>{children}</span>
    </span>
  );
}
