import type { Metadata } from 'next';
import Link from 'next/link';
import { SITE_NAME } from '@/lib/site.config';

export const metadata: Metadata = {
  title: `Legal Documents - ${SITE_NAME}`,
  description: `Privacy Policy and Terms of Service for ${SITE_NAME}`,
};

export default function LegalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-gray-900 text-gray-200">
      {/* Header */}
      <header className="bg-gray-800 border-b border-gray-700">
        <div className="container mx-auto px-4 py-6">
          <Link href="/" className="text-cyan-400 hover:text-cyan-300">
            ← Back to Home
          </Link>
        </div>
      </header>

      {/* Main Content */}
      <div className="container mx-auto px-4 sm:px-6 lg:px-8 max-w-2xl py-12">
        {children}
      </div>

      {/* Footer Navigation */}
      <footer className="bg-gray-800 border-t border-gray-700 mt-12">
        <div className="container mx-auto px-4 py-6 flex gap-4 justify-center">
          <Link
            href="/legal/privacy"
            className="text-cyan-400 hover:text-cyan-300"
          >
            Privacy Policy
          </Link>
          <span className="text-gray-600">|</span>
          <Link
            href="/legal/terms"
            className="text-cyan-400 hover:text-cyan-300"
          >
            Terms of Service
          </Link>
          <span className="text-gray-600">|</span>
          <Link
            href="/legal/cookie-policy"
            className="text-cyan-400 hover:text-cyan-300"
          >
            Cookie Policy
          </Link>
        </div>
      </footer>
    </div>
  );
}
