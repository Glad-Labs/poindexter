'use client';

import { useEffect, useState } from 'react';

interface ShareButtonsProps {
  title: string;
  description?: string;
  slug: string;
  siteUrl?: string;
}

export function ShareButtons({
  title,
  description,
  slug,
  siteUrl = 'https://glad-labs.com',
}: ShareButtonsProps) {
  const [mounted, setMounted] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return null; // Avoid hydration mismatch
  }

  const postUrl = `${siteUrl}/posts/${slug}`;
  const encodedUrl = encodeURIComponent(postUrl);
  const encodedTitle = encodeURIComponent(title);

  const shareLinks = {
    twitter: `https://twitter.com/intent/tweet?url=${encodedUrl}&text=${encodedTitle}&via=GladLabsAI`,
    linkedin: `https://www.linkedin.com/sharing/share-offsite/?url=${encodedUrl}`,
    facebook: `https://www.facebook.com/sharer/sharer.php?u=${encodedUrl}`,
  };

  const handleShare = (platform: string) => {
    const url = shareLinks[platform as keyof typeof shareLinks];
    window.open(url, '_blank', 'width=600,height=400');
  };

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(postUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API not available -- fail silently
    }
  };

  return (
    <div className="flex items-center gap-4">
      <span className="text-sm font-medium text-slate-400">Share:</span>

      {/* Twitter */}
      <button
        onClick={() => handleShare('twitter')}
        className="p-2 rounded-lg bg-slate-800/50 hover:bg-sky-500/20 text-sky-400 hover:text-sky-300 transition-all duration-300"
        aria-label="Share on Twitter"
      >
        <svg
          className="w-5 h-5"
          fill="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path d="M23 3a10.9 10.9 0 01-3.14 1.53 4.48 4.48 0 00-7.86 3v1A10.66 10.66 0 013 4s-4 9 5 13a11.64 11.64 0 01-7 2s9 5 20 5a9.5 9.5 0 00-9-5.5c4.75 2.25 7-7 7-7a10.6 10.6 0 01-9.5 5M21 20.5a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z" />
        </svg>
      </button>

      {/* LinkedIn */}
      <button
        onClick={() => handleShare('linkedin')}
        className="p-2 rounded-lg bg-slate-800/50 hover:bg-blue-500/20 text-blue-400 hover:text-blue-300 transition-all duration-300"
        aria-label="Share on LinkedIn"
      >
        <svg
          className="w-5 h-5"
          fill="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path d="M16 8a6 6 0 016 6v7h-4v-7a2 2 0 00-2-2 2 2 0 00-2 2v7h-4v-7a6 6 0 016-6zM2 9h4v12H2z" />
          <circle cx="4" cy="4" r="2" />
        </svg>
      </button>

      {/* Facebook */}
      <button
        onClick={() => handleShare('facebook')}
        className="p-2 rounded-lg bg-slate-800/50 hover:bg-indigo-500/20 text-indigo-400 hover:text-indigo-300 transition-all duration-300"
        aria-label="Share on Facebook"
      >
        <svg
          className="w-5 h-5"
          fill="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path d="M18 2h-3a6 6 0 00-6 6v3H7v4h2v8h4v-8h3l1-4h-4V8a2 2 0 012-2h3z" />
        </svg>
      </button>

      {/* Copy Link */}
      <button
        onClick={handleCopyLink}
        className="p-2 rounded-lg bg-slate-800/50 hover:bg-cyan-500/20 text-cyan-400 hover:text-cyan-300 transition-all duration-300"
        aria-label={
          copied ? 'Link copied to clipboard' : 'Copy link to clipboard'
        }
      >
        <svg
          className="w-5 h-5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
          />
        </svg>
      </button>
      <span aria-live="polite" className="sr-only">
        {copied ? 'Link copied to clipboard' : ''}
      </span>
    </div>
  );
}
