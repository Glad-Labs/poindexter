import type { Metadata } from 'next';
import logger from '@/lib/logger';
import * as Sentry from '@sentry/nextjs';
import Link from 'next/link';
import { notFound } from 'next/navigation';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  'http://localhost:8000';

interface Post {
  id: string;
  title: string;
  slug: string;
  excerpt?: string;
  cover_image_url?: string;
  featured_image_url?: string;
  published_at?: string;
  created_at: string;
  view_count: number;
}

interface Category {
  id: string;
  name: string;
  slug: string;
  description?: string;
  post_count?: number;
}

async function getCategory(slug: string): Promise<Category | null> {
  try {
    const response = await fetch(`${API_BASE}/api/categories/${slug}`, {
      next: { revalidate: 3600 },
    });

    if (!response.ok) {
      return null;
    }

    const data = await response.json();
    return data.data || data;
  } catch (error) {
    logger.error(`Error fetching category "${slug}":`, error);
    Sentry.captureException(error);
    return null;
  }
}

async function getCategoryPosts(categoryId: string): Promise<Post[]> {
  try {
    const response = await fetch(
      `${API_BASE}/api/posts?category_id=${categoryId}&limit=100&status=published`,
      {
        next: { revalidate: 3600 },
      }
    );

    if (!response.ok) {
      return [];
    }

    const data = await response.json();
    return data.posts || data.items || data.data || [];
  } catch (error) {
    logger.error(`Error fetching posts for category "${categoryId}":`, error);
    Sentry.captureException(error);
    return [];
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const category = await getCategory(slug);

  if (!category) {
    return { title: 'Category Not Found | Glad Labs' };
  }

  const title = `${category.name} Articles | Glad Labs`;
  const description =
    category.description ||
    `Browse all articles in the ${category.name} category on Glad Labs.`;

  return {
    title,
    description,
    openGraph: { title, description },
  };
}

export default async function CategoryPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const category = await getCategory(slug);

  if (!category) {
    notFound();
  }

  const posts = await getCategoryPosts(category.id);

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900">
      {/* Category Header */}
      <div className="pt-20 pb-12">
        <div className="px-4 sm:px-6 lg:px-8">
          <div className="max-w-4xl mx-auto">
            <div className="inline-block px-3 py-1 rounded-full bg-cyan-400/10 border border-cyan-400/30 text-cyan-400 text-sm font-medium mb-4">
              Category
            </div>
            <h1 className="text-4xl md:text-5xl font-bold text-white mb-4 leading-tight">
              {category.name}
            </h1>
            {category.description && (
              <p className="text-xl text-slate-300 mb-8 leading-relaxed max-w-2xl">
                {category.description}
              </p>
            )}
            <div className="flex items-center gap-4">
              <span className="text-slate-400 text-sm">
                {posts.length} article{posts.length !== 1 ? 's' : ''}
              </span>
              <Link
                href="/archive/1"
                className="text-sm text-cyan-400 hover:text-cyan-300 transition-colors"
              >
                ← Back to All Articles
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* Posts Grid */}
      <div className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="max-w-4xl mx-auto">
          {posts.length === 0 ? (
            <div className="bg-slate-800/30 border border-slate-700 rounded-lg p-8 text-center">
              <p className="text-slate-400 text-sm">
                No articles in this category yet.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {posts.map((post) => {
                const imageUrl =
                  post.cover_image_url || post.featured_image_url;
                const publishDate = post.published_at || post.created_at;
                const formattedDate = new Date(publishDate).toLocaleDateString(
                  'en-US',
                  {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                  }
                );

                return (
                  <Link
                    key={post.id}
                    href={`/posts/${post.slug}`}
                    className="group rounded-lg border border-slate-700 hover:border-cyan-400/50 transition-all duration-300 overflow-hidden hover:shadow-lg hover:shadow-cyan-400/10"
                  >
                    {imageUrl && (
                      <div className="relative w-full h-40 bg-slate-700">
                        <img
                          src={imageUrl}
                          alt={post.title}
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                        />
                      </div>
                    )}
                    <div className="p-4">
                      <h3 className="text-lg font-semibold text-cyan-400 group-hover:text-cyan-300 transition-colors line-clamp-2 mb-2">
                        {post.title}
                      </h3>
                      {post.excerpt && (
                        <p className="text-sm text-slate-400 line-clamp-2 mb-3">
                          {post.excerpt}
                        </p>
                      )}
                      <div className="flex items-center justify-between text-xs text-slate-500">
                        <span>{formattedDate}</span>
                        {post.view_count > 0 && (
                          <span>{post.view_count.toLocaleString()} views</span>
                        )}
                      </div>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
