import { SITE_NAME, SITE_URL } from './site.config';
/**
 * SEO Utilities for Glad Labs
 * Helpers for meta tags, Open Graph, Twitter Cards, and more
 */

/**
 * Build meta description (keep under 160 characters for optimal display)
 */
export function buildMetaDescription(excerpt, fallback = '') {
  if (!excerpt) return fallback;

  // Truncate to 160 characters if needed
  if (excerpt.length > 160) {
    return excerpt.substring(0, 160).trim() + '...';
  }

  return excerpt;
}

/**
 * Build SEO title (keep under 60 characters for optimal display).
 *
 * Appends the brand suffix (" | <siteName>") ONLY when the result still
 * fits in 60 chars; otherwise returns the bare title so keyword-rich titles
 * aren't truncated in the SERP. Drops the previous low-value " | Blog"
 * suffix — "Blog" isn't the brand and it pushed ~17 otherwise-fine titles
 * past the 60-char limit (audit 2026-06-02, issue #5).
 */
export function buildSEOTitle(title, siteName = SITE_NAME) {
  if (!siteName) return title;
  const candidate = `${title} | ${siteName}`;
  return candidate.length <= 60 ? candidate : title;
}

/**
 * Generate canonical URL to prevent duplicate content issues
 */
export function generateCanonicalURL(
  slug,
  baseURL = SITE_URL
) {
  if (!slug) return baseURL;

  // Ensure slug doesn't have leading/trailing slashes
  const cleanSlug = slug.replace(/^\/+|\/+$/g, '');

  // Add /posts/ prefix if the slug is a bare post slug (no path prefix)
  const hasPathPrefix = cleanSlug.includes('/');
  const path = hasPathPrefix ? cleanSlug : `posts/${cleanSlug}`;

  return `${baseURL}/${path}`;
}
