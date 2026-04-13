'use client';

import Link from 'next/link';
import { useState } from 'react';
import NewsletterModal from './NewsletterModal';
import { SITE_NAME } from '@/lib/site.config';

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
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2 py-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Home
              </Link>
              <Link
                href="/archive/1"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2 py-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Articles
              </Link>
              <Link
                href="/about"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2 py-2"
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
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2 py-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Privacy Policy
              </Link>
              <Link
                href="/legal/terms"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2 py-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Terms of Service
              </Link>
              <Link
                href="/legal/cookie-policy"
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2 py-2"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Cookie Policy
              </Link>
              <button
                onClick={() => {
                  localStorage.removeItem('cookieConsent');
                  localStorage.removeItem('cookieConsentDate');
                  window.location.reload();
                }}
                className="text-sm text-slate-300 hover:text-cyan-300 transition-colors duration-200 font-medium group inline-flex items-center gap-2 cursor-pointer"
                type="button"
              >
                <span className="w-1 h-1 rounded-full bg-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                Cookie Settings
              </button>
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
            <nav aria-label="Social media links" className="flex items-center gap-4 mt-6">
              <a
                href="https://x.com/_gladlabs"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Follow Glad Labs on X (Twitter)"
                className="text-slate-400 hover:text-cyan-300 transition-colors duration-200"
              >
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                </svg>
              </a>
              <a
                href="https://github.com/Glad-Labs"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Glad Labs on GitHub"
                className="text-slate-400 hover:text-cyan-300 transition-colors duration-200"
              >
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path fillRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clipRule="evenodd" />
                </svg>
              </a>
            </nav>
          </div>
        </div>

        {/* Gradient divider */}
        <div className="h-px bg-gradient-to-r from-transparent via-slate-700/30 to-transparent my-12" />

        {/* Bottom Footer */}
        <div className="flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="text-center md:text-left">
            <p className="text-sm text-slate-300 font-medium">
              &copy; {currentYear} {SITE_NAME}. All rights reserved.
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
