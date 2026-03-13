'use client';

import Link from 'next/link';

export default function TopNavigation() {
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
            aria-label="Glad Labs — Home"
            className="text-2xl font-bold text-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950 rounded"
          >
            GL
          </Link>
          <div className="flex gap-8">
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
          </div>
          <Link
            href="/archive/1"
            className="px-6 py-2.5 bg-cyan-600 text-white rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-slate-950"
          >
            Explore
          </Link>
        </nav>
      </header>
    </>
  );
}
