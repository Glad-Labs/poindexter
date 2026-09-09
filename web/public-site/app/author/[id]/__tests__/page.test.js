/**
 * Author Page Tests (app/author/[id]/page.tsx)
 *
 * Covers:
 * - Known author renders their name and bio
 * - Unknown author renders the default profile
 * - Page has proper heading hierarchy (h1)
 * - Back to articles link present
 * - Articles section heading present
 */

import React from 'react';
import { render, screen } from '@testing-library/react';

// Mock fetch — getPostsByAuthor calls fetchPostIndex which uses fetch.
// The page wraps calls in try/catch so a simple empty-list response suffices.
// Two static files are fetched: posts/index.json and authors.json. Route by
// URL so the slug→author_id join (glad-labs-stack#3339) is exercised.
const AUTHOR_ID = '6d9ec6c3-acaa-4907-9057-97713b24d5b7';
global.fetch = jest.fn().mockImplementation(async (url) => {
  if (String(url).endsWith('/authors.json')) {
    return {
      ok: true,
      status: 200,
      json: async () => [{ id: AUTHOR_ID, name: 'Poindexter AI' }],
    };
  }
  return {
    ok: true,
    status: 200,
    json: async () => ({
      posts: [
        {
          id: 'p1',
          title: 'A post by the byline',
          slug: 'a-post-by-the-byline',
          content: '',
          author_id: AUTHOR_ID,
          status: 'published',
          published_at: '2026-01-02T00:00:00Z',
          created_at: '2026-01-02T00:00:00Z',
          updated_at: '2026-01-02T00:00:00Z',
          view_count: 0,
        },
      ],
      total: 1,
      exported_at: '2026-01-01T00:00:00Z',
    }),
  };
});

// Mock next/link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

// Mock next/navigation
jest.mock('next/navigation', () => ({
  notFound: jest.fn(() => {
    throw new Error('NEXT_NOT_FOUND');
  }),
}));

let AuthorPage;

beforeAll(async () => {
  const mod = await import('../page');
  AuthorPage = mod.default;
});

// Helper to render the async server component
async function renderAuthorPage(id) {
  const jsx = await AuthorPage({ params: Promise.resolve({ id }) });
  return render(jsx);
}

describe('Author Page', () => {
  describe('known author (poindexter-ai)', () => {
    it('renders the author name as h1', async () => {
      const { container } = await renderAuthorPage('poindexter-ai');
      expect(container.querySelector('h1')).toHaveTextContent('Poindexter AI');
    });

    it('renders the author bio', async () => {
      await renderAuthorPage('poindexter-ai');
      expect(
        screen.getByText(/AI Content Generation Engine/i)
      ).toBeInTheDocument();
    });

    it('has back to articles link', async () => {
      await renderAuthorPage('poindexter-ai');
      expect(
        screen.getAllByRole('link', { name: /all articles/i })[0]
      ).toBeInTheDocument();
    });

    it('has articles section heading', async () => {
      await renderAuthorPage('poindexter-ai');
      expect(
        screen.getByText(/Articles by Poindexter AI/i)
      ).toBeInTheDocument();
    });
  });

  describe('author → posts join (glad-labs-stack#3339)', () => {
    it('lists posts whose author_id matches the profile slug via authors.json', async () => {
      const Page = (await import('../page')).default;
      render(await Page({ params: Promise.resolve({ id: 'poindexter-ai' }) }));
      expect(screen.getByText('A post by the byline')).toBeInTheDocument();
      expect(
        screen.queryByText(/hasn't published anything yet/i)
      ).not.toBeInTheDocument();
    });

    it('renders the empty state for a slug that matches no exported author', async () => {
      const Page = (await import('../page')).default;
      render(await Page({ params: Promise.resolve({ id: 'nobody-here' }) }));
      expect(
        screen.queryByText('A post by the byline')
      ).not.toBeInTheDocument();
    });
  });

  describe('unknown author (falls back to default)', () => {
    it('renders the default author name', async () => {
      const { container } = await renderAuthorPage('nonexistent-author');
      expect(container.querySelector('h1')).toHaveTextContent('Glad Labs');
    });

    it('renders the default author bio', async () => {
      await renderAuthorPage('nonexistent-author');
      expect(
        screen.getByText(/Where AI meets thoughtful content creation/i)
      ).toBeInTheDocument();
    });
  });

  describe('page structure', () => {
    it('renders without crashing', async () => {
      const { container } = await renderAuthorPage('poindexter-ai');
      expect(container.firstChild).toBeTruthy();
    });

    it('has proper heading hierarchy with h1 and h2', async () => {
      const { container } = await renderAuthorPage('poindexter-ai');
      expect(container.querySelector('h1')).toBeInTheDocument();
      expect(container.querySelector('h2')).toBeInTheDocument();
    });
  });
});
