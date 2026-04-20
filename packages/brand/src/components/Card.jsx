/*
  <Card> — surface card with left-cyan-tick and hairline border.
  Compose metadata + title + body inside. Provides the "industrial"
  E3 look without needing ad-hoc classnames at callsites.
*/
export function Card({ accent = 'cyan', children, className = '', ...rest }) {
  const accentClass =
    {
      cyan: 'gl-tick-left',
      amber: 'gl-tick-left gl-tick-left--amber',
      mint: 'gl-tick-left gl-tick-left--mint',
    }[accent] || 'gl-tick-left';
  return (
    <div className={`gl-card ${accentClass} ${className}`} {...rest}>
      {children}
    </div>
  );
}

Card.Meta = function CardMeta({ children, className = '' }) {
  return (
    <div className={`gl-card__meta gl-mono gl-mono--upper ${className}`}>
      {children}
    </div>
  );
};

Card.Tag = function CardTag({ children, className = '' }) {
  return (
    <span className={`gl-card__tag gl-mono--amber ${className}`}>
      {children}
    </span>
  );
};

Card.Title = function CardTitle({ as: Tag = 'h3', children, className = '' }) {
  return <Tag className={`gl-h3 ${className}`}>{children}</Tag>;
};

Card.Body = function CardBody({ children, className = '' }) {
  return <p className={`gl-body gl-body--sm ${className}`}>{children}</p>;
};
