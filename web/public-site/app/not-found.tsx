'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import * as Sentry from '@sentry/nextjs';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { getPaginatedPosts } from '../lib/api-fastapi';
import { SITE_NAME, SUPPORT_EMAIL } from '@/lib/site.config';

interface Post {
  id: string | number;
  slug: string;
  title: string;
  excerpt?: string;
}

/**
 * 404 Not Found Page
 * Displays when a page doesn't exist. Suggests related posts.
 */
export default function NotFound() {
  const [suggestedPosts, setSuggestedPosts] = useState<Post[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchSuggestedPosts = async () => {
      try {
        const data = await getPaginatedPosts(1, 3);
        const posts = data?.data || [];
        setSuggestedPosts(posts.slice(0, 3));
      } catch (error) {
        Sentry.captureException(error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchSuggestedPosts();
  }, []);

  return (
    <div className="gl-atmosphere min-h-screen flex flex-col">
      <main className="flex-1 container mx-auto px-4 sm:px-6 lg:px-8 py-20 md:py-32">
        <div className="max-w-3xl mx-auto text-center">
          <Eyebrow>GLAD LABS · 404</Eyebrow>
          <Display xl>
            <Display.Accent>404.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-4 max-w-xl mx-auto">
            The page you&apos;re looking for doesn&apos;t exist. It may have
            been moved, deleted, or mistyped.
          </p>

          {/* Actions */}
          <div className="flex flex-col sm:flex-row gap-3 justify-center mt-8">
            <Button as={Link} href="/" variant="primary">
              ← Back to home
            </Button>
            <Button as={Link} href="/archive/1" variant="secondary">
              Browse all posts
            </Button>
          </div>
        </div>

        {/* Suggested posts */}
        {!isLoading && suggestedPosts.length > 0 && (
          <div className="max-w-5xl mx-auto mt-20">
            <Eyebrow>GLAD LABS · TRY THESE</Eyebrow>
            <h2 className="gl-h2 mt-1 mb-6">You might enjoy these instead.</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {suggestedPosts.map((post) => (
                <Card
                  key={post.id}
                  className="group flex flex-col h-full overflow-hidden"
                >
                  <Card.Title>
                    <Link
                      href={`/posts/${post.slug}`}
                      className="hover:text-[color:var(--gl-cyan)] transition-colors"
                    >
                      {post.title}
                    </Link>
                  </Card.Title>
                  {post.excerpt && (
                    <Card.Body className="line-clamp-3 mt-2">
                      {post.excerpt}
                    </Card.Body>
                  )}
                  <div
                    className="pt-4 mt-4"
                    style={{ borderTop: '1px solid var(--gl-hairline)' }}
                  >
                    <Link
                      href={`/posts/${post.slug}`}
                      className="gl-mono gl-mono--accent gl-mono--upper inline-flex items-center gap-2 hover:opacity-80 transition-opacity"
                      aria-hidden="true"
                      tabIndex={-1}
                    >
                      Read article
                      <span aria-hidden>→</span>
                    </Link>
                  </div>
                </Card>
              ))}
            </div>
          </div>
        )}

        {/* Helpful links */}
        <div className="max-w-3xl mx-auto mt-16 text-center">
          <p className="gl-mono gl-mono--upper opacity-70 text-xs mb-4">
            Other places to explore
          </p>
          <div className="flex flex-wrap justify-center gap-x-4 gap-y-2">
            <Link
              href="/archive/1"
              className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80"
            >
              Archive
            </Link>
            <span className="gl-mono opacity-30" aria-hidden>·</span>
            <Link
              href="/"
              className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80"
            >
              Homepage
            </Link>
            <span className="gl-mono opacity-30" aria-hidden>·</span>
            <Link
              href="/about"
              className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80"
            >
              About {SITE_NAME}
            </Link>
          </div>
        </div>
      </main>

      <footer
        className="container mx-auto px-4 sm:px-6 lg:px-8 py-6"
        style={{ borderTop: '1px solid var(--gl-hairline)' }}
      >
        <p className="gl-mono gl-mono--upper opacity-60 text-xs text-center">
          If you believe this is an error,{' '}
          <a href={`mailto:${SUPPORT_EMAIL}`} className="gl-mono--accent">
            contact us
          </a>
          .
        </p>
      </footer>
    </div>
  );
}
