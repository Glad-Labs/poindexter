'use client';

import { useEffect, useRef } from 'react';

interface GiscusWrapperProps {
  postSlug: string;
  postTitle: string;
}

export function GiscusWrapper({ postSlug, postTitle }: GiscusWrapperProps) {
  const ref = useRef<HTMLDivElement>(null);
  // Defaults point at Glad-Labs/poindexter — the public OSS repo that
  // actually exists on GitHub + has Discussions enabled + has the
  // Giscus app installed. The previous default 'Glad-Labs/glad-labs-codebase'
  // pointed at an internal-only repo name; Giscus returned "not installed"
  // for every post until now. Category is General. Override via
  // NEXT_PUBLIC_GISCUS_REPO / _REPO_ID / _CATEGORY_ID in Vercel if you
  // want comments to land on a different repo.
  const repo = process.env.NEXT_PUBLIC_GISCUS_REPO || 'Glad-Labs/poindexter';
  const repoId = process.env.NEXT_PUBLIC_GISCUS_REPO_ID || 'R_kgDOR-pAaA';
  const categoryId =
    process.env.NEXT_PUBLIC_GISCUS_CATEGORY_ID || 'DIC_kwDOR-pAaM4C7ZMV';
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
