import type { Metadata } from 'next';
import './globals.css';
import { SiteHeader } from '@/components/SiteHeader';
import { SiteFooter } from '@/components/SiteFooter';

export const metadata: Metadata = {
  // Fork-users: set your site's title + description here. These drive the
  // default <title> and social-share metadata for every page.
  title: {
    default: 'Your Site Name',
    template: '%s — Your Site Name',
  },
  description: 'A Poindexter-powered publication.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-brand-surface font-sans text-brand antialiased">
        <SiteHeader />
        <main className="mx-auto max-w-3xl px-4 py-10">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
