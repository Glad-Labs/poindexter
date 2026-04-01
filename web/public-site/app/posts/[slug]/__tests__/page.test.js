/**
 * Post Detail Page Tests (app/posts/[slug]/page.tsx)
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

describe('Post Detail Page', () => {
  describe('valid slug', () => {
    beforeEach(() => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ data: SAMPLE_POST }),
      });
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

    it('fetches the post from the correct URL', async () => {
      await PostPage({ params: Promise.resolve({ slug: 'ai-in-healthcare' }) });
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/posts/ai-in-healthcare'),
        expect.any(Object)
      );
    });
  });

  describe('unknown slug (404)', () => {
    it('calls notFound() when API returns 404', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => null,
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
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: SAMPLE_POST }),
    });

    const metadata = await generateMetadata({
      params: Promise.resolve({ slug: 'ai-in-healthcare' }),
    });

    expect(metadata.title).toContain('AI in Healthcare');
  });

  it('returns not-found metadata for unknown slug', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => null,
    });

    const metadata = await generateMetadata({
      params: Promise.resolve({ slug: 'does-not-exist' }),
    });

    expect(metadata.title).toMatch(/not found/i);
  });
});
