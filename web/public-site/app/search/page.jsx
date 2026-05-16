'use client';

import { useState, useEffect, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { postFeaturedImage } from '@/lib/posts';

// Fetch through /api/posts — server-side proxies R2 same-origin, avoids
// CORS/CSP gaps from hitting the R2 bucket directly from the browser
// (Gitea #262). Limit high enough to cover every published post.
const SEARCH_INDEX_URL = '/api/posts?limit=10000';

function SearchContent() {
  const searchParams = useSearchParams();
  const query = searchParams.get('q') || '';
  const [results, setResults] = useState([]);
  const [allPosts, setAllPosts] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const loadPosts = async () => {
      if (allPosts.length > 0) return;
      try {
        const resp = await fetch(SEARCH_INDEX_URL);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        setAllPosts(data.items || data.posts || data);
      } catch (err) {
        setError('Failed to load articles.');
      }
    };
    loadPosts();
  }, [allPosts.length]);

  useEffect(() => {
    if (!query.trim() || allPosts.length === 0) {
      setResults([]);
      return;
    }

    setIsLoading(true);
    setError('');

    const q = query.toLowerCase();
    const matched = allPosts.filter((post) => {
      const title = (post.title || '').toLowerCase();
      const excerpt = (post.excerpt || '').toLowerCase();
      const seoTitle = (post.seo_title || '').toLowerCase();
      const seoDesc = (post.seo_description || '').toLowerCase();
      const keywords = (post.seo_keywords || '').toLowerCase();
      // Include tags[] so searching a tag slug ("local-inference") matches
      // posts tagged with that slug even when the title/excerpt don't
      // mention it (gitea#267 follow-up).
      const tags = Array.isArray(post.tags)
        ? post.tags.join(' ').toLowerCase()
        : '';
      return (
        title.includes(q) ||
        excerpt.includes(q) ||
        seoTitle.includes(q) ||
        seoDesc.includes(q) ||
        keywords.includes(q) ||
        tags.includes(q)
      );
    });

    setResults(matched);
    if (matched.length === 0) {
      setError(`No articles found for "${query}"`);
    }
    setIsLoading(false);
  }, [query, allPosts]);

  return (
    <main className="gl-atmosphere min-h-screen">
      {/* Header */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <Eyebrow>GLAD LABS · SEARCH</Eyebrow>
          <Display xl>
            Search <Display.Accent>results.</Display.Accent>
          </Display>
          {query ? (
            <p className="gl-body gl-body--lg mt-4 max-w-2xl">
              Found {results.length} article
              {results.length !== 1 ? 's' : ''} matching{' '}
              <span className="gl-mono gl-mono--accent gl-mono--upper">
                {query}
              </span>
              .
            </p>
          ) : (
            <p className="gl-body gl-body--lg mt-4 max-w-2xl">
              Enter a search query to find articles.
            </p>
          )}
          <div className="mt-6">
            <Button as={Link} href="/" variant="ghost">
              ← Back to home
            </Button>
          </div>
        </div>
      </section>

      {/* Results */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="container mx-auto max-w-6xl">
          {isLoading ? (
            <Card accent="cyan" className="text-center py-12">
              <Card.Meta>SEARCHING</Card.Meta>
              <p className="gl-body mt-3">Searching articles...</p>
            </Card>
          ) : error ? (
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>NO MATCHES</Card.Meta>
              <h2 className="gl-h2 mt-2">{error}</h2>
              <div className="mt-6 flex justify-center">
                <Button as={Link} href="/archive/1" variant="secondary">
                  Browse all articles
                </Button>
              </div>
            </Card>
          ) : results.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {results.map((post) => {
                const imageUrl = postFeaturedImage(post);
                return (
                <Card
                  key={post.id || post.slug}
                  className="group flex flex-col h-full overflow-hidden p-0"
                >
                  {imageUrl && (
                    <div className="relative w-full aspect-video overflow-hidden bg-slate-800">
                      <Image
                        src={imageUrl}
                        alt={post.title}
                        fill
                        className="object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                        sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
                      />
                    </div>
                  )}
                  <div className="flex flex-col justify-between flex-1 p-6">
                    <div>
                      {post.published_at ? (
                        <Card.Meta>
                          <time dateTime={post.published_at}>
                            {new Date(post.published_at).toLocaleDateString(
                              'en-US',
                              {
                                year: 'numeric',
                                month: 'short',
                                day: 'numeric',
                              }
                            )}
                          </time>
                        </Card.Meta>
                      ) : null}
                      <Card.Title>
                        <Link
                          href={`/posts/${post.slug}`}
                          className="hover:text-[color:var(--gl-cyan)] transition-colors"
                        >
                          {post.title}
                        </Link>
                      </Card.Title>
                      {post.excerpt ? (
                        <Card.Body className="line-clamp-3 mt-2">
                          {post.excerpt}
                        </Card.Body>
                      ) : null}
                    </div>
                    <div className="pt-4 mt-4 border-t border-[color:var(--gl-hairline)]">
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
                  </div>
                </Card>
                );
              })}
            </div>
          ) : !query ? (
            <Card accent="cyan" className="text-center py-12">
              <Card.Meta>EMPTY QUERY</Card.Meta>
              <p className="gl-body mt-3 max-w-md mx-auto">
                Enter a search query to find articles.
              </p>
              <div className="mt-6 flex justify-center">
                <Button as={Link} href="/archive/1" variant="secondary">
                  Browse all articles
                </Button>
              </div>
            </Card>
          ) : null}
        </div>
      </section>
    </main>
  );
}

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="gl-atmosphere min-h-screen flex items-center justify-center">
          <p className="gl-body">Loading search...</p>
        </div>
      }
    >
      <SearchContent />
    </Suspense>
  );
}
