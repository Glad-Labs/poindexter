import type { Metadata } from 'next';
import Link from 'next/link';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
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

      {/* Articles Section — placeholder until author-indexed posts land */}
      <section className="px-4 sm:px-6 lg:px-8 pb-20">
        <div className="container mx-auto max-w-5xl">
          <h2 className="gl-h2 mb-6">Articles by {author.name}</h2>
          <Card accent="cyan" className="text-center py-10">
            <Card.Meta>COMING SOON</Card.Meta>
            <p className="gl-body mt-3 max-w-md mx-auto">
              Articles from this author coming soon. Check back to see their
              latest work!
            </p>
            <div className="mt-6 flex justify-center">
              <Button as={Link} href="/archive/1" variant="secondary">
                Browse all articles
              </Button>
            </div>
          </Card>
        </div>
      </section>
    </div>
  );
}
