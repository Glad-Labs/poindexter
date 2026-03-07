import logger from './logger';
import { formatDateISO } from './content-utils';
// Image URL utilities for structured data
import { getImageURL } from './api-fastapi';

/**
 * Generate JSON-LD structured data for a blog post
 * Returns BlogPosting schema for Google rich snippets
 */
export function generateBlogPostingSchema(
  post,
  siteUrl = 'https://glad-labs.com'
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
      name: 'Glad Labs',
      url: siteUrl,
    },
    publisher: {
      '@type': 'Organization',
      name: 'Glad Labs',
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

/**
 * Generate JSON-LD structured data for News Article
 * Similar to BlogPosting but optimized for news sites
 */
export function generateNewsArticleSchema(
  post,
  siteUrl = 'https://glad-labs.com'
) {
  if (!post) return null;

  const { title, excerpt, publishedAt, date, coverImage } = post;

  const publishDate = date || publishedAt;
  const imageUrl = coverImage?.url
    ? getImageURL(coverImage.url)
    : `${siteUrl}/og-image.png`;

  return {
    '@context': 'https://schema.org',
    '@type': 'NewsArticle',
    headline: title,
    description: excerpt,
    image: imageUrl,
    datePublished: formatDateISO(publishDate),
    dateModified: formatDateISO(publishDate),
    author: {
      '@type': 'Organization',
      name: 'Glad Labs',
      logo: {
        '@type': 'ImageObject',
        url: `${siteUrl}/logo.png`,
        width: 250,
        height: 60,
      },
    },
  };
}

/**
 * Generate JSON-LD for Article (generic, more flexible)
 */
export function generateArticleSchema(post, siteUrl = 'https://glad-labs.com') {
  if (!post) return null;

  const { title, excerpt, publishedAt, date, coverImage } = post;

  const publishDate = date || publishedAt;
  const imageUrl = coverImage?.url
    ? getImageURL(coverImage.url)
    : `${siteUrl}/og-image.png`;

  return {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: title,
    description: excerpt,
    image: imageUrl,
    datePublished: formatDateISO(publishDate),
    dateModified: formatDateISO(publishDate),
    author: {
      '@type': 'Organization',
      name: 'Glad Labs',
    },
  };
}

/**
 * Generate JSON-LD Breadcrumb navigation
 * Useful for breadcrumb trails in SERPs
 */
export function generateBreadcrumbSchema(
  items = [],
  siteUrl = 'https://glad-labs.com'
) {
  if (!Array.isArray(items) || items.length === 0) return null;

  const itemListElement = items.map((item, index) => ({
    '@type': 'ListItem',
    position: index + 1,
    name: item.name,
    item: `${siteUrl}${item.url}`,
  }));

  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement,
  };
}

/**
 * Example breadcrumb for article page:
 * [
 *   { name: 'Home', url: '/' },
 *   { name: 'Blog', url: '/archive/1' },
 *   { name: 'AI Trends', url: '/category/ai-trends' },
 *   { name: 'Post Title', url: '/posts/post-slug' }
 * ]
 */

/**
 * Generate JSON-LD Organization schema
 * Usually included on homepage
 */
export function generateOrganizationSchema(
  siteUrl = 'https://glad-labs.com',
  options = {}
) {
  const {
    name = 'Glad Labs',
    logo = `${siteUrl}/logo.png`,
    description = 'AI-powered content and business intelligence platform',
    email = 'info@glad-labs.com',
    phone = '',
    sameAs = [],
  } = options;

  return {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    name,
    url: siteUrl,
    logo,
    description,
    ...(email && { email }),
    ...(phone && { phone }),
    sameAs,
  };
}

/**
 * Generate JSON-LD WebSite schema with search action
 * Enables sitelinks search box in SERPs
 */
export function generateWebsiteSchema(siteUrl = 'https://glad-labs.com') {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    url: siteUrl,
    name: 'Glad Labs',
    description: 'AI Co-Founder Platform',
    potentialAction: {
      '@type': 'SearchAction',
      target: {
        '@type': 'EntryPoint',
        urlTemplate: `${siteUrl}/search?q={search_term_string}`,
      },
      'query-input': 'required name=search_term_string',
    },
  };
}

/**
 * Combine multiple schemas into a single JSON-LD script
 */
export function combineSchemas(schemas = []) {
  if (schemas.length === 0) return null;

  if (schemas.length === 1) {
    return schemas[0];
  }

  // Multiple schemas use @graph
  return {
    '@context': 'https://schema.org',
    '@graph': schemas,
  };
}

/**
 * Generate schema.org/FAQPage for common questions
 */
export function generateFAQPageSchema(faqs = []) {
  if (!Array.isArray(faqs) || faqs.length === 0) return null;

  const mainEntity = faqs.map((faq) => ({
    '@type': 'Question',
    name: faq.question,
    acceptedAnswer: {
      '@type': 'Answer',
      text: faq.answer,
    },
  }));

  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity,
  };
}

/**
 * Generate schema for Article Review/Rating
 */
export function generateArticleReviewSchema(post, rating = null) {
  if (!post) return null;

  const baseSchema = generateBlogPostingSchema(post);

  if (!rating) {
    return baseSchema;
  }

  return {
    ...baseSchema,
    reviewRating: {
      '@type': 'Rating',
      ratingValue: rating.value,
      bestRating: rating.bestRating || 5,
      worstRating: rating.worstRating || 1,
    },
  };
}

/**
 * Validate schema before rendering
 * Returns true if schema looks valid
 */
export function validateSchema(schema) {
  if (!schema) return false;

  // Must have @context
  if (!schema['@context']) return false;

  // Must have @type
  if (!schema['@type']) return false;

  return true;
}

/**
 * Convert schema to JSON-LD string for HTML script tag
 */
export function schemaToJSON(schema) {
  if (!schema) return '';

  try {
    return JSON.stringify(schema);
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Error converting schema to JSON:', error);
    }
    return '';
  }
}
