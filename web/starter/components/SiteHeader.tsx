import Link from 'next/link';

/**
 * Top-of-page navigation. Fork-users: drop your logo, site name, and
 * primary nav items here. This file exists to give you a seam to
 * customize without touching the layout.
 */
export function SiteHeader() {
  return (
    <header className="border-b border-gray-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          Your Site Name
        </Link>
        <nav className="flex gap-5 text-sm text-brand-muted">
          <Link href="/" className="hover:text-brand">
            Home
          </Link>
          <Link href="/about" className="hover:text-brand">
            About
          </Link>
        </nav>
      </div>
    </header>
  );
}
