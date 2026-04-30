'use client';

import { useEffect, useRef } from 'react';

interface GiscusWrapperProps {
  postSlug: string;
  postTitle: string;
}

export function GiscusWrapper({ postSlug, postTitle }: GiscusWrapperProps) {
  const ref = useRef<HTMLDivElement>(null);
  // No silent defaults — config lives in Vercel env vars only
  // (NEXT_PUBLIC_GISCUS_REPO / _REPO_ID / _CATEGORY_ID). If any of the
  // three is missing, the component renders the "not configured"
  // placeholder below instead of silently picking a hardcoded repo and
  // misrouting comments to the wrong place.
  const repo = process.env.NEXT_PUBLIC_GISCUS_REPO;
  const repoId = process.env.NEXT_PUBLIC_GISCUS_REPO_ID;
  const categoryId = process.env.NEXT_PUBLIC_GISCUS_CATEGORY_ID;
  const enabled = process.env.NEXT_PUBLIC_ENABLE_COMMENTS !== 'false';

  useEffect(() => {
    if (!ref.current || !repo || !repoId || !categoryId || !enabled) return;
    if (ref.current.querySelector('.giscus')) return;
    const script = document.createElement('script');
    script.src = 'https://giscus.app/client.js';
    script.setAttribute('data-repo', repo);
    script.setAttribute('data-repo-id', repoId);
    script.setAttribute('data-category-id', categoryId);
    script.setAttribute('data-mapping', 'pathname');
    script.setAttribute('data-strict', '0');
    script.setAttribute('data-reactions-enabled', '1');
    script.setAttribute('data-emit-metadata', '0');
    script.setAttribute('data-input-position', 'bottom');
    script.setAttribute('data-theme', 'preferred_color_scheme');
    script.setAttribute('data-lang', 'en');
    script.setAttribute('data-loading', 'lazy');
    script.setAttribute('crossorigin', 'anonymous');
    script.async = true;
    ref.current.appendChild(script);
  }, [repo, repoId, categoryId, enabled, postSlug]);

  if (!enabled) return null;
  if (!repo || !repoId || !categoryId) {
    return (
      <div className="py-12">
        <h2 className="text-2xl font-bold text-white mb-6">Discussion</h2>
        <p className="text-slate-400 text-sm">
          Comments are not yet configured. Enable GitHub Discussions on the repo
          and set the GISCUS env vars in Vercel.
        </p>
      </div>
    );
  }
  return (
    <div className="py-12">
      <h2 className="text-2xl font-bold text-white mb-6">Discussion</h2>
      <div ref={ref} />
    </div>
  );
}
