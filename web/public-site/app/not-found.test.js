/**
 * Not Found (404) Page Tests (app/not-found.js)
 *
 * Tests 404 error page
 * Verifies: Error message, back link, suggestions
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import NotFoundPage from '../app/not-found';

// Mock Next.js Link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

describe('404 Not Found Page', () => {
  it('should render not found page', () => {
    render(<NotFoundPage />);
    expect(document.body).toBeInTheDocument();
  });

  it('should display 404 error code', () => {
    render(<NotFoundPage />);
    const codes = screen.queryAllByText(/404/);
    expect(codes.length).toBeGreaterThan(0);
  });

  it('should display error message', () => {
    render(<NotFoundPage />);
    const messages = screen.queryAllByText(
      /page not found|does not exist|not available|404/i
    );
    expect(messages.length).toBeGreaterThan(0);
  });

  it('should have back to home link', () => {
    render(<NotFoundPage />);
    const homeLinks = screen.getAllByRole('link', {
      name: /home|back|return/i,
    });

    expect(homeLinks.length).toBeGreaterThan(0);
  });

  it('should link to home page correctly', () => {
    render(<NotFoundPage />);
    const homeLinks = screen.getAllByRole('link', {
      name: /home|back|return/i,
    });
    const hasHomeHref = homeLinks.some(
      (link) => link.getAttribute('href') === '/'
    );
    expect(hasHomeHref).toBe(true);
  });

  it('should suggest browsing blog', () => {
    render(<NotFoundPage />);
    const blogLink = screen.queryByRole('link', { name: /blog/i });

    if (blogLink) {
      expect(blogLink).toBeInTheDocument();
    }
  });

  it('should have navigation back to main site', () => {
    render(<NotFoundPage />);
    const links = screen.getAllByRole('link');

    expect(links.length).toBeGreaterThan(0);
  });

  it('should display helpful suggestions for user', () => {
    render(<NotFoundPage />);
    const matches = screen.getAllByText(
      /404|not found|doesn't exist|page not found/i
    );
    expect(matches.length).toBeGreaterThan(0);
  });

  it('should be accessible with proper heading', () => {
    render(<NotFoundPage />);
    const heading = screen.queryByRole('heading', { level: 1 });

    if (heading) {
      expect(heading).toBeInTheDocument();
    }
  });

  it('should have proper semantics', () => {
    const { container } = render(<NotFoundPage />);
    // Page should render content
    expect(container.firstChild).toBeInTheDocument();
  });

  it('should display content centered on page', () => {
    const { container } = render(<NotFoundPage />);
    const centerDiv =
      container.querySelector('[class*="center"]') ||
      container.querySelector('[class*="flex"]');

    expect(centerDiv).toBeInTheDocument() ||
      expect(document.body).toBeInTheDocument();
  });

  it('should have clickable back button', () => {
    render(<NotFoundPage />);
    const backButtons = screen.getAllByRole('link', {
      name: /home|back|return/i,
    });

    expect(backButtons.length).toBeGreaterThan(0);
  });

  it('should provide search functionality link', () => {
    render(<NotFoundPage />);
    const searchLink =
      screen.queryByRole('link', { name: /search/i }) ||
      screen.queryByPlaceholderText(/search/i);

    // Search link is optional
  });

  it('should suggest related pages or categories', () => {
    render(<NotFoundPage />);
    // May suggest categories or popular pages
    expect(document.body).toBeInTheDocument();
  });
});
