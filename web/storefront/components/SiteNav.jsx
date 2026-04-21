import Link from 'next/link';

/*
  Storefront nav — the mono eyebrow is baked into the brand mark itself:
    // GLAD LABS · AI
  That `·` dot is amber so the brand always carries a spark of the
  categorical color. Links sit mono-uppercased on the right.
*/
export function SiteNav() {
  return (
    <nav className="sf-nav" aria-label="Main">
      <div className="sf-nav__inner">
        <Link href="/" aria-label="Glad Labs — home" className="sf-nav__brand">
          // GLAD LABS <span className="sf-nav__brand-dot">·</span> AI
        </Link>
        <div className="sf-nav__links">
          <Link href="/guide" className="sf-nav__link">
            The Guide
          </Link>
          <Link href="/about" className="sf-nav__link">
            About
          </Link>
          <Link
            href="https://www.gladlabs.io"
            className="sf-nav__link"
            target="_blank"
            rel="noopener noreferrer"
          >
            Writing ↗
          </Link>
        </div>
      </div>
    </nav>
  );
}
