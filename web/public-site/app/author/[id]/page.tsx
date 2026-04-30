import type { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { getPostsByAuthor } from '@/lib/posts';
import { SITE_NAME } from '@/lib/site.config';

const authorProfiles: Record<string, { name: string; bio: string }> = {
  'poindexter-ai': {
    name: 'Poindexter AI',
    bio: `AI Content Generation Engine. Poindexter AI is the intelligent content orchestrator powering ${SITE_NAME}, crafting insightful articles on AI, automation, and digital transformation.`,
  },
  default: {
    name: SITE_NAME,
    bio: 'Where AI meets thoughtful content creation. We explore the intersection of artificial intelligence and human creativity.',
  },
};

export function generateStaticParams() {
  return Object.keys(authorProfiles)
    .filter((id) => id !== 'default')
    .map((id) => ({ id }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const author = authorProfiles[id] || authorProfiles.default;
  const title = `${author.name} | ${SITE_NAME}`;

  return {
    title,
    description: author.bio,
    openGraph: { title, description: author.bio },
  };
}

export default async function AuthorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const author = authorProfiles[id] || authorProfiles.default;

  let authorPosts: Awaited<ReturnType<typeof getPostsByAuthor>>['posts'] = [];
  let authorPostCount = 0;
  try {
    const result = await getPostsByAuthor(id, 1);
    authorPosts = result.posts;
    authorPostCount = result.total;
  } catch {
    // Fall through to the empty-state Card below — same as the
    // archive page does on a transient content-service error.
    authorPosts = [];
    authorPostCount = 0;
  }

  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Author Header */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <Eyebrow>GLAD LABS · AUTHOR</Eyebrow>
          <Display xl>
            <Display.Accent>{author.name}.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-4 max-w-2xl">{author.bio}</p>
          <div className="mt-6">
            <Button as={Link} href="/archive/1" variant="ghost">
              ← All articles
            </Button>
          </div>
        </div>
      </section>

      {/* Articles by this author. Renders the real list when posts
          exist; falls back to the friendly empty-state Card otherwise
          (DB hiccup / no posts yet for this byline). */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="container mx-auto max-w-5xl">
          <h2 className="gl-h2 mb-6">
            Articles by {author.name}
            {authorPostCount > 0 ? (
              <span className="gl-mono gl-mono--upper text-base ml-3 opacity-60">
                {authorPostCount}
              </span>
            ) : null}
          </h2>

          {authorPosts.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {authorPosts.map((post) => (
                <Card
                  key={post.id}
                  className="group flex flex-col h-full overflow-hidden p-0"
                >
                  {post.featured_image_url ? (
                    <div className="relative w-full aspect-video overflow-hidden bg-slate-800">
                      <Image
                        src={post.featured_image_url}
                        alt={post.title}
                        fill
                        className="object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                        sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
                      />
                    </div>
                  ) : null}
                  <div className="flex flex-col justify-between flex-1 p-6">
                    <div>
                      <Card.Meta>
                        {post.published_at ? (
                          <time dateTime={post.published_at}>
                            {new Date(post.published_at).toLocaleDateString(
                              'en-US',
                              { year: 'numeric', month: 'short', day: 'numeric' },
                            )}
                          </time>
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
          ) : (
            <Card accent="cyan" className="text-center py-10">
              <Card.Meta>NO ARTICLES YET</Card.Meta>
              <p className="gl-body mt-3 max-w-md mx-auto">
                {author.name} hasn&apos;t published anything yet. Check back
                soon, or browse the full archive in the meantime.
              </p>
              <div className="mt-6 flex justify-center">
                <Button as={Link} href="/archive/1" variant="secondary">
                  Browse all articles
                </Button>
              </div>
            </Card>
          )}
        </div>
      </section>
    </div>
  );
}
