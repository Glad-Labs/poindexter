export default function Loading() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900 animate-pulse">
      <div className="pt-20 pb-12 px-4 sm:px-6 lg:px-8">
        <div className="max-w-4xl mx-auto">
          <div className="h-6 bg-slate-700 rounded w-20 mb-4"></div>
          <div className="h-10 bg-slate-700 rounded w-64 mb-4"></div>
          <div className="h-5 bg-slate-700 rounded w-96 mb-6"></div>
        </div>
      </div>
      <div className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="max-w-4xl mx-auto grid grid-cols-1 md:grid-cols-2 gap-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="bg-slate-800/50 border border-slate-700 rounded-lg overflow-hidden"
            >
              <div className="w-full h-40 bg-slate-700"></div>
              <div className="p-4">
                <div className="h-5 bg-slate-700 rounded mb-3"></div>
                <div className="h-4 bg-slate-700 rounded w-5/6"></div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
