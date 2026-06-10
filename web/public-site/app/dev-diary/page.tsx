import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { Card, Display, Eyebrow } from '@glad-labs/brand';
import {
  getDevDiaryPosts,
  postFeaturedImage,
  cleanPostTitle,
  postExcerpt,
} from '@/lib/posts';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

// Time-based ISR backstop (1h) — on-demand revalidateTag('posts') on publish
// is primary; this floor self-heals if a publish path skips it (poindexter#575).
export const revalidate = 3600;

export const metadata: Metadata = {
  title: `Dev Diary — ${SITE_NAME}`,
  description: 'Daily founder notes from building Glad Labs.',
  alternates: { canonical: `${SITE_URL}/dev-diary` },
  openGraph: {
    title: `Dev Diary — ${SITE_NAME}`,
    description: 'Daily founder notes from building Glad Labs.',
    type: 'website',
    images: [{ url: '/og-image.jpg', width: 1200, height: 630, alt: SITE_NAME }],
  },
};

export default async function DevDiaryPage() {
  let posts: Awaited<ReturnType<typeof getDevDiaryPosts>> = [];
  try {
    posts = await getDevDiaryPosts();
  } catch {
    // On R2 outage keep stale ISR render; an empty list on first load is acceptable.
    posts = [];
  }

  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Header */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-5xl">
          <Eyebrow>GLAD LABS · DEV DIARY</Eyebrow>
          <Display xl>
            The <Display.Accent>diary.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-4 max-w-2xl">
            Daily founder notes from building Glad Labs — raw progress, decisions,
            and what broke today.
          </p>
        </div>
      </section>

      {/* Post list */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="container mx-auto max-w-6xl">
          {posts.length > 0 ? (
            <>
              <h2 className="sr-only">Dev Diary entries</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
                {posts.map((post) => {
                  const imageUrl = postFeaturedImage(post);
                  const title = cleanPostTitle(post.title);
                  const excerpt = postExcerpt(post, 140);
                  return (
                    <Card
                      key={post.id}
                      className="group flex flex-col h-full overflow-hidden p-0"
                    >
                      {imageUrl && (
                        <div className="relative w-full aspect-video overflow-hidden bg-slate-800">
                          <Image
                            src={imageUrl}
                            alt=""
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
                                  },
                                )}
                              </time>
                            ) : null}
                          </Card.Meta>
                          <Card.Title>
                            <Link
                              href={`/posts/${post.slug}`}
                              className="hover:text-[color:var(--gl-cyan)] transition-colors"
                            >
                              {title}
                            </Link>
                          </Card.Title>
                          {excerpt ? (
                            <Card.Body className="line-clamp-3 mt-2">
                              {excerpt}
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
                            Read entry
                            <span aria-hidden>→</span>
                          </Link>
                        </div>
                      </div>
                    </Card>
                  );
                })}
              </div>
            </>
          ) : (
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>NO ENTRIES YET</Card.Meta>
              <h2 className="gl-h2 mt-2">Nothing here yet.</h2>
              <p className="gl-body mt-3 max-w-md mx-auto">
                Dev diary entries appear here as they are published.
              </p>
            </Card>
          )}
        </div>
      </section>
    </div>
  );
}
