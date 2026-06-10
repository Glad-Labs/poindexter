/**
 * Route-level loading boundary.
 *
 * Audit #7: the old version streamed a full-screen pre-E3 slate gradient
 * with a spinner and literal "Loading..." text into the initial HTML
 * payload. Two problems:
 *   1. Off-brand — slate/cyan-400 are the previous design, not E3 tokens.
 *   2. A full-viewport takeover makes a brief stream flash feel like a
 *      page-level failure.
 *
 * This version is a minimal, on-brand mono stamp on the token background.
 * It reads as the terminal aesthetic doing its job rather than a broken
 * page, and reduced-motion users get a static stamp instead of a pulse.
 */
export default function Loading() {
  return (
    <div
      className="gl-atmosphere min-h-screen flex items-center justify-center"
      style={{ background: 'var(--gl-base)' }}
    >
      <p
        role="status"
        className="gl-mono gl-mono--upper motion-safe:animate-pulse"
        style={{ color: 'var(--gl-text-muted)', letterSpacing: '0.18em' }}
      >
        <span aria-hidden style={{ color: 'var(--gl-cyan)' }}>{'// '}</span>
        Fetching posts
      </p>
    </div>
  );
}
