import type { Metadata } from 'next';
import Link from 'next/link';
import Image from 'next/image';
import { notFound } from 'next/navigation';
import logger from '@/lib/logger';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { getAllPublishedPosts, postFeaturedImage } from '@/lib/posts';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

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
  category_id?: string;
}

interface Category {
  id: string;
  name: string;
  slug: string;
  description?: string;
  post_count?: number;
}

async function getCategories(): Promise<Category[]> {
  try {
    const response = await fetch(`${STATIC_URL}/categories.json`, {
      next: { revalidate: 300 },
    });
    if (!response.ok) return [];
    const data = await response.json();
    return data.categories || data || [];
  } catch (error) {
    logger.error('Error fetching categories:', error);
    return [];
  }
}

async function getCategory(slug: string): Promise<Category | null> {
  const categories = await getCategories();
  return categories.find((c) => c.slug === slug) || null;
}

async function getCategoryPosts(categoryId: string): Promise<Post[]> {
  const allPosts = await getAllPublishedPosts();
  return allPosts.filter((p) => p.category_id === categoryId);
}

export async function generateStaticParams() {
  try {
    const categories = await getCategories();
    return categories.map((cat) => ({ slug: cat.slug }));
  } catch {
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
    return { title: `Category Not Found | ${SITE_NAME}` };
  }

  const title = `${category.name} Articles | ${SITE_NAME}`;
  const description =
    category.description ||
    `Browse all articles in the ${category.name} category on ${SITE_NAME}.`;

  return {
    title,
    description,
    alternates: { canonical: `${SITE_URL}/category/${slug}` },
    openGraph: {
      title,
      description,
      type: 'website',
      url: `${SITE_URL}/category/${slug}`,
      images: [
        {
          url: '/og-image.jpg',
          width: 1200,
          height: 630,
          alt: `${category.name} articles on ${SITE_NAME}`,
        },
      ],
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
      images: ['/og-image.jpg'],
    },
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
    <div className="gl-atmosphere min-h-screen">
      {/* Header */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <Eyebrow>GLAD LABS · CATEGORY</Eyebrow>
          <Display xl>
            <Display.Accent>{category.name}.</Display.Accent>
          </Display>
          {category.description && (
            <p className="gl-body gl-body--lg mt-4 max-w-2xl">
              {category.description}
            </p>
          )}
          <div className="flex items-center gap-4 mt-6">
            <span className="gl-mono gl-mono--upper text-[color:var(--gl-text-muted)]">
              {posts.length} article{posts.length !== 1 ? 's' : ''}
            </span>
            <Button as={Link} href="/archive/1" variant="ghost">
              ← All articles
            </Button>
          </div>
        </div>
      </section>

      {/* Posts Grid */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="container mx-auto max-w-6xl">
          {posts.length === 0 ? (
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>NO ARTICLES FOUND</Card.Meta>
              <h2 className="gl-h2 mt-2">
                Nothing in {category.name} yet.
              </h2>
              <p className="gl-body mt-3 max-w-md mx-auto">
                New articles land here as the pipeline ships them. Check back
                soon.
              </p>
              <div className="mt-6 flex justify-center">
                <Button as={Link} href="/archive/1" variant="secondary">
                  Browse all articles
                </Button>
              </div>
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {posts.map((post) => {
                const imageUrl = postFeaturedImage(post);
                const publishDate = post.published_at || post.created_at;
                return (
                  <Card
                    key={post.id}
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
                        <Card.Meta>
                          <time dateTime={publishDate}>
                            {new Date(publishDate).toLocaleDateString('en-US', {
                              year: 'numeric',
                              month: 'short',
                              day: 'numeric',
                            })}
                          </time>
                          {post.view_count > 0 ? (
                            <>
                              <span aria-hidden> · </span>
                              <span>{post.view_count} views</span>
                            </>
                          ) : null}
                        </Card.Meta>
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
          )}
        </div>
      </section>
    </div>
  );
}
