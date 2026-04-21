'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
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

function HamburgerIcon({ open }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="w-6 h-6"
      aria-hidden="true"
    >
      {open ? (
        <>
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </>
      ) : (
        <>
          <line x1="3" y1="7" x2="21" y2="7" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="17" x2="21" y2="17" />
        </>
      )}
    </svg>
  );
}

export default function TopNavigation() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState('');
  const inputRef = useRef(null);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (searchOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [searchOpen]);

  // Close mobile menu whenever the route changes (user tapped a link).
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  // Lock body scroll while mobile menu is open.
  useEffect(() => {
    if (menuOpen) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [menuOpen]);

  function handleSubmit(e) {
    e.preventDefault();
    const trimmed = query.trim();
    if (trimmed) {
      router.push(`/search?q=${encodeURIComponent(trimmed)}`);
      setSearchOpen(false);
      setMenuOpen(false);
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
      <header
        className="fixed top-0 left-0 right-0 z-50"
        style={{
          background: 'rgba(7, 10, 15, 0.8)',
          borderBottom: '1px solid var(--gl-hairline)',
          backdropFilter: 'blur(12px)',
        }}
      >
        <nav
          aria-label="Main navigation"
          className="container mx-auto px-4 md:px-6 py-4 md:py-5 flex items-center justify-between"
        >
          <Link
            href="/"
            aria-label={`${SITE_NAME} — Home`}
            className="gl-focus-ring"
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

          {/* Desktop nav + CTAs — hidden on mobile */}
          <div
            className="hidden md:flex gap-8 items-center gl-mono gl-mono--upper"
            style={{ fontSize: 'var(--gl-size-eyebrow)' }}
          >
            <Link href="/archive/1" className="gl-focus-ring nav-link">
              Articles
            </Link>
            <Link href="/about" className="gl-focus-ring nav-link">
              About
            </Link>
            <Link
              href="/product"
              className="gl-focus-ring nav-link"
              style={{ color: 'var(--gl-amber)' }}
            >
              Premium
            </Link>
            <Link
              href="/claude-templates"
              className="gl-focus-ring nav-link"
              style={{ color: 'var(--gl-mint)' }}
            >
              Templates
            </Link>
          </div>
          <div className="hidden md:flex items-center gap-4">
            {searchOpen ? (
              <form onSubmit={handleSubmit} className="flex items-center">
                <input
                  ref={inputRef}
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Search..."
                  aria-label="Search articles"
                  className="gl-focus-ring"
                  style={{
                    width: '14rem',
                    padding: '0.45rem 0.75rem',
                    background: 'var(--gl-surface)',
                    border: '1px solid var(--gl-cyan-border)',
                    borderRadius: 0,
                    color: 'var(--gl-text)',
                    fontFamily: 'var(--gl-font-mono)',
                    fontSize: '0.8125rem',
                  }}
                />
              </form>
            ) : (
              <button
                onClick={() => setSearchOpen(true)}
                aria-label="Open search"
                className="gl-focus-ring"
                style={{
                  color: 'var(--gl-text-muted)',
                  background: 'transparent',
                  border: 0,
                  padding: '0.25rem',
                  cursor: 'pointer',
                }}
              >
                <SearchIcon />
              </button>
            )}
            <Link
              href="/archive/1"
              className="gl-btn gl-btn--primary gl-focus-ring"
            >
              Explore
            </Link>
          </div>

          {/* Mobile hamburger — hidden on md+ */}
          <button
            type="button"
            className="md:hidden gl-focus-ring"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
            aria-controls="mobile-nav-panel"
            onClick={() => setMenuOpen((v) => !v)}
            style={{
              color: 'var(--gl-text)',
              background: 'transparent',
              border: 0,
              padding: '0.25rem',
              cursor: 'pointer',
            }}
          >
            <HamburgerIcon open={menuOpen} />
          </button>
        </nav>

        {/* Mobile panel — slides down under the header */}
        {menuOpen && (
          <div
            id="mobile-nav-panel"
            className="md:hidden"
            style={{
              background: 'var(--gl-base)',
              borderTop: '1px solid var(--gl-hairline)',
              borderBottom: '1px solid var(--gl-hairline)',
            }}
          >
            <div className="container mx-auto px-4 py-5 flex flex-col gap-5">
              {/* Search form — always visible in the open panel */}
              <form onSubmit={handleSubmit} className="flex items-center gap-2">
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search…"
                  aria-label="Search articles"
                  className="gl-focus-ring flex-1"
                  style={{
                    padding: '0.6rem 0.75rem',
                    background: 'var(--gl-surface)',
                    border: '1px solid var(--gl-hairline)',
                    borderRadius: 0,
                    color: 'var(--gl-text)',
                    fontFamily: 'var(--gl-font-mono)',
                    fontSize: '0.8125rem',
                  }}
                />
                <button
                  type="submit"
                  aria-label="Search"
                  className="gl-focus-ring"
                  style={{
                    padding: '0.6rem 0.75rem',
                    background: 'var(--gl-cyan)',
                    color: '#0a0a0a',
                    border: 0,
                    borderRadius: 0,
                    cursor: 'pointer',
                  }}
                >
                  <SearchIcon />
                </button>
              </form>

              {/* Nav links — mono uppercase column */}
              <div
                className="flex flex-col gl-mono gl-mono--upper"
                style={{ fontSize: 'var(--gl-size-eyebrow)' }}
              >
                <Link
                  href="/archive/1"
                  className="gl-focus-ring nav-link py-3"
                  style={{ borderBottom: '1px solid var(--gl-hairline)' }}
                >
                  Articles
                </Link>
                <Link
                  href="/about"
                  className="gl-focus-ring nav-link py-3"
                  style={{ borderBottom: '1px solid var(--gl-hairline)' }}
                >
                  About
                </Link>
                <Link
                  href="/product"
                  className="gl-focus-ring nav-link py-3"
                  style={{
                    color: 'var(--gl-amber)',
                    borderBottom: '1px solid var(--gl-hairline)',
                  }}
                >
                  Premium
                </Link>
                <Link
                  href="/claude-templates"
                  className="gl-focus-ring nav-link py-3"
                  style={{ color: 'var(--gl-mint)' }}
                >
                  Templates
                </Link>
              </div>

              {/* Primary CTA — full width */}
              <Link
                href="/archive/1"
                className="gl-btn gl-btn--primary gl-focus-ring text-center"
              >
                ▶ Explore the archive
              </Link>
            </div>
          </div>
        )}
      </header>
    </>
  );
}
