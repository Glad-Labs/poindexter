'use client';
import * as Sentry from '@sentry/nextjs';

import Link from 'next/link';
import { useEffect } from 'react';
import { Button, Card, Eyebrow } from '@glad-labs/brand';
import { SUPPORT_EMAIL } from '@/lib/site.config';

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Error Boundary Component
 * Catches errors and displays recovery options.
 * Renders as the error.tsx page in Next.js App Router.
 */
export default function Error({ error, reset }: ErrorProps) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  const isNetworkError =
    error?.message?.includes('fetch') || error?.message?.includes('network');
  const isNotFoundError = error?.message?.includes('404');

  const headline = isNetworkError
    ? 'Network connection error.'
    : isNotFoundError
      ? 'Page not found.'
      : 'An unexpected error occurred.';

  const detail = isNetworkError
    ? 'Unable to connect to the server.'
    : 'An unexpected error occurred.';

  return (
    <div className="gl-atmosphere min-h-screen flex flex-col">
      <main className="flex-1 container mx-auto px-4 sm:px-6 lg:px-8 py-20 md:py-32 flex items-center justify-center">
        <div className="max-w-2xl w-full">
          <Eyebrow>GLAD LABS · ERROR</Eyebrow>
          <h1
            className="mt-2 font-[family-name:var(--gl-font-display)] font-bold text-white text-4xl md:text-5xl leading-tight tracking-tight"
          >
            Something went <span className="gl-accent">wrong.</span>
          </h1>
          <p className="gl-body gl-body--lg mt-4">{headline}</p>

          {/* Error stamp — amber tick, mono */}
          <div
            className="gl-tick-left gl-tick-left--amber mt-8 p-4"
            style={{
              background: 'var(--gl-surface)',
              border: '1px solid var(--gl-hairline)',
            }}
          >
            <p className="gl-mono gl-mono--upper gl-mono--amber text-xs flex items-start gap-2">
              <span aria-hidden>⚠</span>
              <span>ERROR DETAILS</span>
            </p>
            <p className="gl-body gl-body--sm mt-2 font-[family-name:var(--gl-font-mono)] break-words">
              {detail}
            </p>
          </div>

          {/* Description */}
          <div className="mt-8 gl-body gl-body--lg">
            {isNetworkError ? (
              <>
                <p>Unable to load content right now. This could be due to:</p>
                <ul className="gl-body mt-3 space-y-1 list-disc list-inside">
                  <li>Your internet connection is unstable</li>
                  <li>The server is temporarily unavailable</li>
                  <li>A proxy or firewall is blocking the request</li>
                </ul>
              </>
            ) : isNotFoundError ? (
              <p>The resource you&apos;re looking for couldn&apos;t be found.</p>
            ) : (
              <p>
                We&apos;ve been notified and are working on it. Try again in a
                moment.
              </p>
            )}
          </div>

          {/* Actions */}
          <div className="flex flex-col sm:flex-row gap-3 mt-8">
            <Button variant="primary" onClick={() => reset()}>
              ↻ Try again
            </Button>
            <Button as={Link} href="/" variant="secondary">
              ← Back to home
            </Button>
          </div>

          {/* Recovery tips */}
          <Card accent="cyan" className="mt-10">
            <Card.Meta>RECOVERY TIPS</Card.Meta>
            <ul className="gl-body gl-body--sm mt-3 space-y-2 list-none">
              <li>✓ Check your internet connection</li>
              <li>✓ Clear your browser cache and cookies</li>
              <li>✓ Try a different browser or incognito mode</li>
              <li>✓ Refresh the page or try again in a few minutes</li>
            </ul>
          </Card>

          {/* Helpful links */}
          <div className="mt-10 flex flex-wrap gap-x-4 gap-y-2">
            <Link
              href="/"
              className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80"
            >
              Homepage
            </Link>
            <span className="gl-mono opacity-30" aria-hidden>·</span>
            <Link
              href="/archive/1"
              className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80"
            >
              Archive
            </Link>
            <span className="gl-mono opacity-30" aria-hidden>·</span>
            <a
              href={`mailto:${SUPPORT_EMAIL}`}
              className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80"
            >
              Contact Support
            </a>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer
        className="container mx-auto px-4 sm:px-6 lg:px-8 py-6"
        style={{ borderTop: '1px solid var(--gl-hairline)' }}
      >
        <p className="gl-mono gl-mono--upper opacity-60 text-xs text-center">
          If this persists,{' '}
          <a
            href={`mailto:${SUPPORT_EMAIL}?subject=Website%20Error`}
            className="gl-mono--accent"
          >
            report the issue
          </a>
          .
        </p>
      </footer>
    </div>
  );
}
