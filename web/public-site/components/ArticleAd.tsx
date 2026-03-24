'use client';

import AdUnit from './AdUnit';

/**
 * ArticleAd — Client component wrapper for AdUnit in blog posts.
 * Needed because AdUnit uses client-side APIs (useEffect, window)
 * and blog post pages are server components in App Router.
 */
export default function ArticleAd() {
  return (
    <div className="px-4 sm:px-6 lg:px-8 pb-12">
      <div className="max-w-4xl mx-auto">
        <AdUnit
          slot={process.env.NEXT_PUBLIC_ADSENSE_SLOT_ID || ''}
          format="horizontal"
          className="my-4"
        />
      </div>
    </div>
  );
}
