/**
 * Tests for lib/post-mapper.js
 *
 * Covers all exported functions:
 * - mapDatabasePostToComponent
 * - mapDatabasePostsToComponents
 * - getFeaturedImageUrl
 * - getPostDate
 * - getPostDateISO
 * - formatExcerpt
 * - getMetaDescription
 * - getMetaKeywords
 * - validatePost
 */

// Mock logger to suppress console noise in tests
jest.mock('../logger', () => ({
  error: jest.fn(),
  warn: jest.fn(),
  info: jest.fn(),
}));

import {
  mapDatabasePostToComponent,
  mapDatabasePostsToComponents,
  getFeaturedImageUrl,
  getPostDate,
  getPostDateISO,
  formatExcerpt,
  getMetaDescription,
  getMetaKeywords,
  validatePost,
} from '../post-mapper';

const SAMPLE_DB_POST = {
  id: 'post-uuid-1',
  title: 'AI Trends in 2026',
  slug: 'ai-trends-2026',
  content: 'Lorem ipsum dolor sit amet.',
  excerpt: 'A deep dive into AI trends.',
  status: 'published',
  published_at: '2026-03-01T12:00:00Z',
  created_at: '2026-02-28T09:00:00Z',
  updated_at: '2026-03-02T10:00:00Z',
  featured_image_url: '/images/ai-trends.jpg',
  seo_title: 'AI Trends 2026',
  seo_description: 'Top AI trends you should know.',
  seo_keywords: 'AI, machine learning, 2026',
  metadata: { author: 'Glad Labs' },
};

// ---------------------------------------------------------------------------
// mapDatabasePostToComponent
// ---------------------------------------------------------------------------

describe('mapDatabasePostToComponent()', () => {
  test('returns null when input is null', () => {
    expect(mapDatabasePostToComponent(null)).toBeNull();
  });

  test('returns null when input is undefined', () => {
    expect(mapDatabasePostToComponent(undefined)).toBeNull();
  });

  test('maps id and title', () => {
    const post = mapDatabasePostToComponent(SAMPLE_DB_POST);
    expect(post.id).toBe(SAMPLE_DB_POST.id);
    expect(post.title).toBe(SAMPLE_DB_POST.title);
  });

  test('maps slug, content, excerpt, status', () => {
    const post = mapDatabasePostToComponent(SAMPLE_DB_POST);
    expect(post.slug).toBe(SAMPLE_DB_POST.slug);
    expect(post.content).toBe(SAMPLE_DB_POST.content);
    expect(post.excerpt).toBe(SAMPLE_DB_POST.excerpt);
    expect(post.status).toBe(SAMPLE_DB_POST.status);
  });

  test('date uses published_at', () => {
    const post = mapDatabasePostToComponent(SAMPLE_DB_POST);
    expect(post.date).toBe(SAMPLE_DB_POST.published_at);
    expect(post.publishedAt).toBe(SAMPLE_DB_POST.published_at);
  });

  test('date falls back to created_at when published_at absent', () => {
    const dbPost = { ...SAMPLE_DB_POST, published_at: undefined };
    const post = mapDatabasePostToComponent(dbPost);
    expect(post.date).toBe(SAMPLE_DB_POST.created_at);
  });

  test('coverImage is nested structure with featured_image_url', () => {
    const post = mapDatabasePostToComponent(SAMPLE_DB_POST);
    expect(post.coverImage.data.attributes.url).toBe(
      SAMPLE_DB_POST.featured_image_url
    );
  });

  test('coverImage url is null when no featured_image_url', () => {
    const post = mapDatabasePostToComponent({
      ...SAMPLE_DB_POST,
      featured_image_url: undefined,
    });
    expect(post.coverImage.data.attributes.url).toBeNull();
  });

  test('maps SEO fields', () => {
    const post = mapDatabasePostToComponent(SAMPLE_DB_POST);
    expect(post.seo_title).toBe(SAMPLE_DB_POST.seo_title);
    expect(post.seo_description).toBe(SAMPLE_DB_POST.seo_description);
    expect(post.seo_keywords).toBe(SAMPLE_DB_POST.seo_keywords);
  });

  test('metadata defaults to empty object when absent', () => {
    const post = mapDatabasePostToComponent({
      ...SAMPLE_DB_POST,
      metadata: undefined,
    });
    expect(post.metadata).toEqual({});
  });

  test('title defaults to Untitled when absent', () => {
    const post = mapDatabasePostToComponent({
      ...SAMPLE_DB_POST,
      title: undefined,
    });
    expect(post.title).toBe('Untitled');
  });

  test('status defaults to draft when absent', () => {
    const post = mapDatabasePostToComponent({
      ...SAMPLE_DB_POST,
      status: undefined,
    });
    expect(post.status).toBe('draft');
  });
});

// ---------------------------------------------------------------------------
// mapDatabasePostsToComponents
// ---------------------------------------------------------------------------

describe('mapDatabasePostsToComponents()', () => {
  test('returns empty array for null input', () => {
    expect(mapDatabasePostsToComponents(null)).toEqual([]);
  });

  test('returns empty array for non-array input', () => {
    expect(mapDatabasePostsToComponents('not an array')).toEqual([]);
    expect(mapDatabasePostsToComponents(42)).toEqual([]);
    expect(mapDatabasePostsToComponents({})).toEqual([]);
  });

  test('returns empty array for empty array', () => {
    expect(mapDatabasePostsToComponents([])).toEqual([]);
  });

  test('maps each post in array', () => {
    const posts = [
      SAMPLE_DB_POST,
      { ...SAMPLE_DB_POST, id: 'post-2', slug: 'post-2' },
    ];
    const result = mapDatabasePostsToComponents(posts);
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('post-uuid-1');
    expect(result[1].id).toBe('post-2');
  });

  test('null posts in array map to null', () => {
    const result = mapDatabasePostsToComponents([null, SAMPLE_DB_POST]);
    expect(result[0]).toBeNull();
    expect(result[1].id).toBe(SAMPLE_DB_POST.id);
  });
});

// ---------------------------------------------------------------------------
// getFeaturedImageUrl
// ---------------------------------------------------------------------------

describe('getFeaturedImageUrl()', () => {
  test('returns null for null input', () => {
    expect(getFeaturedImageUrl(null)).toBeNull();
  });

  test('returns url from nested coverImage structure', () => {
    const mapped = mapDatabasePostToComponent(SAMPLE_DB_POST);
    expect(getFeaturedImageUrl(mapped)).toBe(SAMPLE_DB_POST.featured_image_url);
  });

  test('returns url from direct featured_image_url field', () => {
    expect(getFeaturedImageUrl(SAMPLE_DB_POST)).toBe(
      SAMPLE_DB_POST.featured_image_url
    );
  });

  test('returns null when no image present', () => {
    expect(getFeaturedImageUrl({ id: 'x' })).toBeNull();
  });

  test('nested format takes priority over direct field', () => {
    const post = {
      featured_image_url: '/direct.jpg',
      coverImage: { data: { attributes: { url: '/nested.jpg' } } },
    };
    expect(getFeaturedImageUrl(post)).toBe('/nested.jpg');
  });
});

// ---------------------------------------------------------------------------
// getPostDate
// ---------------------------------------------------------------------------

describe('getPostDate()', () => {
  test('returns formatted date string for valid date', () => {
    const post = { date: '2026-03-01T12:00:00Z' };
    const result = getPostDate(post);
    expect(result).toMatch(/March/);
    expect(result).toMatch(/2026/);
  });

  test('returns Unknown date when no date fields present', () => {
    expect(getPostDate({})).toBe('Unknown date');
  });

  test('uses publishedAt as fallback', () => {
    const post = { publishedAt: '2026-01-15T00:00:00Z' };
    const result = getPostDate(post);
    expect(result).toMatch(/January/);
  });

  test('uses created_at as final fallback', () => {
    const post = { created_at: '2025-12-25T00:00:00Z' };
    const result = getPostDate(post);
    expect(result).toMatch(/December/);
    expect(result).toMatch(/2025/);
  });
});

// ---------------------------------------------------------------------------
// getPostDateISO
// ---------------------------------------------------------------------------

describe('getPostDateISO()', () => {
  test('returns ISO date string (YYYY-MM-DD) for valid date', () => {
    const post = { date: '2026-03-01T12:00:00Z' };
    expect(getPostDateISO(post)).toBe('2026-03-01');
  });

  test('returns today ISO when no date present', () => {
    const today = new Date().toISOString().split('T')[0];
    expect(getPostDateISO({})).toBe(today);
  });

  test('uses publishedAt field', () => {
    const post = { publishedAt: '2026-06-15T00:00:00Z' };
    expect(getPostDateISO(post)).toBe('2026-06-15');
  });
});

// ---------------------------------------------------------------------------
// formatExcerpt
// ---------------------------------------------------------------------------

describe('formatExcerpt()', () => {
  test('returns empty string for null input', () => {
    expect(formatExcerpt(null)).toBe('');
  });

  test('returns empty string for undefined input', () => {
    expect(formatExcerpt(undefined)).toBe('');
  });

  test('returns short text unchanged', () => {
    const short = 'Short excerpt.';
    expect(formatExcerpt(short)).toBe(short);
  });

  test('truncates to default 160 chars with ellipsis', () => {
    const long = 'A'.repeat(200);
    const result = formatExcerpt(long);
    expect(result).toHaveLength(163); // 160 + '...'
    expect(result.endsWith('...')).toBe(true);
  });

  test('respects custom maxLength', () => {
    const text = 'Hello world this is a test sentence.';
    const result = formatExcerpt(text, 10);
    expect(result.endsWith('...')).toBe(true);
    expect(result.length).toBeLessThanOrEqual(13);
  });

  test('exact maxLength text returned unchanged', () => {
    const text = 'A'.repeat(160);
    expect(formatExcerpt(text)).toBe(text);
  });
});

// ---------------------------------------------------------------------------
// getMetaDescription
// ---------------------------------------------------------------------------

describe('getMetaDescription()', () => {
  test('prefers seo_description', () => {
    const post = {
      seo_description: 'SEO desc.',
      excerpt: 'Excerpt.',
      content: 'Content.',
    };
    expect(getMetaDescription(post)).toBe('SEO desc.');
  });

  test('falls back to excerpt when no seo_description', () => {
    const post = { excerpt: 'Fallback excerpt.', content: 'Content.' };
    expect(getMetaDescription(post)).toBe('Fallback excerpt.');
  });

  test('falls back to content when no seo_description or excerpt', () => {
    const post = { content: 'This is the content of the article.' };
    const result = getMetaDescription(post);
    expect(result).toBeTruthy();
    expect(result.length).toBeGreaterThan(0);
  });

  test('returns empty string when nothing available', () => {
    expect(getMetaDescription({})).toBe('');
  });

  test('strips markdown from content fallback', () => {
    const post = { content: '# Header\n**bold** [link](url) content' };
    const result = getMetaDescription(post);
    expect(result).not.toContain('#');
    expect(result).not.toContain('**');
  });

  test('truncates seo_description to 160 chars', () => {
    const post = { seo_description: 'X'.repeat(200) };
    expect(getMetaDescription(post).length).toBeLessThanOrEqual(163);
  });
});

// ---------------------------------------------------------------------------
// getMetaKeywords
// ---------------------------------------------------------------------------

describe('getMetaKeywords()', () => {
  test('returns seo_keywords when present', () => {
    const post = { seo_keywords: 'AI, ML, Python', title: 'Something' };
    expect(getMetaKeywords(post)).toBe('AI, ML, Python');
  });

  test('generates keywords from title when no seo_keywords', () => {
    const post = { title: 'Artificial Intelligence in Healthcare' };
    const result = getMetaKeywords(post);
    expect(result).toBeTruthy();
    expect(result).toContain('artificial');
  });

  test('filters short words from title', () => {
    const post = { title: 'AI and the future' };
    const result = getMetaKeywords(post);
    // 'and', 'the', 'ai' (2 chars) should be filtered (length <= 3)
    expect(result).not.toContain('and');
    expect(result).not.toContain('the');
  });

  test('returns empty string when no seo_keywords and no title', () => {
    expect(getMetaKeywords({})).toBe('');
  });
});

// ---------------------------------------------------------------------------
// validatePost
// ---------------------------------------------------------------------------

describe('validatePost()', () => {
  test('null input returns isValid false with error', () => {
    const result = validatePost(null);
    expect(result.isValid).toBe(false);
    expect(result.errors).toContain('Post is null or undefined');
  });

  test('missing slug causes error', () => {
    const post = { title: 'Valid Title', slug: null, content: 'A'.repeat(100) };
    const result = validatePost(post);
    expect(result.isValid).toBe(false);
    expect(result.errors.some((e) => e.includes('slug'))).toBe(true);
  });

  test('Untitled title causes error', () => {
    const post = {
      title: 'Untitled',
      slug: 'my-slug',
      content: 'A'.repeat(100),
    };
    const result = validatePost(post);
    expect(result.isValid).toBe(false);
    expect(result.errors.some((e) => e.includes('title'))).toBe(true);
  });

  test('no excerpt and no content causes error', () => {
    const post = { title: 'Good Title', slug: 'good-slug' };
    const result = validatePost(post);
    expect(result.isValid).toBe(false);
    expect(
      result.errors.some((e) => e.includes('excerpt') || e.includes('content'))
    ).toBe(true);
  });

  test('title > 200 chars causes error', () => {
    const post = {
      title: 'A'.repeat(201),
      slug: 'my-slug',
      content: 'A'.repeat(100),
    };
    const result = validatePost(post);
    expect(result.errors.some((e) => e.includes('too long'))).toBe(true);
  });

  test('content < 100 chars causes error', () => {
    const post = {
      title: 'Good Title',
      slug: 'good-slug',
      content: 'Short.',
    };
    const result = validatePost(post);
    expect(result.errors.some((e) => e.includes('too short'))).toBe(true);
  });

  test('valid post returns isValid true with empty errors', () => {
    const post = {
      title: 'Valid Post Title',
      slug: 'valid-post-title',
      excerpt: 'A brief summary.',
      content: 'A'.repeat(100),
    };
    const result = validatePost(post);
    expect(result.isValid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  test('excerpt alone satisfies content requirement', () => {
    const post = {
      title: 'Valid Title',
      slug: 'valid-slug',
      excerpt: 'Excerpt present.',
    };
    const result = validatePost(post);
    // No content error
    expect(
      result.errors.some((e) => e.includes('excerpt') || e.includes('content'))
    ).toBe(false);
  });
});
