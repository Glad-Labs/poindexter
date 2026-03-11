import AdSenseScript from '../components/AdSenseScript';
import CookieConsentBanner from '../components/CookieConsentBanner.jsx';
import Footer from '../components/Footer';
import TopNavigation from '../components/TopNav.js';
import WebVitals from '../components/WebVitals';
import { Analytics } from '@vercel/analytics/react';
import { SpeedInsights } from '@vercel/speed-insights/next';
import '../styles/globals.css';

export const metadata = {
  title: 'Glad Labs - Technology & Innovation',
  description:
    'Exploring the future of technology, AI, and digital innovation at Glad Labs',
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL || 'https://glad-labs.com'
  ),
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: process.env.NEXT_PUBLIC_SITE_URL || 'https://glad-labs.com',
    title: 'Glad Labs',
    description:
      'Exploring the future of technology, AI, and digital innovation',
    images: [
      {
        url: '/og-image.jpg',
        width: 1200,
        height: 630,
        alt: 'Glad Labs',
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    site: '@GladLabsAI',
    creator: '@GladLabsAI',
  },
  robots: {
    index: true,
    follow: true,
    'max-snippet': -1,
    'max-image-preview': 'large',
    'max-video-preview': -1,
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <head>
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
        <AdSenseScript />
        <CookieConsentBanner />
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
