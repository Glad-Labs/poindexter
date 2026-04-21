import './globals.css';
import { SiteNav } from '@/components/SiteNav';
import { SiteFooter } from '@/components/SiteFooter';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

export const metadata = {
  title: {
    default: `${SITE_NAME} — Ship an AI writer. Own the stack.`,
    template: `%s · ${SITE_NAME}`,
  },
  description:
    'Local-first AI publishing pipeline. The Quick Start Guide, the hardware spec, the full source. No SaaS. No subscription.',
  metadataBase: new URL(SITE_URL),
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: SITE_URL,
    siteName: SITE_NAME,
  },
  twitter: {
    card: 'summary_large_image',
    site: '@_gladlabs',
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <div className="gl-atmosphere" style={{ minHeight: '100vh' }}>
          <SiteNav />
          <main id="main">{children}</main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}
