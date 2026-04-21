/**
 * Tests for components/TopNav.js
 *
 * Covers:
 * - Renders nav links
 * - a11y: issue #800 — Articles and Explore links do not have mismatched aria-label
 *   (visible text must be the accessible name per WCAG 2.5.3 Label in Name)
 * - Search icon opens search input
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import TopNavigation from '../TopNav';

// Mock next/link
jest.mock('next/link', () => {
  return ({ children, href, 'aria-label': ariaLabel, ...rest }) => (
    <a href={href} aria-label={ariaLabel} {...rest}>
      {children}
    </a>
  );
});

// Mock next/navigation
const mockPush = jest.fn();
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => '/',
}));

describe('TopNavigation', () => {
  it('renders Articles link', () => {
    render(<TopNavigation />);
    expect(screen.getByRole('link', { name: 'Articles' })).toBeInTheDocument();
  });

  it('renders About link', () => {
    render(<TopNavigation />);
    expect(screen.getByRole('link', { name: 'About' })).toBeInTheDocument();
  });

  it('renders Explore link', () => {
    render(<TopNavigation />);
    expect(screen.getByRole('link', { name: 'Explore' })).toBeInTheDocument();
  });

  it('renders home logo link', () => {
    render(<TopNavigation />);
    expect(
      screen.getByRole('link', { name: 'Glad Labs — Home' })
    ).toBeInTheDocument();
  });

  it('renders skip-to-main link', () => {
    render(<TopNavigation />);
    expect(screen.getByText('Skip to main content')).toBeInTheDocument();
  });

  it('renders search button', () => {
    render(<TopNavigation />);
    expect(screen.getByRole('button', { name: 'Open search' })).toBeInTheDocument();
  });

  it('opens search input when search button is clicked', () => {
    render(<TopNavigation />);
    fireEvent.click(screen.getByRole('button', { name: 'Open search' }));
    expect(screen.getByRole('searchbox', { name: 'Search articles' })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #800: links must not have aria-label that overrides visible text
// ---------------------------------------------------------------------------

describe('TopNavigation — a11y: link accessible names match visible text (issue #800)', () => {
  it('Articles link has no aria-label (accessible name is visible text)', () => {
    render(<TopNavigation />);
    const articlesLink = screen.getByRole('link', { name: 'Articles' });
    expect(articlesLink).not.toHaveAttribute('aria-label');
  });

  it('Explore link has no aria-label (accessible name is visible text)', () => {
    render(<TopNavigation />);
    const exploreLink = screen.getByRole('link', { name: 'Explore' });
    expect(exploreLink).not.toHaveAttribute('aria-label');
  });

  it('About link has no aria-label (accessible name is visible text)', () => {
    render(<TopNavigation />);
    const aboutLink = screen.getByRole('link', { name: 'About' });
    expect(aboutLink).not.toHaveAttribute('aria-label');
  });

  it('Articles accessible name matches visible text exactly', () => {
    const { container } = render(<TopNavigation />);
    const articlesLink = container.querySelector('a[href="/archive/1"]');
    // First /archive/1 link is Articles
    expect(articlesLink.textContent.trim()).toBe('Articles');
    expect(articlesLink).not.toHaveAttribute('aria-label');
  });
});
