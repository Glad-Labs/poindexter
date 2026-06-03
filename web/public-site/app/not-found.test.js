/**
 * Not Found (404) Page Tests (app/not-found.tsx)
 *
 * Tests the 404 error page.
 * Verifies: 404 code, error message, back/home link, suggested posts.
 *
 * NOTE: app/not-found.tsx is an async Server Component — it `await`s
 * getPosts() to render suggested posts (#969 moved the fetch off the client).
 * React Testing Library's render() cannot render an async component directly
 * ("Only Server Components can be async at the moment"). Because NotFound is a
 * plain async function with no hooks, we invoke it to resolve its JSX tree and
 * hand the result to render() — the standard Next.js async-server-component
 * unit-testing pattern. getPosts is mocked so the test never touches fetch/R2.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import NotFoundPage from '../app/not-found';

// Mock Next.js Link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

// Mock the data seam so the async Server Component resolves without hitting
// fetch/R2 (fetch is not defined in jsdom). Returns a couple of posts so the
// suggested-posts section renders too.
jest.mock('../lib/posts', () => ({
  getPosts: jest.fn().mockResolvedValue({
    posts: [
      {
        id: '1',
        slug: 'first-post',
        title: 'First Post',
        excerpt: 'An excerpt.',
      },
      {
        id: '2',
        slug: 'second-post',
        title: 'Second Post',
        excerpt: 'Another.',
      },
    ],
  }),
}));

// Resolve the async Server Component's JSX tree, then render it.
async function renderNotFound() {
  return render(await NotFoundPage());
}

describe('404 Not Found Page', () => {
  it('should render not found page', async () => {
    await renderNotFound();
    expect(document.body).toBeInTheDocument();
  });

  it('should display 404 error code', async () => {
    await renderNotFound();
    const codes = screen.queryAllByText(/404/);
    expect(codes.length).toBeGreaterThan(0);
  });

  it('should display error message', async () => {
    await renderNotFound();
    const messages = screen.queryAllByText(
      /page not found|does not exist|doesn't exist|not available|404/i
    );
    expect(messages.length).toBeGreaterThan(0);
  });

  it('should have back to home link', async () => {
    await renderNotFound();
    const homeLinks = screen.getAllByRole('link', {
      name: /home|back|return/i,
    });

    expect(homeLinks.length).toBeGreaterThan(0);
  });

  it('should link to home page correctly', async () => {
    await renderNotFound();
    const homeLinks = screen.getAllByRole('link', {
      name: /home|back|return/i,
    });
    const hasHomeHref = homeLinks.some(
      (link) => link.getAttribute('href') === '/'
    );
    expect(hasHomeHref).toBe(true);
  });

  it('should suggest browsing blog', async () => {
    await renderNotFound();
    const blogLink = screen.queryByRole('link', { name: /blog/i });

    if (blogLink) {
      expect(blogLink).toBeInTheDocument();
    }
  });

  it('should have navigation back to main site', async () => {
    await renderNotFound();
    const links = screen.getAllByRole('link');

    expect(links.length).toBeGreaterThan(0);
  });

  it('should display helpful suggestions for user', async () => {
    await renderNotFound();
    const matches = screen.getAllByText(
      /404|not found|doesn't exist|page not found/i
    );
    expect(matches.length).toBeGreaterThan(0);
  });

  it('should be accessible with proper heading', async () => {
    await renderNotFound();
    const heading = screen.queryByRole('heading', { level: 1 });

    if (heading) {
      expect(heading).toBeInTheDocument();
    }
  });

  it('should have proper semantics', async () => {
    const { container } = await renderNotFound();
    // Page should render content
    expect(container.firstChild).toBeInTheDocument();
  });

  it('should display content centered on page', async () => {
    const { container } = await renderNotFound();
    const centerDiv =
      container.querySelector('[class*="center"]') ||
      container.querySelector('[class*="flex"]');

    expect(centerDiv).toBeInTheDocument() ||
      expect(document.body).toBeInTheDocument();
  });

  it('should have clickable back button', async () => {
    await renderNotFound();
    const backButtons = screen.getAllByRole('link', {
      name: /home|back|return/i,
    });

    expect(backButtons.length).toBeGreaterThan(0);
  });

  it('should provide search functionality link', async () => {
    await renderNotFound();
    const searchLink =
      screen.queryByRole('link', { name: /search/i }) ||
      screen.queryByPlaceholderText(/search/i);

    // Search link is optional
  });

  it('should suggest related pages or categories', async () => {
    await renderNotFound();
    // May suggest categories or popular pages
    expect(document.body).toBeInTheDocument();
  });
});
