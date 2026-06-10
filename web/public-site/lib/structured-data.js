// Image URL utilities for structured data
import { getImageURL } from './url';
import { SITE_NAME, SITE_URL } from './site.config';

/**
 * Format date to ISO format for schema.org
 * Returns: "2025-10-25"
 */
function formatDateISO(dateString) {
  if (!dateString) {
    return '';
  }

  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) {
      return '';
    }

    return date.toISOString().split('T')[0];
  } catch (_error) {
    return '';
  }
}

/**
 * Generate JSON-LD structured data for a blog post
 * Returns BlogPosting schema for Google rich snippets
 */
export function generateBlogPostingSchema(
  post,
  siteUrl = SITE_URL
) {
  if (!post) return null;

  const {
    title,
    excerpt,
    content,
    slug,
    publishedAt,
    date,
    coverImage,
    category,
  } = post;

  const publishDate = date || publishedAt;
  const imageUrl = coverImage?.url
    ? getImageURL(coverImage.url)
    : `${siteUrl}/og-image.png`;

  return {
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: title,
    description: excerpt,
    image: {
      '@type': 'ImageObject',
      url: imageUrl,
      width: 1200,
      height: 630,
    },
    datePublished: formatDateISO(publishDate),
    dateModified: formatDateISO(publishDate),
    author: {
      '@type': 'Person',
      name: SITE_NAME,
      url: siteUrl,
    },
    publisher: {
      '@type': 'Organization',
      name: SITE_NAME,
      logo: {
        '@type': 'ImageObject',
        url: `${siteUrl}/logo.png`,
        width: 250,
        height: 60,
      },
    },
    mainEntityOfPage: {
      '@type': 'WebPage',
      '@id': `${siteUrl}/posts/${slug}`,
    },
    articleBody: content,
    keywords: category?.name ? [category.name] : [],
    wordCount: content ? content.split(/\s+/).length : 0,
  };
}
