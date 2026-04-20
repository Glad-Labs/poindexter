/*
  <Display> — uppercase Space Grotesk 700 hero headline.
  Use <Display.Accent> inside for amber-highlighted words.

    <Display>
      Ship an <Display.Accent>AI writer.</Display.Accent>
    </Display>
*/
export function Display({
  as: Tag = 'h1',
  xl = false,
  children,
  className = '',
  ...rest
}) {
  return (
    <Tag
      className={`gl-display ${xl ? 'gl-display--xl' : ''} ${className}`}
      {...rest}
    >
      {children}
    </Tag>
  );
}

Display.Accent = function DisplayAccent({ children }) {
  return <em className="gl-accent">{children}</em>;
};
