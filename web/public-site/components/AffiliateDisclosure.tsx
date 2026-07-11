// Conditional FTC affiliate disclosure — rendered at the top of a post body
// when the exported post JSON carries `has_affiliate_links` (set by the static
// export when the content contains /go redirect links). Static server
// component (no 'use client'). Styled with the site's design tokens: a
// hairline-bordered surface note with a mono-upper accent eyebrow.

const DEFAULT_TEXT =
  'Some links in this article are affiliate links — if you sign up through them, Glad Labs may earn a commission at no extra cost to you.';

export function AffiliateDisclosure({ text }: { text?: string }) {
  return (
    <div
      role="note"
      aria-label="Affiliate disclosure"
      className="mb-8 px-4 py-3"
      style={{
        border: '1px solid var(--gl-hairline)',
        background: 'var(--gl-surface)',
      }}
    >
      <span
        className="gl-mono gl-mono--upper gl-mono--accent block"
        style={{ fontSize: '0.6875rem' }}
      >
        Affiliate Disclosure
      </span>
      <p className="gl-body gl-body--sm mt-1 text-[color:var(--gl-text-muted)]">
        {text && text.trim() ? text : DEFAULT_TEXT}
      </p>
    </div>
  );
}
