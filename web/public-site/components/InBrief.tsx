// The post's answer-first "In brief" summary (posts.metadata.summary, written
// after publishing by the backend's GeneratePostSummariesJob), shown above the
// article body. Readers get the finding first; AI answer engines get a passage
// they can quote whole. Styled like the affiliate disclosure note: a
// hairline-bordered surface with a mono-upper accent eyebrow.

export function InBrief({ summary }: { summary?: string | null }) {
  const text = (summary || '').trim();
  if (!text) return null;
  return (
    <aside
      aria-label="In brief"
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
        In brief
      </span>
      <p className="gl-body mt-1">{text}</p>
    </aside>
  );
}
