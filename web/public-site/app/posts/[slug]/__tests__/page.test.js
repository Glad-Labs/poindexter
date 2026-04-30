/**
 * Post Detail Page Tests (app/posts/[slug]/page.tsx)
 *
 * The page now uses getPostBySlug() from lib/posts.ts which fetches
 * from static JSON on R2/CDN at /posts/{slug}.json.
 *
 * Covers:
 * - Valid slug: renders post title, excerpt, view count
 * - Unknown slug: calls notFound()
 * - generateMetadata: returns correct title and description for known post
 * - generateMetadata: returns "not found" metadata for unknown slug
 * - API error: returns null, calls notFound()
 */

import React from 'react';
import { render, screen } from '@testing-library/react';

// Mock next/link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props) => <img {...props} />,
}));

// Mock next/navigation
const mockNotFound = jest.fn(() => {
  throw new Error('NEXT_NOT_FOUND');
});
jest.mock('next/navigation', () => ({
  notFound: mockNotFound,
}));

// Mock @sentry/nextjs
jest.mock('@sentry/nextjs', () => ({
  captureMessage: jest.fn(),
  captureException: jest.fn(),
}));

// Mock sanitize-html to pass through content and expose .defaults.allowedTags
const mockSanitizeHtml = jest.fn((html) => html);
mockSanitizeHtml.defaults = { allowedTags: [] };
jest.mock('sanitize-html', () => mockSanitizeHtml);

// Mock StructuredData components
jest.mock('@/components/StructuredData', () => ({
  BlogPostingSchema: () => null,
  BreadcrumbSchema: () => null,
}));

// Mock GiscusWrapper
jest.mock('@/components/GiscusWrapper', () => ({
  GiscusWrapper: () => <div data-testid="giscus-wrapper" />,
}));

// Mock lib/structured-data
jest.mock('@/lib/structured-data', () => ({
  generateBlogPostingSchema: jest.fn(() => null),
}));

// Mock lib/seo
jest.mock('@/lib/seo', () => ({
  buildMetaDescription: jest.fn((d) => d),
  buildSEOTitle: jest.fn((t) => t),
  generateCanonicalURL: jest.fn(
    (slug) => `https://www.gladlabs.io/posts/${slug}`
  ),
}));

// Mock logger
jest.mock('@/lib/logger', () => ({
  __esModule: true,
  default: {
    error: jest.fn(),
    warn: jest.fn(),
    info: jest.fn(),
    log: jest.fn(),
  },
}));

// Mock react cache — just call through the passed function
jest.mock('react', () => ({
  ...jest.requireActual('react'),
  cache: (fn) => fn,
}));

const SAMPLE_POST = {
  id: 'post-1',
  title: 'AI in Healthcare',
  slug: 'ai-in-healthcare',
  content: '<p>AI is transforming healthcare.</p>',
  excerpt: 'How AI is changing medicine.',
  view_count: 42,
  created_at: '2026-01-15T00:00:00Z',
  published_at: '2026-01-15T00:00:00Z',
  seo_title: 'AI in Healthcare | Glad Labs',
  seo_description: 'Learn how AI is transforming healthcare.',
  status: 'published',
  updated_at: '2026-01-15T00:00:00Z',
};

let PostPage;
let generateMetadata;

beforeAll(async () => {
  const mod = await import('../page');
  PostPage = mod.default;
  generateMetadata = mod.generateMetadata;
});

beforeEach(() => {
  jest.clearAllMocks();
});

/**
 * Helper: mock fetch to handle both index.json (for related posts) and slug.json calls.
 * The page calls getPostBySlug (slug.json) and getRelatedPosts (index.json).
 */
function mockFetchForPost(post, allPosts = []) {
  global.fetch = jest.fn().mockImplementation((url) => {
    if (url.includes('/posts/index.json')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          posts: allPosts,
          total: allPosts.length,
          exported_at: '2026-01-15T00:00:00Z',
        }),
      });
    }
    // posts/{slug}.json
    if (post) {
      return Promise.resolve({
        ok: true,
        json: async () => post,
      });
    }
    return Promise.resolve({
      ok: false,
      status: 404,
    });
  });
}

describe('Post Detail Page', () => {
  describe('valid slug', () => {
    beforeEach(() => {
      mockFetchForPost(SAMPLE_POST);
    });

    it('renders the post title', async () => {
      const jsx = await PostPage({
        params: Promise.resolve({ slug: 'ai-in-healthcare' }),
      });
      render(jsx);
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
        'AI in Healthcare'
      );
    });

    it('renders the post excerpt', async () => {
      const jsx = await PostPage({
        params: Promise.resolve({ slug: 'ai-in-healthcare' }),
      });
      render(jsx);
      expect(
        screen.getByText(/How AI is changing medicine/i)
      ).toBeInTheDocument();
    });

    it('renders view count', async () => {
      const jsx = await PostPage({
        params: Promise.resolve({ slug: 'ai-in-healthcare' }),
      });
      render(jsx);
      expect(screen.getByText(/42 views/i)).toBeInTheDocument();
    });

    it('fetches the post from the correct static JSON URL', async () => {
      await PostPage({ params: Promise.resolve({ slug: 'ai-in-healthcare' }) });
      const calls = global.fetch.mock.calls.map((c) => c[0]);
      expect(
        calls.some((url) => url.includes('/posts/ai-in-healthcare.json'))
      ).toBe(true);
    });
  });

  describe('unknown slug (404)', () => {
    it('calls notFound() when static JSON returns 404', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 404,
      });

      await expect(
        PostPage({ params: Promise.resolve({ slug: 'does-not-exist' }) })
      ).rejects.toThrow('NEXT_NOT_FOUND');

      expect(mockNotFound).toHaveBeenCalled();
    });
  });

  describe('API error', () => {
    it('calls notFound() when fetch throws', async () => {
      global.fetch = jest.fn().mockRejectedValue(new Error('Network error'));

      await expect(
        PostPage({ params: Promise.resolve({ slug: 'ai-in-healthcare' }) })
      ).rejects.toThrow('NEXT_NOT_FOUND');

      expect(mockNotFound).toHaveBeenCalled();
    });
  });
});

describe('generateMetadata', () => {
  it('returns correct title for a known post', async () => {
    mockFetchForPost(SAMPLE_POST);

    const metadata = await generateMetadata({
      params: Promise.resolve({ slug: 'ai-in-healthcare' }),
    });

    expect(metadata.title).toContain('AI in Healthcare');
  });

  it('returns not-found metadata for unknown slug', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
    });

    const metadata = await generateMetadata({
      params: Promise.resolve({ slug: 'does-not-exist' }),
    });

    expect(metadata.title).toMatch(/not found/i);
  });
});
