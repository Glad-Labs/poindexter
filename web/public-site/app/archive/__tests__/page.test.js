import React from 'react';
import { render, screen } from '@testing-library/react';

// Mock next/link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props) => {
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
    return <img {...props} />;
  },
}));

// Mock @sentry/nextjs
jest.mock('@sentry/nextjs', () => ({
  captureException: jest.fn(),
}));

// Mock the logger
jest.mock('@/lib/logger', () => ({
  __esModule: true,
  default: { error: jest.fn(), warn: jest.fn(), info: jest.fn() },
}));

// Mock global fetch to return static JSON shape from R2
const mockPosts = [
  {
    id: '1',
    title: 'Test Post One',
    slug: 'test-post-one',
    excerpt: 'An excerpt for post one',
    featured_image_url: '/images/test1.jpg',
    published_at: '2026-01-15T00:00:00Z',
    created_at: '2026-01-15T00:00:00Z',
    view_count: 42,
  },
  {
    id: '2',
    title: 'Test Post Two',
    slug: 'test-post-two',
    excerpt: 'An excerpt for post two',
    featured_image_url: null,
    published_at: '2026-01-10T00:00:00Z',
    created_at: '2026-01-10T00:00:00Z',
    view_count: 0,
  },
];

beforeEach(() => {
  global.fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          posts: mockPosts,
          total: 2,
          exported_at: '2026-01-15T00:00:00Z',
        }),
    })
  );
});

afterEach(() => {
  jest.restoreAllMocks();
});

// Dynamic import so the module picks up our mocks
let ArchivePage;
beforeAll(async () => {
  const mod = await import('../[page]/page');
  ArchivePage = mod.default;
});

describe('Archive Page (/archive/[page])', () => {
  const renderPage = async (page = '1') => {
    // The component is async — call it as a function and await the JSX
    const jsx = await ArchivePage({ params: Promise.resolve({ page }) });
    return render(jsx);
  };

  test('renders archive page component', async () => {
    const { container } = await renderPage();
    expect(container).toBeInTheDocument();
  });

  test('has archive page heading', async () => {
    await renderPage();
    const heading = screen.getByText('Article Archive');
    expect(heading).toBeInTheDocument();
  });

  test('renders post titles', async () => {
    await renderPage();
    expect(screen.getByText('Test Post One')).toBeInTheDocument();
    expect(screen.getByText('Test Post Two')).toBeInTheDocument();
  });

  test('has proper semantic structure with grid layout', async () => {
    const { container } = await renderPage();
    const hasGridLayout = Array.from(container.querySelectorAll('*')).some(
      (el) => {
        const classList = el.className || '';
        return classList.includes('grid');
      }
    );
    expect(hasGridLayout).toBe(true);
  });

  test('renders responsive container classes', async () => {
    const { container } = await renderPage();
    const hasResponsive = Array.from(container.querySelectorAll('*')).some(
      (el) => {
        const classList = el.className || '';
        return classList.includes('mx-auto') || classList.includes('max-w');
      }
    );
    expect(hasResponsive).toBe(true);
  });

  test('renders empty state when no posts', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            posts: [],
            total: 0,
            exported_at: '2026-01-15T00:00:00Z',
          }),
      })
    );
    await renderPage();
    expect(screen.getByText('No Articles Found')).toBeInTheDocument();
  });

  test('handles fetch failure gracefully', async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error('Network error')));
    await renderPage();
    expect(screen.getByText('No Articles Found')).toBeInTheDocument();
  });

  test('renders pagination when total exceeds page size', async () => {
    // 25 posts in index means 3 pages; we return first 10 in the slice
    const manyPosts = Array.from({ length: 25 }, (_, i) => ({
      ...mockPosts[0],
      id: String(i),
      slug: `post-${i}`,
    }));
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            posts: manyPosts,
            total: 25,
            exported_at: '2026-01-15T00:00:00Z',
          }),
      })
    );
    const { container } = await renderPage();
    const nav = container.querySelector('nav[aria-label="Archive pagination"]');
    expect(nav).toBeInTheDocument();
  });
});
