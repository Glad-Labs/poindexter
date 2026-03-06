'use client';

interface GiscusCommentsProps {
  postSlug: string;
  postTitle: string;
}

export default function GiscusComments({
  postSlug,
  postTitle,
}: GiscusCommentsProps) {
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-6">
      <h3 className="text-lg font-semibold text-slate-300 mb-4">Comments</h3>
      <div className="text-slate-400 text-sm">
        <p>Comments coming soon. Post: {postTitle}</p>
      </div>
    </div>
  );
}
