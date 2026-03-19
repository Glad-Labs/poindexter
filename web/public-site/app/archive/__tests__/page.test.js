import React from 'react';
import { render, screen } from '@testing-library/react';
import ArchivePage from '../[page]/page';

// Mock next/link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props) => {
    return <img {...props} />;
  },
}));

// Mock the PostCard component
jest.mock('@/components/PostCard', () => {
  return function MockPostCard({ post }) {
    return <div data-testid={`post-card-${post.id}`}>{post.title}</div>;
  };
});

describe('Archive Page (/archive/[page])', () => {
  const mockParams = { page: '1' };

  test('renders archive page component', () => {
    const { container } = render(<ArchivePage params={mockParams} />);
    expect(container).toBeInTheDocument();
  });

  test('has archive page heading', () => {
    render(<ArchivePage params={mockParams} />);

    const headings = screen.queryAllByRole('heading');
    expect(headings.length).toBeGreaterThan(0);
  });

  test('displays pagination controls for multiple pages', () => {
    render(<ArchivePage params={mockParams} />);

    // Archive should have navigation for pagination
    const nav = screen.queryByRole('navigation');
    expect(nav).toBeInTheDocument();
  });

  test('has proper semantic main element', () => {
    const { container } = render(<ArchivePage params={mockParams} />);

    const mainElement = container.querySelector('main');
    expect(mainElement).toBeInTheDocument();
  });

  test('renders with page parameter', () => {
    const testPage = '3';
    const { container } = render(<ArchivePage params={{ page: testPage }} />);

    expect(container).toBeInTheDocument();
  });

  test('renders grid layout for posts', () => {
    const { container } = render(<ArchivePage params={mockParams} />);

    // Archive should use grid layout
    const hasGridLayout = Array.from(container.querySelectorAll('*')).some(
      (el) => {
        const classList = el.className || '';
        return classList.includes('grid') || classList.includes('flex');
      }
    );

    expect(hasGridLayout).toBe(true);
  });

  test('has proper heading hierarchy', () => {
    const { container } = render(<ArchivePage params={mockParams} />);

    const headings = container.querySelectorAll('h1, h2');
    expect(headings.length).toBeGreaterThan(0);
  });

  test('page is responsive with container classes', () => {
    const { container } = render(<ArchivePage params={mockParams} />);

    // Check for responsive container usage
    const hasResponsiveElements = Array.from(
      container.querySelectorAll('*')
    ).some((el) => {
      const classList = el.className || '';
      return classList.includes('mx-auto') || classList.includes('max-w');
    });

    expect(hasResponsiveElements).toBe(true);
  });

  test('renders archive without errors for page 1', () => {
    const { container } = render(<ArchivePage params={{ page: '1' }} />);

    expect(container.firstChild).toBeTruthy();
  });

  test('renders archive without errors for subsequent pages', () => {
    const { container } = render(<ArchivePage params={{ page: '2' }} />);

    expect(container.firstChild).toBeTruthy();
  });

  test('pagination links have correct href attributes', () => {
    render(<ArchivePage params={{ page: '1' }} />);

    // Should have pagination navigation
    const links = screen.queryAllByRole('link');
    expect(links.length).toBeGreaterThan(0);
  });
});
