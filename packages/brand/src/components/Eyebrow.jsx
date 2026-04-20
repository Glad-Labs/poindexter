/*
  <Eyebrow> — the `// GLAD LABS · CONTEXT` signature pattern.
  Sits above display headlines. Mono + uppercase + cyan.
*/
export function Eyebrow({ children, className = '', ...rest }) {
  return (
    <span className={`gl-eyebrow ${className}`} {...rest}>
      // {children}
    </span>
  );
}
