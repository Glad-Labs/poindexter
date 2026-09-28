import { SITE_NAME, SITE_URL } from './site.config';

// The one image this site serves for itself (public/og-image.jpg). It stands
// in for a missing cover image and is the publisher logo, matching the
// Organization schema in components/StructuredData.tsx.
const SITE_IMAGE_PATH = '/og-image.jpg';

/**
 * Resolve an image URL for schema.org, which wants an absolute URL.
 *
 * Cover images in the static export are absolute (R2, Pexels, Cloudinary)
 * and pass through untouched. A relative path can only name a file this site
 * serves, so it resolves against the site URL. It used to resolve against
 * NEXT_PUBLIC_API_BASE_URL (the FastAPI worker), which the public site
 * cannot reach, and threw in production when that variable was unset.
 */
function absoluteImageURL(path, siteUrl) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${siteUrl}${path.startsWith('/') ? '' : '/'}${path}`;
}

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
export function generateBlogPostingSchema(post, siteUrl = SITE_URL) {
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
  const imageUrl = absoluteImageURL(
    coverImage?.url || SITE_IMAGE_PATH,
    siteUrl
  );

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
        url: absoluteImageURL(SITE_IMAGE_PATH, siteUrl),
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
