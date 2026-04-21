import Link from 'next/link';

export function SiteFooter() {
  return (
    <footer className="sf-footer">
      <div className="sf-container sf-footer__inner">
        <div>// GLAD LABS · LLC · 2025-2026 · ALL RIGHTS RESERVED</div>
        <div className="sf-footer__legal">
          <Link href="/about">About</Link>
          <Link
            href="https://www.gladlabs.io"
            target="_blank"
            rel="noopener noreferrer"
          >
            Writing ↗
          </Link>
          <Link
            href="mailto:security@gladlabs.io"
            aria-label="Email security team"
          >
            Security
          </Link>
          <Link
            href="https://github.com/Glad-Labs/poindexter"
            target="_blank"
            rel="noopener noreferrer"
          >
            Source ↗
          </Link>
        </div>
      </div>
    </footer>
  );
}
