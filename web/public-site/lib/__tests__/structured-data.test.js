/**
 * Tests for lib/structured-data.js
 *
 * Covers all exported schema generation functions:
 * - generateBlogPostingSchema
 * - generateNewsArticleSchema
 * - generateArticleSchema
 * - generateBreadcrumbSchema
 * - generateOrganizationSchema
 * - generateWebsiteSchema
 * - combineSchemas
 * - generateFAQPageSchema
 * - generateArticleReviewSchema
 * - validateSchema
 */

// Mock logger to suppress console noise in tests
jest.mock('../logger', () => ({
  error: jest.fn(),
  warn: jest.fn(),
  info: jest.fn(),
}));

// Mock api-fastapi to avoid real URL construction depending on env
jest.mock('../api-fastapi', () => ({
  getImageURL: jest.fn((path) =>
    path ? `https://api.www.gladlabs.io${path}` : null
  ),
}));

import {
  generateArticleReviewSchema,
  generateArticleSchema,
  generateBlogPostingSchema,
  generateBreadcrumbSchema,
  generateFAQPageSchema,
  generateNewsArticleSchema,
  generateOrganizationSchema,
  generateWebsiteSchema,
  combineSchemas,
  validateSchema,
} from '../structured-data';

const SITE_URL = 'https://www.gladlabs.io';

const SAMPLE_POST = {
  title: 'AI Trends in 2026',
  slug: 'ai-trends-2026',
  excerpt: 'A deep dive into AI trends.',
  content: 'Lorem ipsum '.repeat(20),
  date: '2026-03-12',
  coverImage: { url: '/images/ai-trends.jpg' },
  category: { name: 'Technology' },
};

// ---------------------------------------------------------------------------
// generateBlogPostingSchema
// ---------------------------------------------------------------------------

describe('generateBlogPostingSchema()', () => {
  test('returns null when post is null', () => {
    expect(generateBlogPostingSchema(null)).toBeNull();
  });

  test('returns object with @context and @type', () => {
    const schema = generateBlogPostingSchema(SAMPLE_POST);
    expect(schema['@context']).toBe('https://schema.org');
    expect(schema['@type']).toBe('BlogPosting');
  });

  test('headline equals post title', () => {
    const schema = generateBlogPostingSchema(SAMPLE_POST);
    expect(schema.headline).toBe(SAMPLE_POST.title);
  });

  test('description equals post excerpt', () => {
    const schema = generateBlogPostingSchema(SAMPLE_POST);
    expect(schema.description).toBe(SAMPLE_POST.excerpt);
  });

  test('mainEntityOfPage contains slug URL', () => {
    const schema = generateBlogPostingSchema(SAMPLE_POST, SITE_URL);
    expect(schema.mainEntityOfPage['@id']).toContain(SAMPLE_POST.slug);
  });

  test('keywords include category name', () => {
    const schema = generateBlogPostingSchema(SAMPLE_POST);
    expect(schema.keywords).toContain('Technology');
  });

  test('keywords empty when no category', () => {
    const schema = generateBlogPostingSchema({
      ...SAMPLE_POST,
      category: null,
    });
    expect(schema.keywords).toEqual([]);
  });

  test('wordCount calculated from content', () => {
    const schema = generateBlogPostingSchema(SAMPLE_POST);
    expect(schema.wordCount).toBeGreaterThan(0);
  });

  test('wordCount is 0 when no content', () => {
    const schema = generateBlogPostingSchema({
      ...SAMPLE_POST,
      content: undefined,
    });
    expect(schema.wordCount).toBe(0);
  });

  test('uses og-image.png when no coverImage', () => {
    const schema = generateBlogPostingSchema({
      ...SAMPLE_POST,
      coverImage: undefined,
    });
    expect(schema.image.url).toContain('og-image.png');
  });

  test('publisher is Organization', () => {
    const schema = generateBlogPostingSchema(SAMPLE_POST);
    expect(schema.publisher['@type']).toBe('Organization');
  });

  test('uses publishedAt as fallback for date', () => {
    const post = { ...SAMPLE_POST, date: undefined, publishedAt: '2026-01-01' };
    const schema = generateBlogPostingSchema(post);
    expect(schema.datePublished).toContain('2026-01-01');
  });
});

// ---------------------------------------------------------------------------
// generateNewsArticleSchema
// ---------------------------------------------------------------------------

describe('generateNewsArticleSchema()', () => {
  test('returns null when post is null', () => {
    expect(generateNewsArticleSchema(null)).toBeNull();
  });

  test('returns @type NewsArticle', () => {
    const schema = generateNewsArticleSchema(SAMPLE_POST);
    expect(schema['@type']).toBe('NewsArticle');
  });

  test('headline and description populated', () => {
    const schema = generateNewsArticleSchema(SAMPLE_POST);
    expect(schema.headline).toBe(SAMPLE_POST.title);
    expect(schema.description).toBe(SAMPLE_POST.excerpt);
  });

  test('uses og-image.png when no coverImage', () => {
    const schema = generateNewsArticleSchema({
      ...SAMPLE_POST,
      coverImage: undefined,
    });
    expect(schema.image).toContain('og-image.png');
  });
});

// ---------------------------------------------------------------------------
// generateArticleSchema
// ---------------------------------------------------------------------------

describe('generateArticleSchema()', () => {
  test('returns null when post is null', () => {
    expect(generateArticleSchema(null)).toBeNull();
  });

  test('returns @type Article', () => {
    const schema = generateArticleSchema(SAMPLE_POST);
    expect(schema['@type']).toBe('Article');
  });

  test('author is Organization', () => {
    const schema = generateArticleSchema(SAMPLE_POST);
    expect(schema.author['@type']).toBe('Organization');
  });
});

// ---------------------------------------------------------------------------
// generateBreadcrumbSchema
// ---------------------------------------------------------------------------

describe('generateBreadcrumbSchema()', () => {
  test('returns null for empty array', () => {
    expect(generateBreadcrumbSchema([])).toBeNull();
  });

  test('returns null when no items passed', () => {
    expect(generateBreadcrumbSchema()).toBeNull();
  });

  test('returns BreadcrumbList schema', () => {
    const items = [
      { name: 'Home', url: '/' },
      { name: 'Blog', url: '/blog' },
    ];
    const schema = generateBreadcrumbSchema(items);
    expect(schema['@type']).toBe('BreadcrumbList');
  });

  test('item positions are 1-indexed', () => {
    const items = [
      { name: 'Home', url: '/' },
      { name: 'AI', url: '/ai' },
    ];
    const schema = generateBreadcrumbSchema(items);
    expect(schema.itemListElement[0].position).toBe(1);
    expect(schema.itemListElement[1].position).toBe(2);
  });

  test('item URLs include siteUrl', () => {
    const schema = generateBreadcrumbSchema(
      [{ name: 'Blog', url: '/blog' }],
      SITE_URL
    );
    expect(schema.itemListElement[0].item).toBe(`${SITE_URL}/blog`);
  });

  test('item count matches input', () => {
    const items = [
      { name: 'Home', url: '/' },
      { name: 'Blog', url: '/blog' },
      { name: 'Post', url: '/post' },
    ];
    const schema = generateBreadcrumbSchema(items);
    expect(schema.itemListElement).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// generateOrganizationSchema
// ---------------------------------------------------------------------------

describe('generateOrganizationSchema()', () => {
  test('returns @type Organization', () => {
    const schema = generateOrganizationSchema();
    expect(schema['@type']).toBe('Organization');
  });

  test('default name is Glad Labs', () => {
    const schema = generateOrganizationSchema();
    expect(schema.name).toBe('Glad Labs');
  });

  test('custom name applied', () => {
    const schema = generateOrganizationSchema(SITE_URL, { name: 'Acme Corp' });
    expect(schema.name).toBe('Acme Corp');
  });

  test('sameAs defaults to empty array', () => {
    const schema = generateOrganizationSchema();
    expect(schema.sameAs).toEqual([]);
  });

  test('phone excluded when empty', () => {
    const schema = generateOrganizationSchema(SITE_URL, { phone: '' });
    expect(schema.phone).toBeUndefined();
  });

  test('phone included when provided', () => {
    const schema = generateOrganizationSchema(SITE_URL, {
      phone: '+1-800-555-0100',
    });
    expect(schema.phone).toBe('+1-800-555-0100');
  });
});

// ---------------------------------------------------------------------------
// generateWebsiteSchema
// ---------------------------------------------------------------------------

describe('generateWebsiteSchema()', () => {
  test('returns @type WebSite', () => {
    const schema = generateWebsiteSchema();
    expect(schema['@type']).toBe('WebSite');
  });

  test('potentialAction is SearchAction', () => {
    const schema = generateWebsiteSchema();
    expect(schema.potentialAction['@type']).toBe('SearchAction');
  });

  test('search URL template contains siteUrl', () => {
    const schema = generateWebsiteSchema(SITE_URL);
    expect(schema.potentialAction.target.urlTemplate).toContain(SITE_URL);
  });
});

// ---------------------------------------------------------------------------
// combineSchemas
// ---------------------------------------------------------------------------

describe('combineSchemas()', () => {
  test('returns null for empty array', () => {
    expect(combineSchemas([])).toBeNull();
  });

  test('single schema returned as-is', () => {
    const single = { '@context': 'https://schema.org', '@type': 'WebSite' };
    expect(combineSchemas([single])).toBe(single);
  });

  test('multiple schemas wrapped in @graph', () => {
    const a = { '@type': 'WebSite' };
    const b = { '@type': 'Organization' };
    const result = combineSchemas([a, b]);
    expect(result['@graph']).toHaveLength(2);
    expect(result['@context']).toBe('https://schema.org');
  });
});

// ---------------------------------------------------------------------------
// generateFAQPageSchema
// ---------------------------------------------------------------------------

describe('generateFAQPageSchema()', () => {
  test('returns null for empty array', () => {
    expect(generateFAQPageSchema([])).toBeNull();
  });

  test('returns null when no argument passed', () => {
    expect(generateFAQPageSchema()).toBeNull();
  });

  test('returns FAQPage schema', () => {
    const faqs = [
      { question: 'What is AI?', answer: 'Artificial Intelligence.' },
    ];
    const schema = generateFAQPageSchema(faqs);
    expect(schema['@type']).toBe('FAQPage');
  });

  test('mainEntity contains Question items', () => {
    const faqs = [{ question: 'What is AI?', answer: 'AI is amazing.' }];
    const schema = generateFAQPageSchema(faqs);
    expect(schema.mainEntity[0]['@type']).toBe('Question');
    expect(schema.mainEntity[0].name).toBe('What is AI?');
  });
});

// ---------------------------------------------------------------------------
// generateArticleReviewSchema
// ---------------------------------------------------------------------------

describe('generateArticleReviewSchema()', () => {
  test('returns null when post is null', () => {
    expect(generateArticleReviewSchema(null)).toBeNull();
  });

  test('without rating returns BlogPosting schema', () => {
    const schema = generateArticleReviewSchema(SAMPLE_POST, null);
    expect(schema['@type']).toBe('BlogPosting');
    expect(schema.reviewRating).toBeUndefined();
  });

  test('with rating adds reviewRating', () => {
    const schema = generateArticleReviewSchema(SAMPLE_POST, { value: 4.5 });
    expect(schema.reviewRating['@type']).toBe('Rating');
    expect(schema.reviewRating.ratingValue).toBe(4.5);
  });

  test('rating defaults: bestRating=5, worstRating=1', () => {
    const schema = generateArticleReviewSchema(SAMPLE_POST, { value: 3 });
    expect(schema.reviewRating.bestRating).toBe(5);
    expect(schema.reviewRating.worstRating).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// validateSchema
// ---------------------------------------------------------------------------

describe('validateSchema()', () => {
  test('returns false for null', () => {
    expect(validateSchema(null)).toBe(false);
  });

  test('returns false when @context missing', () => {
    expect(validateSchema({ '@type': 'WebSite' })).toBe(false);
  });

  test('returns false when @type missing', () => {
    expect(validateSchema({ '@context': 'https://schema.org' })).toBe(false);
  });

  test('returns true for valid schema', () => {
    expect(
      validateSchema({ '@context': 'https://schema.org', '@type': 'WebSite' })
    ).toBe(true);
  });
});
