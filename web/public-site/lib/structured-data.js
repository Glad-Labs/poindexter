import { SITE_NAME, SITE_URL } from './site.config';
import { siteImageObject } from './site-image';

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
  // A cover image is whatever size the pipeline rendered (1024x1024 on 39 of
  // the 40 newest posts on 2026-09-28), so it goes out with no width/height
  // rather than a guessed 1200x630. The site image stands in when a post has
  // no cover, and its size is known.
  const image = coverImage?.url
    ? {
        '@type': 'ImageObject',
        url: absoluteImageURL(coverImage.url, siteUrl),
      }
    : siteImageObject(siteUrl);

  return {
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: title,
    description: excerpt,
    image,
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
      logo: siteImageObject(siteUrl),
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
