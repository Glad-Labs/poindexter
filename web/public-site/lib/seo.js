import logger from './logger';
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
 * Build SEO title (keep under 60 characters for optimal display)
 */
export function buildSEOTitle(
  title,
  siteName = SITE_NAME,
  suffix = '| Blog'
) {
  const separator = siteName ? ` ${suffix} ` : '';

  // Target 50-60 characters
  const fullTitle = siteName ? `${title}${separator}${siteName}` : title;

  if (fullTitle.length > 60) {
    return `${title} ${suffix}`;
  }

  return fullTitle;
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

/**
 * Generate Open Graph meta tags object
 */
export function generateOGTags(post, baseURL = SITE_URL) {
  if (!post) return {};

  const { title, excerpt, slug, coverImage } = post;

  const imageUrl = coverImage?.url || `${baseURL}/og-image.png`;
  const pageURL = `${baseURL}/posts/${slug}`;

  return {
    'og:title': title,
    'og:description': excerpt || '',
    'og:image': imageUrl,
    'og:image:width': '1200',
    'og:image:height': '630',
    'og:url': pageURL,
    'og:type': 'article',
    'og:site_name': SITE_NAME,
  };
}

/**
 * Generate Twitter Card meta tags object
 */
export function generateTwitterTags(
  post,
  twitterHandle = '@GladLabsAI',
  baseURL = SITE_URL
) {
  if (!post) return {};

  const { title, excerpt, coverImage } = post;

  const imageUrl = coverImage?.url || `${baseURL}/og-image.png`;

  return {
    'twitter:card': 'summary_large_image',
    'twitter:title': title,
    'twitter:description': excerpt || '',
    'twitter:image': imageUrl,
    'twitter:site': twitterHandle,
    'twitter:creator': twitterHandle,
  };
}

/**
 * Generate robots meta tag
 */
export function generateRobotsTag(options = {}) {
  const {
    index = true,
    follow = true,
    snippet = true,
    imageIndex = true,
    archive = true,
  } = options;

  const parts = [];

  if (index) parts.push('index');
  else parts.push('noindex');

  if (follow) parts.push('follow');
  else parts.push('nofollow');

  if (!snippet) parts.push('nosnippet');
  if (!imageIndex) parts.push('noimageindex');
  if (!archive) parts.push('noarchive');

  return parts.join(', ');
}

/**
 * Build hreflang tags for multi-language support
 */
export function generateHrefLangTags(
  slug,
  languages = ['en'],
  baseURL = SITE_URL
) {
  return languages.map((lang) => ({
    rel: 'alternate',
    hrefLang: lang,
    href: `${baseURL}/${lang}/posts/${slug}`,
  }));
}

/**
 * Generate viewport meta tag (standard for responsive design)
 */
export function generateViewportTag() {
  return {
    name: 'viewport',
    content: 'width=device-width, initial-scale=1, maximum-scale=5',
  };
}

/**
 * Generate charset meta tag
 */
export function generateCharsetTag() {
  return {
    charSet: 'utf-8',
  };
}

/**
 * Generate theme color meta tag (for mobile browsers)
 */
export function generateThemeColorTag(color = '#00d4ff') {
  return {
    name: 'theme-color',
    content: color,
  };
}

/**
 * Generate apple-touch-icon meta tag (for iOS bookmarks)
 */
export function generateAppleTouchIconTag(url) {
  if (!url) return null;

  return {
    rel: 'apple-touch-icon',
    href: url,
  };
}

/**
 * Generate favicon link tag
 */
export function generateFaviconTag(url) {
  if (!url) return null;

  return {
    rel: 'icon',
    href: url,
    type: 'image/x-icon',
  };
}

/**
 * Generate manifest link tag (for PWA)
 */
export function generateManifestTag(url) {
  if (!url) return null;

  return {
    rel: 'manifest',
    href: url,
  };
}

/**
 * Generate preconnect link (for performance)
 */
export function generatePreconnectLink(url) {
  if (!url) return null;

  return {
    rel: 'preconnect',
    href: url,
  };
}

/**
 * Generate DNS prefetch link (for performance)
 */
export function generateDNSPrefetchLink(url) {
  if (!url) return null;

  return {
    rel: 'dns-prefetch',
    href: url,
  };
}

/**
 * Build complete SEO metadata object for a post
 */
export function buildPostSEO(post, options = {}) {
  const {
    baseURL = SITE_URL,
    siteName = SITE_NAME,
    twitterHandle = '@GladLabsAI',
  } = options;

  const { title, excerpt, slug } = post;

  const seoTitle = buildSEOTitle(title, siteName);
  const metaDescription = buildMetaDescription(excerpt);
  const canonicalURL = generateCanonicalURL(`/posts/${slug}`, baseURL);
  const ogTags = generateOGTags(post, baseURL);
  const twitterTags = generateTwitterTags(post, twitterHandle, baseURL);

  return {
    title: seoTitle,
    description: metaDescription,
    canonical: canonicalURL,
    og: ogTags,
    twitter: twitterTags,
    robots: generateRobotsTag({ index: true, follow: true }),
  };
}

/**
 * Check if URL is valid
 */
export function isValidURL(url) {
  try {
    new URL(url);
    return true;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Invalid URL:', url, error);
    }
    return false;
  }
}

/**
 * Generate breadcrumb list for SEO
 */
export function generateBreadcrumbs(path = '/') {
  const segments = path.split('/').filter(Boolean);
  const breadcrumbs = [{ name: 'Home', url: '/' }];

  let currentPath = '';
  segments.forEach((segment) => {
    currentPath += `/${segment}`;
    breadcrumbs.push({
      name: segment.charAt(0).toUpperCase() + segment.slice(1),
      url: currentPath,
    });
  });

  return breadcrumbs;
}

/**
 * Generate alt text for images (SEO + accessibility)
 */
export function generateImageAltText(title, context = 'Featured image') {
  if (!title) return context;

  // Limit to ~125 characters for optimal display in image viewers
  const alt = `${title} - ${context}`;
  if (alt.length > 125) {
    return `${title.substring(0, 100)} - ${context}`;
  }

  return alt;
}

/**
 * Check content readability (Flesch Reading Ease simplified)
 * Returns: 'Easy', 'Medium', 'Hard'
 */
export function checkContentReadability(text) {
  if (!text) return 'Unknown';

  const words = text.split(/\s+/).length;
  const sentences = text.split(/[.!?]+/).length;
  const syllables = text.match(/[aeiou]/g)?.length || 0;

  if (words === 0) return 'Unknown';

  // Simplified Flesch Reading Ease
  const score =
    206.835 - (1.015 * words) / sentences - (84.6 * syllables) / words;

  if (score > 60) return 'Easy';
  if (score > 40) return 'Medium';
  return 'Hard';
}

/**
 * Generate keyword recommendations from content
 * Simple implementation: extracts common words
 */
export function extractKeywords(text, limit = 5) {
  if (!text) return [];

  // Comprehensive stopwords list matching Python version for consistency
  const stopWords = new Set([
    // Common pronouns and determiners
    'the',
    'a',
    'an',
    'and',
    'or',
    'but',
    'in',
    'on',
    'at',
    'to',
    'for',
    'is',
    'are',
    'was',
    'were',
    'be',
    'been',
    'being',
    'have',
    'has',
    'had',
    'do',
    'does',
    'did',
    'will',
    'would',
    'could',
    'should',
    'may',
    'might',
    'must',
    'i',
    'me',
    'my',
    'we',
    'you',
    'your',
    'he',
    'she',
    'it',
    'they',
    'them',
    'this',
    'that',
    'these',
    'those',
    'which',
    'what',
    'who',
    'where',
    'when',
    'why',
    'with',
    'from',
    'by',
    'about',
    'as',
    'just',
    'only',
    'so',
    'than',
    'very',
    // Common verbs
    'can',
    'make',
    'made',
    'use',
    'used',
    'say',
    'said',
    'get',
    'got',
    'go',
    'went',
    'come',
    'came',
    'take',
    'took',
    'know',
    'knew',
    'think',
    'thought',
    // Generic words that don't add value
    'data',
    'information',
    'content',
    'post',
    'article',
    'blog',
    'website',
    'page',
    'thing',
    'things',
    'stuff',
    'way',
    'time',
    'year',
    'day',
    'week',
    'month',
    'also',
    'more',
    'most',
    'some',
    'any',
    'all',
    'each',
    'every',
    'other',
    'first',
    'second',
    'third',
    'last',
    'new',
    'old',
    'right',
    'left',
    'good',
    'bad',
    'like',
    'such',
    'example',
    'however',
    'therefore',
    'because',
    'while',
    'another',
    'through',
    'during',
    'before',
    'after',
    'between',
    'above',
    'below',
    'even',
    'then',
    'there',
    'here',
    'now',
    'today',
    'could',
    // Irrelevant context words
    'fred',
    'role',
    'roll',
    'lobster',
    'potato',
    'potatoes',
    'muffin',
    'flour',
    'clam',
    'songs',
    'song',
    'films',
    'worst',
    'best',
    'player',
    'game',
    'love',
    'future',
    'their',
    'shall',
    'within',
    'until',
    'among',
    'via',
    'throughout',
    'toward',
    'towards',
    'upon',
    'without',
    'against',
  ]);

  const words = text.toLowerCase().match(/\b[\w']{4,}\b/g) || [];

  const frequ = {};
  words.forEach((word) => {
    if (!stopWords.has(word) && word.length >= 4 && word.length <= 20) {
      frequ[word] = (frequ[word] || 0) + 1;
    }
  });

  // Filter: keep only words appearing 2+ times (removes noise) and return sorted
  return Object.entries(frequ)
    .filter(([_, count]) => count >= 2)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([word]) => word);
}
