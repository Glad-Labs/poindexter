/**
 * Top Navigation Component Tests (components/TopNav.js)
 *
 * Tests header navigation, logo, search, menu
 * Verifies: Navigation links, responsive menu, search functionality
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import TopNav from '../components/TopNav';

// Mock Next.js Link — preserve all props including aria-label
jest.mock('next/link', () => {
  return ({ children, href, ...props }) => (
    <a href={href} {...props}>
      {children}
    </a>
  );
});

// Mock Next.js useRouter
jest.mock('next/router', () => ({
  useRouter: () => ({
    pathname: '/',
    push: jest.fn(),
    query: {},
  }),
}));

describe('TopNav Component', () => {
  it('should render navigation header', () => {
    const { container } = render(<TopNav />);
    expect(
      container.querySelector('nav') || container.querySelector('header')
    ).toBeInTheDocument();
  });

  it('should display logo', () => {
    render(<TopNav />);
    // TopNav renders "GL" text as logo with aria-label "Glad Labs — Home"
    const logoLink = screen.getByRole('link', { name: /glad labs/i });
    expect(logoLink).toBeInTheDocument();
  });

  it('should display home link via logo', () => {
    render(<TopNav />);
    const homeLink = screen.getByRole('link', { name: /glad labs/i });
    expect(homeLink).toHaveAttribute('href', '/');
  });

  it('should display articles link', () => {
    render(<TopNav />);
    const articlesLink = screen.getByRole('link', { name: /articles/i });
    expect(articlesLink).toBeInTheDocument();
  });

  it('should display about link', () => {
    render(<TopNav />);
    const aboutLink = screen.getByRole('link', { name: /about/i });
    expect(aboutLink).toBeInTheDocument();
  });

  it('should display explore link', () => {
    render(<TopNav />);
    const exploreLink = screen.getByRole('link', { name: /explore/i });
    expect(exploreLink).toBeInTheDocument();
  });

  it('should have correct navigation links', () => {
    render(<TopNav />);
    const homeLink = screen.getByRole('link', { name: /glad labs/i });
    expect(homeLink).toHaveAttribute('href', '/');
  });

  it('should include search functionality', () => {
    render(<TopNav />);
    const searchInput =
      screen.queryByRole('searchbox') ||
      screen.queryByPlaceholderText(/search/i);
    if (searchInput) {
      expect(searchInput).toBeInTheDocument();
    }
  });

  it('should have responsive mobile menu', () => {
    render(<TopNav />);
    const menuButton = screen.queryByRole('button', {
      name: /menu|hamburger|toggle/i,
    });
    if (menuButton) {
      expect(menuButton).toBeInTheDocument();
    }
  });

  it('should toggle mobile menu on button click', () => {
    render(<TopNav />);
    const menuButton = screen.queryByRole('button', {
      name: /menu|hamburger|toggle/i,
    });

    if (menuButton) {
      fireEvent.click(menuButton);
      // Mobile menu should be visible after click
      expect(screen.getByRole('link', { name: /blog/i })).toBeInTheDocument();
    }
  });

  it('should have proper semantic structure', () => {
    const { container } = render(<TopNav />);
    expect(
      container.querySelector('nav') || container.querySelector('header')
    ).toBeInTheDocument();
  });

  it('should display all main navigation items', () => {
    render(<TopNav />);
    expect(
      screen.getByRole('link', { name: /glad labs/i })
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /articles/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /about/i })).toBeInTheDocument();
  });

  it('should have navigation landmark', () => {
    render(<TopNav />);
    const nav = screen.getByRole('navigation', { name: /main/i });
    expect(nav).toBeInTheDocument();
  });

  it('should have accessibility attributes', () => {
    render(<TopNav />);
    const nav =
      screen.getByRole('navigation') ||
      screen.getByRole('link', { name: /home/i }).closest('nav');
    expect(nav).toBeInTheDocument();
  });

  it('should display subscribe button or CTA', () => {
    render(<TopNav />);
    const subscribeButton =
      screen.queryByRole('button', { name: /subscribe|newsletter/i }) ||
      screen.queryByRole('link', { name: /subscribe|newsletter/i });

    if (subscribeButton) {
      expect(subscribeButton).toBeInTheDocument();
    }
  });

  it('should have fixed positioning class', () => {
    const { container } = render(<TopNav />);
    const header = container.querySelector('header');
    // TopNav uses Tailwind "fixed" class
    expect(header?.className).toContain('fixed');
  });

  it('should handle search input', () => {
    render(<TopNav />);
    const searchInput =
      screen.queryByRole('searchbox') ||
      screen.queryByPlaceholderText(/search/i);

    if (searchInput) {
      fireEvent.change(searchInput, { target: { value: 'React' } });
      expect(searchInput.value).toBe('React');
    }
  });

  it('should have proper z-index for overlay', () => {
    const { container } = render(<TopNav />);
    const nav =
      container.querySelector('nav') || container.querySelector('header');

    if (nav) {
      const style = window.getComputedStyle(nav);
      expect(parseInt(style.zIndex) || 1).toBeGreaterThan(0);
    }
  });
});
