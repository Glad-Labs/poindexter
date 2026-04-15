import Script from 'next/script';
import CookieConsentBanner from '../components/CookieConsentBanner.jsx';
import Footer from '../components/Footer';
import TopNavigation from '../components/TopNav.js';
import WebVitals from '../components/WebVitals';
import { SITE_NAME, SITE_URL, SITE_TAGLINE, PODCAST_NAME } from '@/lib/site.config';
import '../styles/globals.css';

export const metadata = {
  title: `${SITE_NAME} - ${SITE_TAGLINE}`,
  description:
    'Exploring the future of technology, AI, and digital innovation. In-depth articles on AI agents, cloud infrastructure, and modern development.',
  metadataBase: new URL(SITE_URL),
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: SITE_URL,
    title: SITE_NAME,
    description:
      'Exploring the future of technology, AI, and digital innovation',
    images: [
      {
        url: '/og-image.jpg',
        width: 1200,
        height: 630,
        alt: SITE_NAME,
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    site: '@_gladlabs',
    creator: '@_gladlabs',
  },
  verification: {
    google:
      process.env.NEXT_PUBLIC_GOOGLE_VERIFICATION ||
      'C-pZ-_sOD4wRU9OVPAcG-1TVQAYEwZfdaApx-BxkgsM',
  },
  robots: {
    index: true,
    follow: true,
    'max-snippet': -1,
    'max-image-preview': 'large',
    'max-video-preview': -1,
  },
  alternates: {
    types: {
      'application/rss+xml': [
        { url: '/feed.xml', title: `${SITE_NAME} Blog` },
        { url: '/api/podcast', title: PODCAST_NAME },
      ],
    },
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <head>
        {/* AdSense account verification (ads.txt alone should suffice, but belt-and-suspenders) */}
        <meta name="google-adsense-account" content="ca-pub-4578747062758519" />
        {/* RSS autodiscovery for feed readers */}
        <link
          rel="alternate"
          type="application/rss+xml"
          title={SITE_NAME}
          href={`${SITE_URL}/feed.xml`}
        />
        <link
          rel="alternate"
          type="application/rss+xml"
          title={PODCAST_NAME}
          href={`${SITE_URL}/podcast-feed.xml`}
        />
        {/*
          GDPR COMPLIANCE: Analytics scripts are NOT loaded here.
          They are only loaded AFTER user consent via CookieConsentBanner.tsx
          See: components/CookieConsentBanner.tsx loadGoogleAnalytics()
        */}
      </head>
      <body>
        <TopNavigation />
        <main id="main-content" className="flex-grow">
          {children}
        </main>
        <Footer />
        {/* Client-side components that need hydration */}
        <WebVitals />
        {/*
          GDPR: GA and AdSense are NOT loaded here.
          CookieConsentBanner handles loading them ONLY after user consent.
          AdSenseScript and GA Script tags were removed from layout to comply.
          See: components/CookieConsentBanner.tsx
        */}
        <CookieConsentBanner />
        {/* Lemon Squeezy affiliate tracking */}
        <Script
          id="lemon-squeezy-affiliate"
          strategy="lazyOnload"
          dangerouslySetInnerHTML={{
            __html:
              'window.lemonSqueezyAffiliateConfig = { store: "gladlabs" };',
          }}
        />
        <Script
          src="https://lmsqueezy.com/affiliate.js"
          strategy="lazyOnload"
        />
      </body>
    </html>
  );
}
