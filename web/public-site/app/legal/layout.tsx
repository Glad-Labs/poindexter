import type { Metadata } from 'next';
import Link from 'next/link';
import { Button } from '@glad-labs/brand';
import { SITE_NAME } from '@/lib/site.config';

export const metadata: Metadata = {
  title: `Legal Documents - ${SITE_NAME}`,
  description: `Privacy Policy and Terms of Service for ${SITE_NAME}`,
};

const LEGAL_LINK_CLASS =
  'gl-mono gl-mono--accent gl-mono--upper gl-focus-ring inline-block hover:opacity-80 transition-opacity';

export default function LegalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="gl-atmosphere min-h-screen flex flex-col">
      {/* Top bar — back-to-home */}
      <header className="container mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-6">
        <Button as={Link} href="/" variant="ghost">
          ← Back to home
        </Button>
      </header>

      {/* Page content — each page controls its own width + prose */}
      <main className="flex-1 pb-12">{children}</main>

      {/* Footer — mono cross-links */}
      <footer
        className="container mx-auto px-4 sm:px-6 lg:px-8 py-8"
        style={{ borderTop: '1px solid var(--gl-hairline)' }}
      >
        <div className="flex flex-wrap gap-x-6 gap-y-2 justify-center items-center">
          <Link href="/legal/privacy" className={LEGAL_LINK_CLASS}>
            Privacy Policy
          </Link>
          <span className="gl-mono opacity-30" aria-hidden>
            ·
          </span>
          <Link href="/legal/terms" className={LEGAL_LINK_CLASS}>
            Terms of Service
          </Link>
          <span className="gl-mono opacity-30" aria-hidden>
            ·
          </span>
          <Link href="/legal/cookie-policy" className={LEGAL_LINK_CLASS}>
            Cookie Policy
          </Link>
          <span className="gl-mono opacity-30" aria-hidden>
            ·
          </span>
          <Link href="/legal/data-requests" className={LEGAL_LINK_CLASS}>
            Data Requests
          </Link>
        </div>
      </footer>
    </div>
  );
}
