/**
 * Post Data Mapper
 *
 * Maps FastAPI JSON response to React component format
 * This bridges the gap between:
 * - API: FastAPI with nested coverImage structure
 * - Components: Expecting coverImage.data.attributes format
 */

/**
 * Map single post from database to component format
 * @param {Object} dbPost - Post object from PostgreSQL
 * @returns {Object} Mapped post object for React components
 */
export function mapDatabasePostToComponent(dbPost) {
  if (!dbPost) return null;

  return {
    // Basic fields (pass through)
    id: dbPost.id,
    title: dbPost.title || 'Untitled',
    slug: dbPost.slug,
    content: dbPost.content || '',
    excerpt: dbPost.excerpt || '',
    status: dbPost.status || 'draft',

    // Date fields
    date: dbPost.published_at || dbPost.created_at,
    publishedAt: dbPost.published_at || dbPost.created_at,
    created_at: dbPost.created_at,
    updated_at: dbPost.updated_at,

    // Image field - convert from simple string to nested structure
    // This makes it compatible with existing PostCard component
    coverImage: {
      data: {
        attributes: {
          url: dbPost.featured_image_url || null,
          alternativeText: `Featured image for ${dbPost.title}`,
          name: 'featured-image',
        },
      },
    },

    // SEO fields
    seo_title: dbPost.seo_title,
    seo_description: dbPost.seo_description,
    seo_keywords: dbPost.seo_keywords,

    // Metadata
    metadata: dbPost.metadata || {},
  };
}

/**
 * Map array of posts (for lists)
 * @param {Array} posts - Array of posts from database
 * @returns {Array} Mapped posts array
 */
export function mapDatabasePostsToComponents(posts) {
  if (!Array.isArray(posts)) return [];
  return posts.map(mapDatabasePostToComponent);
}

/**
 * Extract featured image URL (works with both formats)
 * @param {Object} post - Post object (either database or mapped format)
 * @returns {string|null} Image URL or null
 */
export function getFeaturedImageUrl(post) {
  if (!post) return null;

  // If already mapped to nested format
  if (post.coverImage?.data?.attributes?.url) {
    return post.coverImage.data.attributes.url;
  }

  // If direct database format
  if (post.featured_image_url) {
    return post.featured_image_url;
  }

  return null;
}

/**
 * Get display date string
 * @param {Object} post - Post object
 * @returns {string} Formatted date
 */
export function getPostDate(post) {
  const dateString = post.date || post.publishedAt || post.created_at;
  if (!dateString) return 'Unknown date';

  try {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch (e) {
    if (process.env.NODE_ENV !== 'production') {
      console.error('[Post Mapper] Error formatting date:', dateString, e);
    }
    return 'Invalid date';
  }
}

/**
 * Get ISO date string for <time dateTime> attribute
 * @param {Object} post - Post object
 * @returns {string} ISO date string (YYYY-MM-DD)
 */
export function getPostDateISO(post) {
  const dateString = post.date || post.publishedAt || post.created_at;
  if (!dateString) return new Date().toISOString().split('T')[0];

  try {
    return new Date(dateString).toISOString().split('T')[0];
  } catch (e) {
    if (process.env.NODE_ENV !== 'production') {
      console.error('[Post Mapper] Error formatting ISO date:', dateString, e);
    }
    return new Date().toISOString().split('T')[0];
  }
}

/**
 * Format excerpt if too long
 * @param {string} excerpt - Excerpt text
 * @param {number} maxLength - Max length (default 160)
 * @returns {string} Formatted excerpt
 */
export function formatExcerpt(excerpt, maxLength = 160) {
  if (!excerpt) return '';
  if (excerpt.length <= maxLength) return excerpt;

  return excerpt.substring(0, maxLength).trim() + '...';
}

/**
 * Get SEO meta description
 * Prefers seo_description, falls back to formatted excerpt
 * @param {Object} post - Post object
 * @returns {string} Meta description
 */
export function getMetaDescription(post) {
  if (post.seo_description) {
    return formatExcerpt(post.seo_description, 160);
  }

  if (post.excerpt) {
    return formatExcerpt(post.excerpt, 160);
  }

  // Fallback: use first 160 chars of content
  if (post.content) {
    // Remove markdown/HTML for cleaner excerpt
    const plainText = post.content
      .replace(/[#*_\[\]()]/g, '')
      .replace(/<[^>]*>/g, '');
    return formatExcerpt(plainText, 160);
  }

  return '';
}

/**
 * Get SEO meta keywords
 * @param {Object} post - Post object
 * @returns {string} Comma-separated keywords
 */
export function getMetaKeywords(post) {
  if (post.seo_keywords) {
    return post.seo_keywords;
  }

  // Fallback: generate from title
  if (post.title) {
    const titleWords = post.title
      .toLowerCase()
      .split(' ')
      .filter((w) => w.length > 3)
      .slice(0, 5);
    return titleWords.join(', ');
  }

  return '';
}

/**
 * Validate post has required fields for display
 * @param {Object} post - Post object to validate
 * @returns {Object} Validation result { isValid: boolean, errors: string[] }
 */
export function validatePost(post) {
  const errors = [];

  if (!post) {
    return { isValid: false, errors: ['Post is null or undefined'] };
  }

  if (!post.title || post.title === 'Untitled') {
    errors.push('Post has no proper title');
  }

  if (!post.slug) {
    errors.push('Post has no slug');
  }

  if (!post.excerpt && !post.content) {
    errors.push('Post has no excerpt or content');
  }

  if (post.title?.length > 200) {
    errors.push('Post title is too long (>200 chars)');
  }

  if (post.content && post.content.length < 100) {
    errors.push('Post content is too short (<100 chars)');
  }

  return {
    isValid: errors.length === 0,
    errors,
  };
}
