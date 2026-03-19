/**
 * Pagination Component Tests
 *
 * Tests blog pagination controls
 * Verifies: Page navigation, disabled states, link generation
 */
import { render, screen } from '@testing-library/react';
import Pagination from '../Pagination';

// Mock Next.js Link
jest.mock('next/link', () => {
  return ({ children, href }) => {
    return <a href={href}>{children}</a>;
  };
});

describe('Pagination Component', () => {
  const defaultProps = {
    pagination: { page: 2, pageCount: 5 },
    basePath: '/archive',
  };

  it('should render pagination container', () => {
    const { container } = render(<Pagination {...defaultProps} />);
    expect(container).toBeInTheDocument();
  });

  it('should render previous page link', () => {
    render(<Pagination {...defaultProps} />);
    const prevLink = screen.getByRole('link', { name: /previous|prev|←/i });
    expect(prevLink).toBeInTheDocument();
  });

  it('should render next page link', () => {
    render(<Pagination {...defaultProps} />);
    const nextLink = screen.getByRole('link', { name: /next|→/i });
    expect(nextLink).toBeInTheDocument();
  });

  it('should generate correct previous page link', () => {
    render(<Pagination {...defaultProps} />);
    const links = screen.getAllByRole('link');
    // Should have a link pointing to page 1
    const prevLink = links.find((l) => l.getAttribute('href')?.includes('/1'));
    expect(prevLink).toBeDefined();
  });

  it('should generate correct next page link', () => {
    render(<Pagination {...defaultProps} />);
    const links = screen.getAllByRole('link');
    // Should have a link pointing to page 3
    const nextLink = links.find((l) => l.getAttribute('href')?.includes('/3'));
    expect(nextLink).toBeDefined();
  });

  it('should not render next link on last page', () => {
    const lastPageProps = {
      pagination: { page: 5, pageCount: 5 },
      basePath: '/archive',
    };
    render(<Pagination {...lastPageProps} />);
    const links = screen.getAllByRole('link');
    // Should not have a link to page 6
    const nextLink = links.find((l) => l.getAttribute('href')?.includes('/6'));
    expect(nextLink).toBeUndefined();
  });

  it('should not render previous link on first page', () => {
    const firstPageProps = {
      pagination: { page: 1, pageCount: 5 },
      basePath: '/archive',
    };
    render(<Pagination {...firstPageProps} />);
    const links = screen.getAllByRole('link');
    // Should not have a link to page 0
    const prevLink = links.find((l) => l.getAttribute('href')?.includes('/0'));
    expect(prevLink).toBeUndefined();
  });

  it('should display current page indicator', () => {
    render(<Pagination {...defaultProps} />);
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('should display total pages', () => {
    render(<Pagination {...defaultProps} />);
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('should render page number links', () => {
    render(<Pagination {...defaultProps} />);
    const links = screen.queryAllByRole('link');
    expect(links.length).toBeGreaterThan(0);
  });

  it('should highlight current page with aria-current', () => {
    const { container } = render(<Pagination {...defaultProps} />);
    const currentPage = container.querySelector('[aria-current="page"]');
    expect(currentPage).toBeInTheDocument();
  });

  it('should return null for single page', () => {
    const singlePageProps = {
      pagination: { page: 1, pageCount: 1 },
      basePath: '/archive',
    };
    const { container } = render(<Pagination {...singlePageProps} />);
    // Component returns null when pageCount <= 1
    expect(container.querySelector('nav')).toBeNull();
  });

  it('should be keyboard accessible', () => {
    render(<Pagination {...defaultProps} />);
    const links = screen.queryAllByRole('link');
    links.forEach((link) => {
      expect(link).toHaveAttribute('href');
    });
  });

  it('should handle large page numbers', () => {
    const largePageProps = {
      pagination: { page: 50, pageCount: 100 },
      basePath: '/archive',
    };
    render(<Pagination {...largePageProps} />);
    expect(screen.getByText('50')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #794: Pagination ol does not have role=menubar
// ---------------------------------------------------------------------------

describe('Pagination — a11y: ol must not have role=menubar (issue #794)', () => {
  const validProps = { pagination: { page: 2, pageCount: 5 } };

  it('renders a nav landmark with aria-label="Pagination navigation"', () => {
    render(<Pagination {...validProps} />);
    const nav = screen.getByRole('navigation', {
      name: 'Pagination navigation',
    });
    expect(nav).toBeInTheDocument();
  });

  it('inner ol does not have role="menubar"', () => {
    const { container } = render(<Pagination {...validProps} />);
    const ol = container.querySelector('ol');
    expect(ol).toBeInTheDocument();
    expect(ol).not.toHaveAttribute('role', 'menubar');
  });

  it('inner ol does not have any ARIA role override', () => {
    const { container } = render(<Pagination {...validProps} />);
    const ol = container.querySelector('ol');
    expect(ol).not.toHaveAttribute('role');
  });
});
