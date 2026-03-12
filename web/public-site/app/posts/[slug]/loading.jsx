export default function Loading() {
  return (
    <div
      className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900"
      role="status"
    >
      <span className="sr-only">Loading article...</span>
      <div
        className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-20 animate-pulse"
        aria-hidden="true"
      >
        {/* Header skeleton */}
        <div className="mb-8">
          <div className="h-4 bg-slate-700 rounded w-24 mb-6"></div>
          <div className="h-10 bg-slate-700 rounded w-3/4 mb-4"></div>
          <div className="h-6 bg-slate-700 rounded w-1/2 mb-6"></div>
          <div className="flex gap-3">
            <div className="h-4 bg-slate-700 rounded w-24"></div>
            <div className="h-4 bg-slate-700 rounded w-20"></div>
          </div>
        </div>
        {/* Featured image skeleton */}
        <div className="w-full aspect-video bg-slate-700 rounded-xl mb-12"></div>
        {/* Content skeleton */}
        <div className="space-y-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div
              key={i}
              className="h-4 bg-slate-700 rounded"
              style={{ width: `${85 + Math.random() * 15}%` }}
            ></div>
          ))}
        </div>
      </div>
    </div>
  );
}
