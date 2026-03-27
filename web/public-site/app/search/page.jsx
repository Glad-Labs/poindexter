'use client';

import { useState, useEffect } from 'react';
import * as Sentry from '@sentry/nextjs';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { searchPosts } from '../../lib/api-fastapi';
import PostCard from '../../components/PostCard';

export default function SearchPage() {
  const searchParams = useSearchParams();
  const query = searchParams.get('q') || '';
  const [results, setResults] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }

    const performSearch = async () => {
      setIsLoading(true);
      setError('');
      try {
        const posts = await searchPosts(query, 50);
        setResults(posts || []);
        if (!posts || posts.length === 0) {
          setError(`No articles found for "${query}"`);
        }
      } catch (err) {
        Sentry.captureException(err);
        setError('Failed to search articles. Please try again.');
      } finally {
        setIsLoading(false);
      }
    };

    performSearch();
  }, [query]);

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
      {/* Background Grid */}
      <div
        className="fixed inset-0 opacity-[0.03] pointer-events-none"
        style={{
          backgroundImage:
            'linear-gradient(0deg, transparent 24%, rgba(0, 217, 255, 0.1) 25%, rgba(0, 217, 255, 0.1) 26%, transparent 27%, transparent 74%, rgba(0, 217, 255, 0.1) 75%, rgba(0, 217, 255, 0.1) 76%, transparent 77%, transparent), linear-gradient(90deg, transparent 24%, rgba(0, 217, 255, 0.1) 25%, rgba(0, 217, 255, 0.1) 26%, transparent 27%, transparent 74%, rgba(0, 217, 255, 0.1) 75%, rgba(0, 217, 255, 0.1) 76%, transparent 77%, transparent)',
          backgroundSize: '50px 50px',
        }}
      />

      <div className="relative z-10">
        {/* Header */}
        <div className="border-b border-cyan-900/30 bg-slate-900/50 backdrop-blur-sm">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <Link
              href="/"
              className="text-cyan-400 hover:text-cyan-300 mb-4 inline-block"
            >
              ← Back to Home
            </Link>
            <h1 className="text-4xl font-bold text-white">Search Results</h1>
            {query && (
              <p className="text-slate-300 mt-2">
                Found {results.length} article{results.length !== 1 ? 's' : ''}{' '}
                for{' '}
                <span className="text-cyan-400 font-semibold">"{query}"</span>
              </p>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="text-center">
                <div className="inline-block animate-spin text-4xl text-cyan-400 mb-4">
                  ⟳
                </div>
                <p className="text-slate-300">Searching articles...</p>
              </div>
            </div>
          ) : error ? (
            <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-6 text-center">
              <p className="text-red-300">{error}</p>
              <div className="mt-6">
                <Link
                  href="/"
                  className="inline-block px-6 py-2 bg-cyan-500 hover:bg-cyan-600 text-white rounded-lg transition-colors"
                >
                  Browse All Articles
                </Link>
              </div>
            </div>
          ) : results.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {results.map((post) => (
                <PostCard key={post.id || post.slug} post={post} />
              ))}
            </div>
          ) : !query ? (
            <div className="rounded-lg bg-slate-800 border border-cyan-500/30 p-8 text-center">
              <p className="text-slate-300 mb-4">
                Enter a search query to find articles
              </p>
              <Link
                href="/"
                className="inline-block px-6 py-2 bg-cyan-500 hover:bg-cyan-600 text-white rounded-lg transition-colors"
              >
                Browse All Articles
              </Link>
            </div>
          ) : null}
        </div>
      </div>
    </main>
  );
}
