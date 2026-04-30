export default function Loading() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center">
      <div className="text-center" role="status" aria-label="Loading content">
        <div
          className="inline-block animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-cyan-400 mb-4"
          aria-hidden="true"
        ></div>
        <p className="text-slate-400">Loading...</p>
      </div>
    </div>
  );
}
