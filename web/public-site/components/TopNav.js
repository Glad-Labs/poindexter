'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { SITE_NAME } from '@/lib/site.config';

function SearchIcon({ className }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className={className || 'w-5 h-5'}
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
        clipRule="evenodd"
      />
    </svg>
  );
}

export default function TopNavigation() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const inputRef = useRef(null);
  const router = useRouter();

  useEffect(() => {
    if (searchOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [searchOpen]);

  function handleSubmit(e) {
    e.preventDefault();
    const trimmed = query.trim();
    if (trimmed) {
      router.push(`/search?q=${encodeURIComponent(trimmed)}`);
      setSearchOpen(false);
      setQuery('');
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Escape') {
      setSearchOpen(false);
      setQuery('');
    }
  }

  return (
    <>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-[100] focus:px-4 focus:py-2 focus:bg-cyan-600 focus:text-white focus:rounded-lg focus:outline-none"
      >
        Skip to main content
      </a>
      <header className="fixed top-0 left-0 right-0 z-50 bg-slate-950/80 border-b border-slate-800/50 backdrop-blur-xl">
        <nav
          aria-label="Main navigation"
          className="container mx-auto px-4 md:px-6 py-4 md:py-5 flex items-center justify-between"
        >
          <Link
            href="/"
            aria-label={`${SITE_NAME} — Home`}
            className="text-2xl font-bold text-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950 rounded"
          >
            GL
          </Link>
          <div className="flex gap-8 items-center">
            <Link
              href="/archive/1"
              className="text-slate-300 hover:text-cyan-300 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950 rounded px-1"
            >
              Articles
            </Link>
            <Link
              href="/about"
              className="text-slate-300 hover:text-cyan-300 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950 rounded px-1"
            >
              About
            </Link>
            <Link
              href="/product"
              className="text-cyan-400 hover:text-cyan-300 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950 rounded px-1 font-medium"
            >
              Premium
            </Link>
          </div>
          <div className="flex items-center gap-4">
            {searchOpen ? (
              <form onSubmit={handleSubmit} className="flex items-center">
                <input
                  ref={inputRef}
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Search articles..."
                  aria-label="Search articles"
                  className="w-40 md:w-56 px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-200 placeholder-slate-500 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400"
                />
              </form>
            ) : (
              <button
                onClick={() => setSearchOpen(true)}
                aria-label="Open search"
                className="text-slate-400 hover:text-cyan-300 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950 rounded p-1"
              >
                <SearchIcon />
              </button>
            )}
            <Link
              href="/archive/1"
              className="px-6 py-2.5 bg-cyan-600 text-white rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950"
            >
              Explore
            </Link>
          </div>
        </nav>
      </header>
    </>
  );
}
