export default function Loading() {
  return (
    <div
      className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900"
      role="status"
    >
      <span className="sr-only">Loading articles...</span>
      <div className="pt-24 pb-16 px-4 sm:px-6 lg:px-8" aria-hidden="true">
        <div className="max-w-4xl mx-auto text-center animate-pulse">
          <div className="h-10 bg-slate-700 rounded w-64 mx-auto mb-4"></div>
          <div className="h-5 bg-slate-700 rounded w-80 mx-auto"></div>
        </div>
      </div>
      <div className="px-4 sm:px-6 lg:px-8 pb-20" aria-hidden="true">
        <div className="max-w-4xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 animate-pulse">
          {Array.from({ length: 9 }).map((_, i) => (
            <div
              key={i}
              className="bg-slate-800/50 border border-slate-700/50 rounded-xl overflow-hidden"
            >
              <div className="w-full aspect-video bg-slate-700"></div>
              <div className="p-6">
                <div className="h-5 bg-slate-700 rounded mb-3"></div>
                <div className="h-4 bg-slate-700 rounded w-5/6 mb-2"></div>
                <div className="h-4 bg-slate-700 rounded w-4/6"></div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
