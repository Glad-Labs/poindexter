/*
  <Logo> — the Glad Labs "GL" word-mark.

  A thin presentational component that renders the two-letter mark with the
  canonical display-font stack, weight, tracking, case, and accent colour.
  Stateless and side-effect-free — pass any extra HTML attributes you need
  (id, className, onClick, …) and they flow straight through to the element.

  Usage:
    import { Logo } from '@glad-labs/brand';
    <Logo />

  The component intentionally renders a plain <span> so it can be dropped
  inside any wrapper (<Link>, <a>, <button>, <div>, …) without altering
  the DOM hierarchy.  For a linked logo use the wrapper at the call site:

    <Link href="/" aria-label="Glad Labs — Home" className="gl-focus-ring">
      <Logo />
    </Link>
*/
export function Logo({ className = '', ...rest }) {
  return (
    <span
      aria-hidden="true"
      className={className}
      style={{
        fontFamily: 'var(--gl-font-display)',
        fontWeight: 700,
        fontSize: '1.25rem',
        letterSpacing: '0.05em',
        color: 'var(--gl-cyan)',
        textTransform: 'uppercase',
      }}
      {...rest}
    >
      GL
    </span>
  );
}
