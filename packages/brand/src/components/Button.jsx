import { forwardRef } from 'react';

/*
  <Button> — E3 buttons. Zero border-radius, mono uppercase, three roles:
    • primary — cyan fill, dark text (CTAs: "Get the guide")
    • secondary — cyan outline, transparent fill (secondary CTAs)
    • ghost — neutral border, text color (tertiary / "Skip" / "Cancel")

  Forwards refs so callers can manage focus (e.g. focus-return from a
  modal dialog). Works with `as={Link}` for router links too.
*/
export const Button = forwardRef(function Button(
  {
    variant = 'primary',
    as: Tag = 'button',
    children,
    className = '',
    ...rest
  },
  ref
) {
  const variantClass =
    {
      primary: 'gl-btn gl-btn--primary',
      secondary: 'gl-btn gl-btn--secondary',
      ghost: 'gl-btn gl-btn--ghost',
    }[variant] || 'gl-btn';

  return (
    <Tag
      ref={ref}
      className={`${variantClass} gl-focus-ring ${className}`}
      {...rest}
    >
      {children}
    </Tag>
  );
});
