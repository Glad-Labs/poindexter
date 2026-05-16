import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { getPosts, postFeaturedImage } from '@/lib/posts';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

interface ArchivePageProps {
  params: Promise<{
    page: string;
  }>;
}

const POSTS_PER_PAGE = 10;

async function getArchivePosts(page: number) {
  try {
    const data = await getPosts(page);
    return { posts: data.posts, total: data.total };
  } catch {
    return { posts: [], total: 0 };
  }
}

export async function generateMetadata({
  params,
}: ArchivePageProps): Promise<Metadata> {
  const { page } = await params;
  const pageNum = parseInt(page) || 1;

  return {
    title: `Article Archive — Page ${pageNum} | ${SITE_NAME}`,
    description: `Browse our collection of in-depth articles and insights. Page ${pageNum} of the ${SITE_NAME} article archive.`,
    alternates: { canonical: `${SITE_URL}/archive/${pageNum}` },
    openGraph: {
      title: `Article Archive — Page ${pageNum} | ${SITE_NAME}`,
      description: `Browse our collection of in-depth articles and insights. Page ${pageNum}.`,
      type: 'website',
    },
  };
}

export async function generateStaticParams() {
  // Pre-generate the first 5 archive pages at build time
  return Array.from({ length: 5 }, (_, i) => ({
    page: String(i + 1),
  }));
}

export default async function ArchivePage({ params }: ArchivePageProps) {
  const { page } = await params;
  const pageNum = parseInt(page) || 1;
  const { posts, total } = await getArchivePosts(pageNum);
  const totalPages = Math.ceil(total / POSTS_PER_PAGE);

  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Header — E3 eyebrow + display + body, matches home hero */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <Eyebrow>GLAD LABS · ARCHIVE</Eyebrow>
          <Display xl>
            The <Display.Accent>archive.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-4 max-w-2xl">
            Every article we&apos;ve published. In-depth dives across AI, hardware,
            and the edges where they meet.
          </p>
        </div>
      </section>

      {/* Content */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="container mx-auto max-w-6xl">
          {posts.length > 0 ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
                {posts.map((post) => {
                  const imageUrl = postFeaturedImage(post);
                  return (
                  <Card key={post.id} className="group flex flex-col h-full overflow-hidden p-0">
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
                  );
                })}
              </div>

              {/* Pagination — brand buttons, amber current page */}
              {totalPages > 1 && (
                <nav
                  className="flex items-center justify-center gap-2 flex-wrap"
                  aria-label="Archive pagination"
                >
                  {pageNum > 1 ? (
                    <Button
                      as={Link}
                      href={`/archive/${pageNum - 1}`}
                      variant="ghost"
                      aria-label={`Go to previous page (page ${pageNum - 1})`}
                    >
                      ← Previous
                    </Button>
                  ) : null}

                  <div className="flex items-center gap-1">
                    {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                      let pageToShow: number;
                      if (totalPages <= 5) {
                        pageToShow = i + 1;
                      } else if (pageNum <= 3) {
                        pageToShow = i + 1;
                      } else if (pageNum >= totalPages - 2) {
                        pageToShow = totalPages - 4 + i;
                      } else {
                        pageToShow = pageNum - 2 + i;
                      }
                      const isCurrent = pageNum === pageToShow;
                      return (
                        <Button
                          key={pageToShow}
                          as={Link}
                          href={`/archive/${pageToShow}`}
                          variant={isCurrent ? 'primary' : 'ghost'}
                          aria-current={isCurrent ? 'page' : undefined}
                          aria-label={`Go to page ${pageToShow}`}
                        >
                          {pageToShow}
                        </Button>
                      );
                    })}
                  </div>

                  {pageNum < totalPages ? (
                    <Button
                      as={Link}
                      href={`/archive/${pageNum + 1}`}
                      variant="ghost"
                      aria-label={`Go to next page (page ${pageNum + 1})`}
                    >
                      Next →
                    </Button>
                  ) : null}
                </nav>
              )}
            </>
          ) : (
            /* Empty state — mono stamp, minimal, no illustrations */
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>NO ARTICLES FOUND</Card.Meta>
              <h2 className="gl-h2 mt-2">Nothing on this page yet.</h2>
              <p className="gl-body mt-3 max-w-md mx-auto">
                New articles land here as the pipeline ships them. Check back
                soon.
              </p>
              <div className="mt-6 flex justify-center">
                <Button as={Link} href="/archive/1" variant="secondary">
                  Go to page 1
                </Button>
              </div>
            </Card>
          )}
        </div>
      </section>
    </div>
  );
}
