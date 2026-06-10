/**
 * Dev Diary Page Tests (app/dev-diary/page.tsx)
 *
 * Covers:
 * - Page renders without crashing
 * - Heading hierarchy (h1 via Display, h2 sr-only)
 * - Empty state shows a fallback card
 * - Posts are rendered when dev_diary data is returned
 * - Each post card links to /posts/<slug>
 */

import React from 'react';
import { render, screen } from '@testing-library/react';

// Mock next/link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

// Mock next/image
jest.mock('next/image', () => {
  return ({ src, alt, ...props }) => <img src={src} alt={alt} />;
});

// Mock @glad-labs/brand components used by the page
jest.mock('@glad-labs/brand', () => ({
  Card: Object.assign(
    ({ children, className, accent }) => (
      <div className={className} data-accent={accent}>{children}</div>
    ),
    {
      Meta: ({ children }) => <span>{children}</span>,
      Title: ({ children }) => <h3>{children}</h3>,
      Body: ({ children, className }) => <p className={className}>{children}</p>,
      Tag: ({ children }) => <span>{children}</span>,
    },
  ),
  Display: Object.assign(
    ({ children }) => <h1>{children}</h1>,
    {
      Accent: ({ children }) => <span>{children}</span>,
    },
  ),
  Eyebrow: ({ children }) => <p>{children}</p>,
  Button: ({ children, href, as: As, ...props }) =>
    As ? <As href={href} {...props}>{children}</As> : <button {...props}>{children}</button>,
}));

const DEV_DIARY_POSTS = [
  {
    id: '1',
    title: 'Day 1: Starting out',
    slug: 'day-1-starting-out',
    excerpt: 'First day notes.',
    niche_slug: 'dev_diary',
    published_at: '2026-06-01T00:00:00Z',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    status: 'published',
    view_count: 0,
    content: '',
  },
  {
    id: '2',
    title: 'Day 2: Progress',
    slug: 'day-2-progress',
    excerpt: 'Second day notes.',
    niche_slug: 'dev_diary',
    published_at: '2026-06-02T00:00:00Z',
    created_at: '2026-06-02T00:00:00Z',
    updated_at: '2026-06-02T00:00:00Z',
    status: 'published',
    view_count: 0,
    content: '',
  },
];

// Mock lib/posts — getDevDiaryPosts is the key export used by this page
jest.mock('@/lib/posts', () => ({
  getDevDiaryPosts: jest.fn(),
  postFeaturedImage: jest.fn(() => null),
  cleanPostTitle: jest.fn((t) => t),
  postExcerpt: jest.fn((post) => post.excerpt || null),
}));

const { getDevDiaryPosts } = require('@/lib/posts');

let DevDiaryPage;

beforeAll(async () => {
  const mod = await import('../page');
  DevDiaryPage = mod.default;
});

beforeEach(() => {
  jest.clearAllMocks();
});

// Helper to render the async server component
async function renderPage() {
  const jsx = await DevDiaryPage();
  return render(jsx);
}

describe('Dev Diary Page', () => {
  describe('with posts', () => {
    beforeEach(() => {
      getDevDiaryPosts.mockResolvedValue(DEV_DIARY_POSTS);
    });

    it('renders without crashing', async () => {
      const { container } = await renderPage();
      expect(container.firstChild).toBeTruthy();
    });

    it('has a h1 heading', async () => {
      const { container } = await renderPage();
      expect(container.querySelector('h1')).toBeInTheDocument();
    });

    it('renders post cards for each dev diary entry', async () => {
      await renderPage();
      expect(screen.getByText('Day 1: Starting out')).toBeInTheDocument();
      expect(screen.getByText('Day 2: Progress')).toBeInTheDocument();
    });

    it('links each post to its /posts/<slug> URL', async () => {
      await renderPage();
      const links = screen.getAllByRole('link');
      const hrefs = links.map((l) => l.getAttribute('href'));
      expect(hrefs).toContain('/posts/day-1-starting-out');
      expect(hrefs).toContain('/posts/day-2-progress');
    });
  });

  describe('empty state', () => {
    beforeEach(() => {
      getDevDiaryPosts.mockResolvedValue([]);
    });

    it('shows the empty state message', async () => {
      await renderPage();
      expect(screen.getByText(/nothing here yet/i)).toBeInTheDocument();
    });

    it('does not render any post cards', async () => {
      const { container } = await renderPage();
      expect(container.querySelector('h3')).toBeNull();
    });
  });

  describe('fetch error handling', () => {
    beforeEach(() => {
      getDevDiaryPosts.mockRejectedValue(new Error('R2 unavailable'));
    });

    it('renders empty state gracefully on fetch error', async () => {
      await renderPage();
      expect(screen.getByText(/nothing here yet/i)).toBeInTheDocument();
    });
  });
});
