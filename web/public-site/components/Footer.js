'use client';

import Link from 'next/link';
import { useState } from 'react';
import { Button, Eyebrow } from '@glad-labs/brand';
import NewsletterModal from './NewsletterModal';
import { SITE_NAME } from '@/lib/site.config';

const FOOTER_LINK_CLASS =
  'gl-mono gl-mono--upper gl-focus-ring inline-block py-1 transition-colors hover:text-[color:var(--gl-cyan)]';

const Footer = () => {
  // currentYear is computed at render time. suppressHydrationWarning on the
  // containing element tells Next.js to accept minor mismatches (e.g. a
  // year-boundary edge case) without throwing a hydration error — no mounted
  // state boilerplate required (issue #96).
  const currentYear = new Date().getFullYear();
  const [isNewsletterModalOpen, setIsNewsletterModalOpen] = useState(false);

  return (
    <footer
      className="relative mt-auto"
      role="contentinfo"
      suppressHydrationWarning
      style={{
        background: 'var(--gl-base)',
        borderTop: '1px solid var(--gl-hairline)',
      }}
    >
      <div className="container mx-auto px-4 sm:px-6 lg:px-8 py-14 md:py-16">
        {/* Main grid */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-10 mb-12">
          {/* Brand */}
          <div>
            <Link
              href="/"
              aria-label={`${SITE_NAME} — Home`}
              className="gl-focus-ring inline-block mb-5"
              style={{
                fontFamily: 'var(--gl-font-display)',
                fontWeight: 700,
                fontSize: '1.25rem',
                letterSpacing: '0.05em',
                color: 'var(--gl-cyan)',
                textTransform: 'uppercase',
              }}
            >
              GL
            </Link>
            <p className="gl-body gl-body--sm mb-3">
              AI, hardware, and the edges where they meet. Locally-published,
              human-reviewed, free to read.
            </p>
            <p className="gl-mono gl-mono--upper opacity-60" style={{ fontSize: '0.6875rem' }}>
              Built for innovation · Powered by AI
            </p>
          </div>

          {/* Explore */}
          <div>
            <Eyebrow>EXPLORE</Eyebrow>
            <nav
              aria-label="Explore navigation"
              className="flex flex-col gap-1 mt-4"
            >
              <Link href="/" className={FOOTER_LINK_CLASS}>
                Home
              </Link>
              <Link href="/archive/1" className={FOOTER_LINK_CLASS}>
                Articles
              </Link>
              <Link href="/about" className={FOOTER_LINK_CLASS}>
                About
              </Link>
            </nav>
          </div>

          {/* Legal */}
          <div>
            <Eyebrow>LEGAL</Eyebrow>
            <nav
              aria-label="Legal navigation"
              className="flex flex-col gap-1 mt-4"
            >
              <Link href="/legal/privacy" className={FOOTER_LINK_CLASS}>
                Privacy Policy
              </Link>
              <Link href="/legal/terms" className={FOOTER_LINK_CLASS}>
                Terms of Service
              </Link>
              <Link href="/legal/cookie-policy" className={FOOTER_LINK_CLASS}>
                Cookie Policy
              </Link>
              <button
                type="button"
                onClick={() => {
                  localStorage.removeItem('cookieConsent');
                  localStorage.removeItem('cookieConsentDate');
                  window.location.reload();
                }}
                className={`${FOOTER_LINK_CLASS} text-left cursor-pointer bg-transparent border-0 p-0`}
              >
                Cookie Settings
              </button>
            </nav>
          </div>

          {/* Connect */}
          <div>
            <Eyebrow>CONNECT</Eyebrow>
            <p className="gl-body gl-body--sm mt-4 mb-5">
              Updates when something new ships. No noise.
            </p>
            <Button
              variant="secondary"
              onClick={() => setIsNewsletterModalOpen(true)}
            >
              Get updates →
            </Button>
            <nav
              aria-label="Social media links"
              className="flex items-center gap-4 mt-6"
            >
              <a
                href="https://x.com/_gladlabs"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Follow Glad Labs on X (Twitter)"
                className="gl-focus-ring transition-colors hover:text-[color:var(--gl-cyan)]"
                style={{ color: 'var(--gl-text-muted)' }}
              >
                <svg
                  className="w-5 h-5"
                  fill="currentColor"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                </svg>
              </a>
              <a
                href="https://github.com/Glad-Labs"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Glad Labs on GitHub"
                className="gl-focus-ring transition-colors hover:text-[color:var(--gl-cyan)]"
                style={{ color: 'var(--gl-text-muted)' }}
              >
                <svg
                  className="w-5 h-5"
                  fill="currentColor"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path
                    fillRule="evenodd"
                    d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                    clipRule="evenodd"
                  />
                </svg>
              </a>
            </nav>
          </div>
        </div>

        {/* Hairline divider */}
        <div
          className="h-px w-full"
          style={{ background: 'var(--gl-hairline)' }}
        />

        {/* Bottom row */}
        <div className="flex flex-col md:flex-row justify-between items-center gap-3 pt-6">
          <p className="gl-mono gl-mono--upper text-xs opacity-80">
            © {currentYear} {SITE_NAME} · All rights reserved
          </p>
          <p
            className="gl-mono gl-mono--upper opacity-50"
            style={{ fontSize: '0.6875rem' }}
          >
            // ONE PERSON · LOCAL AI · UNLIMITED SCALE
          </p>
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
