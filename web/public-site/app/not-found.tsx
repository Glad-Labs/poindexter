'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { getPaginatedPosts } from '../lib/api-fastapi';
import * as Sentry from '@sentry/nextjs';

interface Post {
  id: string | number;
  slug: string;
  title: string;
  excerpt?: string;
}

/**
 * 404 Not Found Page
 * Displays when a page doesn't exist
 * Shows related posts and navigation options for recovery
 */
export default function NotFound() {
  const [suggestedPosts, setSuggestedPosts] = useState<Post[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Fetch recent posts to suggest to user
    const fetchSuggestedPosts = async () => {
      try {
        const data = await getPaginatedPosts(1, 3);
        const posts = data?.data || [];
        setSuggestedPosts(posts.slice(0, 3));
      } catch (error) {
        // Client-side error — report to Sentry for visibility (non-critical: page still renders)
        Sentry.captureException(error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchSuggestedPosts();
  }, []);

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-black flex flex-col">
      {/* Main Content */}
      <div className="flex-1 container mx-auto px-4 md:px-6 py-12 md:py-24 flex flex-col justify-center">
        <div className="max-w-2xl mx-auto text-center">
          {/* Large 404 */}
          <div className="mb-8">
            <h1 className="text-9xl md:text-[150px] font-black bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-blue-500 leading-none">
              404
            </h1>
            <p className="text-gray-400 text-lg mt-4">
              Oops! The page you&apos;re looking for doesn&apos;t exist.
            </p>
          </div>

          {/* Description */}
          <div className="mb-8 md:mb-12">
            <p className="text-gray-300 text-lg mb-4">
              This could be due to a broken link, typo in the URL, or the page
              may have been moved or deleted.
            </p>
            <p className="text-gray-400">
              Don&apos;t worry, here are some ways you can get back on track:
            </p>
          </div>

          {/* Action Buttons */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-16">
            <Link
              href="/"
              className="inline-block px-8 py-3 bg-cyan-500 hover:bg-cyan-600 text-white font-semibold rounded-lg transition-colors duration-200"
            >
              ← Back to Home
            </Link>
            <Link
              href="/archive/1"
              className="inline-block px-8 py-3 bg-gray-700 hover:bg-gray-600 text-white font-semibold rounded-lg transition-colors duration-200"
            >
              Browse All Posts
            </Link>
          </div>

          {/* Suggested Posts */}
          {!isLoading && suggestedPosts.length > 0 && (
            <div className="mt-16 pt-12 border-t border-gray-700">
              <h2 className="text-2xl font-bold text-cyan-300 mb-8">
                You might enjoy these posts instead:
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {suggestedPosts.map((post) => (
                  <Link
                    key={post.id}
                    href={`/posts/${post.slug}`}
                    className="group bg-gray-800 rounded-lg overflow-hidden hover:bg-gray-700 transition-colors duration-200"
                  >
                    <div className="p-6">
                      <h3 className="font-bold text-cyan-300 group-hover:text-cyan-200 transition-colors mb-2 line-clamp-2">
                        {post.title}
                      </h3>
                      <p className="text-gray-400 text-sm line-clamp-3 mb-4">
                        {post.excerpt}
                      </p>
                      <span className="text-cyan-400 text-sm font-medium">
                        Read Article →
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* Helpful Links */}
          <div className="mt-16 pt-12 border-t border-gray-700">
            <p className="text-gray-400 mb-6">Other places to explore:</p>
            <div className="flex flex-wrap justify-center gap-4">
              <Link
                href="/archive/1"
                className="text-cyan-400 hover:text-cyan-300 underline"
              >
                Post Archive
              </Link>
              <span className="text-gray-600">•</span>
              <Link
                href="/"
                className="text-cyan-400 hover:text-cyan-300 underline"
              >
                Homepage
              </Link>
              <span className="text-gray-600">•</span>
              <a
                href="https://www.glad-labs.com"
                className="text-cyan-400 hover:text-cyan-300 underline"
              >
                Glad Labs Main Site
              </a>
            </div>
          </div>
        </div>
      </div>

      {/* Footer Info */}
      <div className="border-t border-gray-700 bg-gray-900/50 py-6">
        <div className="container mx-auto px-4 text-center text-gray-500 text-sm">
          <p>
            If you believe this is an error, please{' '}
            <a
              href="mailto:hello@glad-labs.com"
              className="text-cyan-400 hover:text-cyan-300"
            >
              contact us
            </a>
            .
          </p>
        </div>
      </div>
    </div>
  );
}
