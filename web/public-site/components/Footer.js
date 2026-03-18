'use client';

import Link from 'next/link';
import { useState } from 'react';
import NewsletterModal from './NewsletterModal';

const Footer = () => {
  // currentYear is computed at render time. suppressHydrationWarning on the
  // containing element tells Next.js to accept minor mismatches (e.g. a
  // year-boundary edge case) without throwing a hydration error — no mounted
  // state boilerplate required (issue #96).
  const currentYear = new Date().getFullYear();
  const [isNewsletterModalOpen, setIsNewsletterModalOpen] = useState(false);

  return (
    <footer
      className="relative bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 border-t border-slate-800/50 mt-auto overflow-hidden"
      role="contentinfo"
      suppressHydrationWarning
    >
      {/* Animated gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-transparent to-transparent pointer-events-none" />

      {/* Decorative glow elements */}
      <div className="absolute top-0 right-0 w-96 h-96 bg-gradient-to-bl from-cyan-500/5 to-transparent rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-0 left-0 w-96 h-96 bg-gradient-to-tr from-blue-500/5 to-transparent rounded-full blur-3xl pointer-events-none" />

      <div className="relative container mx-auto px-4 sm:px-6 lg:px-8 py-16 md:py-20">
        {/* Main content grid */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8 md:gap-12 mb-16">
          {/* Brand Column */}
          <div className="col-span-1 md:col-span-1">
            <Link
              href="/"
              className="inline-flex items-center gap-2 group mb-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 rounded-lg px-2 py-1 transition-all"
            >
              <div className="text-3xl font-black bg-gradient-to-r from-cyan-400 via-blue-500 to-violet-500 bg-clip-text text-transparent group-hover:opacity-80 transition-all duration-300">
                GL
              </div>
            </Link>
            <p className="text-sm text-slate-300 leading-relaxed mb-6">
              Transforming digital innovation with AI-powered insights and
              autonomous intelligence.
            </p>
            <p className="text-xs text-slate-400">
              Building the future, one algorithm at a time.
            </p>
          </div>

          {/* Explore Column */}
          <div>
            <h3 className="text-sm font-semibold text-white mb-6 uppercase tracking-widest flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-gradient-to-r from-cyan-400 to-blue-500" />
              Explore
            </h3>
            <nav
              aria-label="Explore navigation"
              className="flex flex-col space-y-3"
            >
              <Link
                href="/"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Home
              </Link>
              <Link
                href="/archive/1"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Articles
              </Link>
              <Link
                href="/about"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                About Us
              </Link>
            </nav>
          </div>

          {/* Legal Column */}
          <div>
            <h3 className="text-sm font-semibold text-white mb-6 uppercase tracking-widest flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-gradient-to-r from-cyan-400 to-blue-500" />
              Legal
            </h3>
            <nav
              aria-label="Legal navigation"
              className="flex flex-col space-y-3"
            >
              <Link
                href="/legal/privacy"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Privacy Policy
              </Link>
              <Link
                href="/legal/terms"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Terms of Service
              </Link>
              <Link
                href="/legal/cookie-policy"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Cookie Policy
              </Link>
            </nav>
          </div>

          {/* Connect Column */}
          <div>
            <h3 className="text-sm font-semibold text-white mb-6 uppercase tracking-widest flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-gradient-to-r from-cyan-400 to-blue-500" />
              Connect
            </h3>
            <p className="text-sm text-slate-300 mb-6 leading-relaxed">
              Stay updated with the latest AI insights and innovations.
            </p>
            <button
              onClick={() => setIsNewsletterModalOpen(true)}
              className="inline-flex items-center gap-2 px-5 py-3 bg-gradient-to-r from-cyan-500/20 to-blue-600/20 border border-cyan-500/30 text-cyan-300 rounded-lg font-medium text-sm hover:bg-gradient-to-r hover:from-cyan-500/30 hover:to-blue-600/30 hover:border-cyan-500/50 transition-all duration-200 cursor-pointer"
            >
              Get Updates
              <span>→</span>
            </button>
          </div>
        </div>

        {/* Gradient divider */}
        <div className="h-px bg-gradient-to-r from-transparent via-slate-700/30 to-transparent my-12" />

        {/* Bottom Footer */}
        <div className="flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="text-center md:text-left">
            <p className="text-sm text-slate-300 font-medium">
              &copy; {currentYear} Glad Labs. All rights reserved.
            </p>
          </div>
          <div className="flex items-center gap-6">
            <p className="text-xs text-slate-400">
              Built for innovation, powered by AI.
            </p>
          </div>
        </div>
      </div>

      <NewsletterModal
        isOpen={isNewsletterModalOpen}
        onClose={() => setIsNewsletterModalOpen(false)}
      />
    </footer>
  );
};

export default Footer;
