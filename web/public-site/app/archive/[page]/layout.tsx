import type { Metadata } from 'next';
import { SITE_NAME } from '@/lib/site.config';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ page: string }>;
}): Promise<Metadata> {
  const { page } = await params;
  const pageNum = parseInt(page) || 1;
  const title =
    pageNum === 1
      ? `Article Archive | ${SITE_NAME}`
      : `Article Archive — Page ${pageNum} | ${SITE_NAME}`;

  return {
    title,
    description:
      'Explore our collection of in-depth articles and insights on AI, automation, and digital transformation.',
    openGraph: {
      title,
      description:
        'Explore our collection of in-depth articles and insights on AI, automation, and digital transformation.',
    },
  };
}

export default function ArchiveLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
