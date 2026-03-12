/**
 * Tests for components/RelatedPosts.tsx
 *
 * Covers:
 * - RelatedPosts (default export) — null for empty, renders posts, calls onPostClick
 * - RelatedPostsList (named export) — navigation list, maxItems, empty state
 * - RelatedPostsFeatured (named export) — featured 2-column layout, maxItems
 */

import { render, screen, fireEvent } from '@testing-library/react';
import RelatedPosts, {
  RelatedPostsList,
  RelatedPostsFeatured,
} from '../RelatedPosts';

// Mock Next.js Link and Image components
jest.mock('next/link', () => {
  return function MockLink({ children, href, onClick }) {
    return (
      <a href={href} onClick={onClick}>
        {children}
      </a>
    );
  };
});

jest.mock('next/image', () => {
  return function MockImage({ src, alt, ...rest }) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt={alt} />;
  };
});

// Mock content-utils
jest.mock('../../lib/content-utils', () => ({
  formatDate: jest.fn((dateStr) => `Formatted: ${dateStr}`),
}));

const SAMPLE_POSTS = [
  {
    id: '1',
    title: 'Post Alpha',
    slug: 'post-alpha',
    excerpt: 'Excerpt for alpha post.',
    publishedAt: '2026-03-01T00:00:00Z',
    coverImage: { data: { attributes: { url: '/images/alpha.jpg' } } },
    category: { data: { attributes: { slug: 'tech', name: 'Technology' } } },
    tags: [{ id: 'tag-1' }, { id: 'tag-2' }],
  },
  {
    id: '2',
    title: 'Post Beta',
    slug: 'post-beta',
    excerpt: 'Excerpt for beta post.',
    publishedAt: '2026-02-15T00:00:00Z',
    coverImage: null,
    category: null,
    tags: [],
  },
  {
    id: '3',
    title: 'Post Gamma',
    slug: 'post-gamma',
    excerpt: 'Excerpt for gamma.',
    publishedAt: '2026-01-10T00:00:00Z',
  },
];

// ---------------------------------------------------------------------------
// RelatedPosts (default export)
// ---------------------------------------------------------------------------

describe('RelatedPosts', () => {
  test('returns null for empty posts array', () => {
    const { container } = render(<RelatedPosts posts={[]} />);
    expect(container.firstChild).toBeNull();
  });

  test('returns null when no posts prop', () => {
    const { container } = render(<RelatedPosts />);
    expect(container.firstChild).toBeNull();
  });

  test('renders Related Articles heading', () => {
    render(<RelatedPosts posts={SAMPLE_POSTS} />);
    expect(screen.getByText('Related Articles')).toBeInTheDocument();
  });

  test('renders a card for each post', () => {
    render(<RelatedPosts posts={SAMPLE_POSTS} />);
    expect(screen.getByText('Post Alpha')).toBeInTheDocument();
    expect(screen.getByText('Post Beta')).toBeInTheDocument();
    expect(screen.getByText('Post Gamma')).toBeInTheDocument();
  });

  test('renders post excerpt text', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[0]]} />);
    expect(screen.getByText('Excerpt for alpha post.')).toBeInTheDocument();
  });

  test('renders cover image when present', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[0]]} />);
    const img = screen.getByAltText('Cover image for: Post Alpha');
    expect(img).toBeInTheDocument();
  });

  test('does not render image when no coverImage', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[1]]} />);
    expect(screen.queryByRole('img')).toBeNull();
  });

  test('renders category badge when category present', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[0]]} />);
    expect(screen.getByText('Technology')).toBeInTheDocument();
  });

  test('does not render category when absent', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[1]]} />);
    expect(screen.queryByText('Technology')).toBeNull();
  });

  test('renders tag count when tags present', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[0]]} />);
    expect(screen.getByText(/2 tags/i)).toBeInTheDocument();
  });

  test('does not render tag count when no tags', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[1]]} />);
    expect(screen.queryByText(/tags/i)).toBeNull();
  });

  test('post links point to /posts/[slug]', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[0]]} />);
    const link = screen.getByRole('link', { name: /Post Alpha/i });
    expect(link.getAttribute('href')).toBe('/posts/post-alpha');
  });

  test('calls onPostClick with post when card is clicked', () => {
    const onPostClick = jest.fn();
    render(
      <RelatedPosts posts={[SAMPLE_POSTS[0]]} onPostClick={onPostClick} />
    );
    const link = screen.getAllByRole('link')[0];
    fireEvent.click(link);
    expect(onPostClick).toHaveBeenCalledWith(SAMPLE_POSTS[0]);
  });

  test('does not crash when onPostClick not provided', () => {
    render(<RelatedPosts posts={[SAMPLE_POSTS[0]]} />);
    const link = screen.getAllByRole('link')[0];
    expect(() => fireEvent.click(link)).not.toThrow();
  });

  test('section has correct role and aria-labelledby', () => {
    render(<RelatedPosts posts={SAMPLE_POSTS} />);
    const section = screen.getByRole('region', { name: /Related Articles/i });
    expect(section).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// RelatedPostsList
// ---------------------------------------------------------------------------

describe('RelatedPostsList', () => {
  test('returns null for empty posts', () => {
    const { container } = render(<RelatedPostsList posts={[]} />);
    expect(container.firstChild).toBeNull();
  });

  test('returns null when no posts prop', () => {
    const { container } = render(<RelatedPostsList />);
    expect(container.firstChild).toBeNull();
  });

  test('renders post titles as links', () => {
    render(<RelatedPostsList posts={SAMPLE_POSTS} />);
    expect(screen.getByText('Post Alpha')).toBeInTheDocument();
    expect(screen.getByText('Post Beta')).toBeInTheDocument();
  });

  test('respects maxItems prop', () => {
    render(<RelatedPostsList posts={SAMPLE_POSTS} maxItems={2} />);
    expect(screen.queryByText('Post Gamma')).toBeNull();
    expect(screen.getByText('Post Alpha')).toBeInTheDocument();
    expect(screen.getByText('Post Beta')).toBeInTheDocument();
  });

  test('default maxItems is 5', () => {
    const sixPosts = Array.from({ length: 6 }, (_, i) => ({
      id: String(i),
      title: `Post ${i}`,
      slug: `post-${i}`,
    }));
    render(<RelatedPostsList posts={sixPosts} />);
    // 5 posts should be visible (first 5)
    expect(screen.getByText('Post 0')).toBeInTheDocument();
    expect(screen.getByText('Post 4')).toBeInTheDocument();
    expect(screen.queryByText('Post 5')).toBeNull();
  });

  test('links point to /posts/[slug]', () => {
    render(<RelatedPostsList posts={[SAMPLE_POSTS[0]]} />);
    expect(screen.getByRole('link').getAttribute('href')).toBe(
      '/posts/post-alpha'
    );
  });

  test('renders publishedAt as formatted date when present', () => {
    render(<RelatedPostsList posts={[SAMPLE_POSTS[0]]} />);
    expect(screen.getByText(/Formatted:/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// RelatedPostsFeatured
// ---------------------------------------------------------------------------

describe('RelatedPostsFeatured', () => {
  test('returns null for empty posts', () => {
    const { container } = render(<RelatedPostsFeatured posts={[]} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders at most 2 posts by default', () => {
    render(<RelatedPostsFeatured posts={SAMPLE_POSTS} />);
    // 3 posts provided; default maxItems=2
    expect(screen.getByText('Post Alpha')).toBeInTheDocument();
    expect(screen.getByText('Post Beta')).toBeInTheDocument();
    expect(screen.queryByText('Post Gamma')).toBeNull();
  });

  test('respects custom maxItems', () => {
    render(<RelatedPostsFeatured posts={SAMPLE_POSTS} maxItems={3} />);
    expect(screen.getByText('Post Gamma')).toBeInTheDocument();
  });
});
