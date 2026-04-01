/**
 * Structured Data Components
 * Schema.org markup for enhanced SEO
 */

/**
 * Breadcrumb Schema
 * Shows breadcrumb navigation in Google search results
 */
export function BreadcrumbSchema({
  items = [],
}: {
  items?: Array<{ label: string; url: string }>;
}) {
  const schema = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: (items || []).map((item, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name: item.label,
      item: `https://www.gladlabs.io${item.url}`,
    })),
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
    />
  );
}

/**
 * FAQ Schema
 * Enables FAQ rich snippets in Google SERP
 */
export function FAQSchema({
  faqs = [],
}: {
  faqs?: Array<{ question: string; answer: string }>;
}) {
  const schema = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: (faqs || []).map(({ question, answer }) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: answer,
      },
    })),
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
    />
  );
}

/**
 * Organization Schema
 * Identifies your organization for Google
 */
export function OrganizationSchema() {
  const schema = {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    name: 'Glad Labs',
    description:
      'AI and digital innovation research organization focused on autonomous intelligence',
    url: 'https://www.gladlabs.io',
    logo: 'https://www.gladlabs.io/og-image.jpg',
    sameAs: [
      'https://twitter.com/GladLabsAI',
      'https://linkedin.com/company/glad-labs',
      'https://github.com/glad-labs',
    ],
    contactPoint: {
      '@type': 'ContactPoint',
      contactType: 'Customer Service',
      email: 'hello@gladlabs.io',
    },
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
    />
  );
}

/**
 * Blog Schema
 * Identifies a blog post for richer indexing
 */
export function BlogPostingSchema({
  headline,
  description,
  image,
  datePublished,
  dateModified,
  author = 'Glad Labs',
}: {
  headline: string;
  description: string;
  image: string;
  datePublished: string;
  dateModified?: string;
  author?: string;
}) {
  const schema = {
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: headline,
    description: description,
    image: image,
    datePublished: datePublished,
    dateModified: dateModified || datePublished,
    author: {
      '@type': 'Person',
      name: author,
    },
    publisher: {
      '@type': 'Organization',
      name: 'Glad Labs',
      logo: {
        '@type': 'ImageObject',
        url: 'https://www.gladlabs.io/og-image.jpg',
        width: 1200,
        height: 630,
      },
    },
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
    />
  );
}

/**
 * News Article Schema
 * For news and timely content
 */
export function NewsArticleSchema({
  headline,
  description,
  image,
  datePublished,
  dateModified,
}: {
  headline: string;
  description: string;
  image: string;
  datePublished: string;
  dateModified?: string;
}) {
  const schema = {
    '@context': 'https://schema.org',
    '@type': 'NewsArticle',
    headline: headline,
    description: description,
    image: image,
    datePublished: datePublished,
    dateModified: dateModified || datePublished,
    publisher: {
      '@type': 'Organization',
      name: 'Glad Labs',
      logo: {
        '@type': 'ImageObject',
        url: 'https://www.gladlabs.io/og-image.jpg',
        width: 1200,
        height: 630,
      },
    },
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
    />
  );
}
