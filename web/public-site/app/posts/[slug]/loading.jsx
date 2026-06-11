export default function Loading() {
  return (
    <div
      className="min-h-screen bg-gradient-to-b from-[var(--gl-base)] via-[var(--gl-surface)] to-[var(--gl-base)]"
      role="status"
    >
      <span className="sr-only">Loading article...</span>
      <div
        className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-20 animate-pulse"
        aria-hidden="true"
      >
        {/* Header skeleton */}
        <div className="mb-8">
          <div className="h-4 bg-[var(--gl-surface-2)] rounded w-24 mb-6"></div>
          <div className="h-10 bg-[var(--gl-surface-2)] rounded w-3/4 mb-4"></div>
          <div className="h-6 bg-[var(--gl-surface-2)] rounded w-1/2 mb-6"></div>
          <div className="flex gap-3">
            <div className="h-4 bg-[var(--gl-surface-2)] rounded w-24"></div>
            <div className="h-4 bg-[var(--gl-surface-2)] rounded w-20"></div>
          </div>
        </div>
        {/* Featured image skeleton */}
        <div className="w-full aspect-video bg-[var(--gl-surface-2)] rounded-xl mb-12"></div>
        {/* Content skeleton — fixed widths to avoid hydration mismatch.
            Math.random() at render time produces different markup on
            server vs client and React 18 throws a hydration warning. */}
        <div className="space-y-4">
          {[92, 88, 95, 86, 91, 97, 89, 94].map((widthPercent, i) => (
            <div
              key={i}
              className="h-4 bg-[var(--gl-surface-2)] rounded"
              style={{ width: `${widthPercent}%` }}
            ></div>
          ))}
        </div>
      </div>
    </div>
  );
}
