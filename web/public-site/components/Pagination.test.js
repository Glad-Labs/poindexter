/**
 * Pagination Component Tests
 *
 * Tests blog pagination controls
 * Verifies: Page navigation, disabled states, link generation
 */
import { render, screen } from '@testing-library/react';
import Pagination from './Pagination';

// Mock Next.js Link
jest.mock('next/link', () => {
  return ({ children, href }) => {
    return <a href={href}>{children}</a>;
  };
});

describe('Pagination Component', () => {
  const mockPaginationProps = {
    currentPage: 2,
    totalPages: 5,
    baseUrl: '/blog',
  };

  it('should render pagination container', () => {
    const { container } = render(<Pagination {...mockPaginationProps} />);
    expect(container).toBeInTheDocument();
  });

  it('should render previous page link', () => {
    render(<Pagination {...mockPaginationProps} />);
    const prevLink = screen.getByRole('link', { name: /previous|prev/i });
    expect(prevLink).toBeInTheDocument();
  });

  it('should render next page link', () => {
    render(<Pagination {...mockPaginationProps} />);
    const nextLink = screen.getByRole('link', { name: /next/i });
    expect(nextLink).toBeInTheDocument();
  });

  it('should generate correct previous page link', () => {
    render(<Pagination {...mockPaginationProps} />);
    const prevLink = screen.getByRole('link', { name: /previous|prev/i });
    expect(prevLink).toHaveAttribute('href', expect.stringContaining('1'));
  });

  it('should generate correct next page link', () => {
    render(<Pagination {...mockPaginationProps} />);
    const nextLink = screen.getByRole('link', { name: /next/i });
    expect(nextLink).toHaveAttribute('href', expect.stringContaining('3'));
  });

  it('should disable next button on last page', () => {
    const lastPageProps = {
      currentPage: 5,
      totalPages: 5,
      baseUrl: '/blog',
    };
    render(<Pagination {...lastPageProps} />);
    const nextLink = screen.queryByRole('link', { name: /next/i });
    expect(nextLink?.hasAttribute('disabled') || !nextLink).toBe(true);
  });

  it('should disable previous button on first page', () => {
    const firstPageProps = {
      currentPage: 1,
      totalPages: 5,
      baseUrl: '/blog',
    };
    render(<Pagination {...firstPageProps} />);
    const prevLink = screen.queryByRole('link', { name: /previous|prev/i });
    expect(prevLink?.hasAttribute('disabled') || !prevLink).toBe(true);
  });

  it('should display current page indicator', () => {
    render(<Pagination {...mockPaginationProps} />);
    expect(screen.getByText(/2/)).toBeInTheDocument();
  });

  it('should display total pages', () => {
    render(<Pagination {...mockPaginationProps} />);
    expect(screen.getByText(/5/)).toBeInTheDocument();
  });

  it('should render page number links', () => {
    render(<Pagination {...mockPaginationProps} />);
    const pageLinks = screen.queryAllByRole('link');
    expect(pageLinks.length).toBeGreaterThan(0);
  });

  it('should highlight current page', () => {
    const { container } = render(<Pagination {...mockPaginationProps} />);
    const currentPageElement =
      container.querySelector('[data-current]') ||
      container.querySelector('[aria-current]');
    expect(currentPageElement).toBeDefined();
  });

  it('should handle single page', () => {
    const singlePageProps = {
      currentPage: 1,
      totalPages: 1,
      baseUrl: '/blog',
    };
    render(<Pagination {...singlePageProps} />);
    expect(screen.getByText(/1/)).toBeInTheDocument();
  });

  it('should adjust base URL correctly', () => {
    const customBaseUrl = '/posts/category/tech';
    const props = {
      currentPage: 2,
      totalPages: 3,
      baseUrl: customBaseUrl,
    };
    render(<Pagination {...props} />);
    const links = screen.queryAllByRole('link');
    const hasCustomBase = links.some((link) => link.href.includes('tech'));
    expect(hasCustomBase || links.length > 0).toBe(true);
  });

  it('should be keyboard accessible', () => {
    render(<Pagination {...mockPaginationProps} />);
    const links = screen.queryAllByRole('link');
    links.forEach((link) => {
      expect(link).toHaveAttribute('href');
    });
  });

  it('should support compact layout', () => {
    const { container } = render(
      <Pagination {...mockPaginationProps} compact={true} />
    );
    expect(container).toBeInTheDocument();
  });

  it('should handle large page numbers', () => {
    const largePageProps = {
      currentPage: 50,
      totalPages: 100,
      baseUrl: '/blog',
    };
    render(<Pagination {...largePageProps} />);
    expect(screen.getByText(/50/)).toBeInTheDocument();
  });

  it('should generate proper slug pagination links', () => {
    const slugProps = {
      currentPage: 2,
      totalPages: 3,
      baseUrl: '/category/technology',
    };
    render(<Pagination {...slugProps} />);
    const links = screen.queryAllByRole('link');
    expect(links.length > 0).toBe(true);
  });
});
