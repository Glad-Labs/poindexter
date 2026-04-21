import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { getPosts } from '@/lib/posts';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

const POSTS_PER_PAGE = 12;

async function getAllPublishedPosts() {
  try {
    const data = await getPosts(1);
    return { posts: data.posts, total: data.total, error: null };
  } catch {
    return { posts: [], total: 0, error: 'network' };
  }
}

export const metadata: Metadata = {
  title: `All Articles | ${SITE_NAME}`,
  description:
    'Browse our complete collection of in-depth articles on AI, technology, and digital transformation.',
  alternates: { canonical: `${SITE_URL}/posts` },
  openGraph: {
    title: `All Articles | ${SITE_NAME}`,
    description:
      'Browse our complete collection of in-depth articles on AI, technology, and digital transformation.',
    type: 'website',
  },
};

export default async function PostsPage() {
  const { posts, total, error } = await getAllPublishedPosts();
  const totalPages = Math.ceil(total / POSTS_PER_PAGE);

  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Header */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <Eyebrow>GLAD LABS · ARTICLES</Eyebrow>
          <Display xl>
            All <Display.Accent>articles.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-4 max-w-2xl">
            Explore our collection of in-depth articles and insights across AI,
            hardware, and the edges where they meet.
          </p>
        </div>
      </section>

      {/* Content */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="container mx-auto max-w-6xl">
          {error ? (
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>SERVICE UNAVAILABLE</Card.Meta>
              <h2 className="gl-h2 mt-2">
                Unable to load articles right now.
              </h2>
              <p className="gl-body mt-3 max-w-md mx-auto">
                Our content service is temporarily unavailable. Please try
                again shortly.
              </p>
            </Card>
          ) : posts.length > 0 ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
                {posts.map((post) => (
                  <Card
                    key={post.id}
                    className="group flex flex-col h-full overflow-hidden p-0"
                  >
                    {post.featured_image_url && (
                      <div className="relative w-full aspect-video overflow-hidden bg-slate-800">
                        <Image
                          src={post.featured_image_url}
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
                          {post.published_at ? (
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
                          ) : null}
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
                ))}
              </div>

              {totalPages > 1 && (
                <div className="text-center">
                  <Button as={Link} href="/archive/1" variant="primary">
                    Browse full archive ({total} articles) →
                  </Button>
                </div>
              )}
            </>
          ) : (
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>NO ARTICLES FOUND</Card.Meta>
              <h2 className="gl-h2 mt-2">Nothing here yet.</h2>
              <p className="gl-body mt-3 max-w-md mx-auto">
                Check back soon for new articles.
              </p>
            </Card>
          )}
        </div>
      </section>
    </div>
  );
}
