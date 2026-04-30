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
