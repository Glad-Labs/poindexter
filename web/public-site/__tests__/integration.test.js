/**
 * Integration Tests - Blog Data → Component Rendering
 *
 * Tests end-to-end flows from API data shapes through React component rendering.
 * Each test mocks a realistic API response and renders the corresponding component,
 * then asserts that the data is correctly displayed in the DOM.
 *
 * Replaces the previous version which only tested raw fetch() responses
 * without rendering any React components (issues #902, #617).
 */
import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';

// Mock Next.js modules
jest.mock('next/link', () => {
  return ({ children, href, ...props }) => (
    <a href={href} {...props}>
      {children}
    </a>
  );
});

jest.mock('next/image', () => ({
  __esModule: true,
  default: ({ alt, src, ...props }) => <img alt={alt} src={src} {...props} />,
}));

// Import components under test
import PostCard from '../components/PostCard';
import RelatedPosts, { RelatedPostsList } from '../components/RelatedPosts';
import Pagination from '../components/Pagination';
import { AuthorCard } from '../components/AuthorCard';
import { PostNavigation } from '../components/PostNavigation';
import { PostCategories } from '../components/PostCategories';

// Realistic mock data matching the shapes returned by the backend API
const mockApiPost = {
  id: '1',
  title: 'Building AI Agents with FastAPI',
  slug: 'building-ai-agents-fastapi',
  excerpt: 'Learn how to build **production-ready** AI agents using FastAPI',
  content: '# Building AI Agents\n\nThis is the full content.',
  featured_image_url: 'https://cdn.example.com/ai-agents.jpg',
  cover_image_url: 'https://cdn.example.com/ai-agents.jpg',
  author_id: 'poindexter-ai',
  category_id: 'technology',
  status: 'published',
  published_at: '2024-06-15T10:00:00Z',
  created_at: '2024-06-10T08:00:00Z',
  updated_at: '2024-06-15T12:00:00Z',
  view_count: 142,
  seo_title: 'Building AI Agents | Glad Labs',
  seo_description: 'A guide to building production AI agents',
  seo_keywords: 'ai,agents,fastapi',
};

const mockRelatedPost = {
  id: '2',
  title: 'Scaling Agent Pipelines',
  slug: 'scaling-agent-pipelines',
  excerpt: 'How to scale your multi-agent system',
  publishedAt: '2024-05-20T09:00:00Z',
  tags: ['agents', 'scaling'],
};

describe('Post Listing → Component Rendering', () => {
  it('should render multiple PostCards from a post list API response', () => {
    const apiResponse = {
      posts: [
        { ...mockApiPost, id: '1', slug: 'post-1', title: 'First Post' },
        { ...mockApiPost, id: '2', slug: 'post-2', title: 'Second Post' },
        { ...mockApiPost, id: '3', slug: 'post-3', title: 'Third Post' },
      ],
      total: 3,
    };

    const { container } = render(
      <div>
        {apiResponse.posts.map((post) => (
          <PostCard key={post.id} post={post} />
        ))}
      </div>
    );

    expect(screen.getByText('First Post')).toBeInTheDocument();
    expect(screen.getByText('Second Post')).toBeInTheDocument();
    expect(screen.getByText('Third Post')).toBeInTheDocument();

    const links = container.querySelectorAll('a[href^="/posts/"]');
    expect(links.length).toBeGreaterThanOrEqual(3);
  });

  it('should render post with formatted date from API timestamp', () => {
    render(<PostCard post={mockApiPost} />);

    // The published_at timestamp should be formatted as a human-readable date
    expect(screen.getByText(/June|Jun/)).toBeInTheDocument();
  });

  it('should render markdown-formatted excerpt text', () => {
    render(<PostCard post={mockApiPost} />);

    // The **bold** markdown should be rendered with font-semibold styling
    const boldEl = screen.getByText('production-ready');
    expect(boldEl.tagName).toBe('STRONG');
  });

  it('should render post card with featured image', () => {
    render(<PostCard post={mockApiPost} />);

    const img = screen.getByAltText(/cover image for/i);
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute('src', mockApiPost.featured_image_url);
  });

  it('should handle post without optional fields', () => {
    const minimalPost = {
      id: '99',
      title: 'Minimal Post',
      slug: 'minimal',
      status: 'published',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
      view_count: 0,
    };

    const { container } = render(<PostCard post={minimalPost} />);
    expect(screen.getByText('Minimal Post')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });
});

describe('Post Detail → Related Posts Rendering', () => {
  it('should render related posts from API response data', () => {
    const relatedPosts = [
      { ...mockRelatedPost, id: '2', title: 'Related One', slug: 'related-1' },
      { ...mockRelatedPost, id: '3', title: 'Related Two', slug: 'related-2' },
    ];

    render(<RelatedPosts posts={relatedPosts} />);

    expect(screen.getByText('Related Articles')).toBeInTheDocument();
    expect(screen.getByText('Related One')).toBeInTheDocument();
    expect(screen.getByText('Related Two')).toBeInTheDocument();
  });

  it('should render related posts as clickable links to post slugs', () => {
    const relatedPosts = [
      {
        ...mockRelatedPost,
        id: '4',
        title: 'Linked Post',
        slug: 'linked-post',
      },
    ];

    render(<RelatedPosts posts={relatedPosts} />);

    const link = screen.getByRole('link', { name: /linked post/i });
    expect(link).toHaveAttribute('href', '/posts/linked-post');
  });

  it('should display tag count on related post cards', () => {
    const relatedPosts = [
      {
        ...mockRelatedPost,
        id: '5',
        title: 'Tagged Post',
        slug: 'tagged',
        tags: ['a', 'b', 'c'],
      },
    ];

    render(<RelatedPosts posts={relatedPosts} />);
    expect(screen.getByText('3 tags')).toBeInTheDocument();
  });

  it('should render nothing when related posts array is empty', () => {
    const { container } = render(<RelatedPosts posts={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('should render compact list variant for sidebar', () => {
    const posts = [
      { ...mockRelatedPost, id: '6', title: 'Sidebar Post', slug: 'sidebar' },
    ];

    render(<RelatedPostsList posts={posts} maxItems={5} />);
    expect(screen.getByText('Sidebar Post')).toBeInTheDocument();
  });
});

describe('Pagination → Navigation Rendering', () => {
  it('should render page numbers from API pagination metadata', () => {
    const paginationData = { page: 2, pageCount: 5 };

    render(<Pagination pagination={paginationData} />);

    // All 5 page numbers should be present — use getAllByLabelText since
    // "page 2" appears in both current-page and "Go to page" labels
    for (let i = 1; i <= 5; i++) {
      const matches = screen.getAllByLabelText(new RegExp(`page ${i}`, 'i'));
      expect(matches.length).toBeGreaterThanOrEqual(1);
    }
  });

  it('should highlight the current page', () => {
    render(<Pagination pagination={{ page: 3, pageCount: 5 }} />);

    const current = screen.getByLabelText(/current page, page 3/i);
    expect(current).toHaveAttribute('aria-current', 'page');
  });

  it('should render previous/next navigation links', () => {
    render(<Pagination pagination={{ page: 2, pageCount: 5 }} />);

    expect(screen.getByLabelText(/go to previous page/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/go to next page/i)).toBeInTheDocument();
  });

  it('should not render previous link on first page', () => {
    render(<Pagination pagination={{ page: 1, pageCount: 5 }} />);

    expect(screen.queryByLabelText(/go to previous page/i)).toBeNull();
    expect(screen.getByLabelText(/go to next page/i)).toBeInTheDocument();
  });

  it('should not render next link on last page', () => {
    render(<Pagination pagination={{ page: 5, pageCount: 5 }} />);

    expect(screen.getByLabelText(/go to previous page/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/go to next page/i)).toBeNull();
  });

  it('should not render pagination when there is only one page', () => {
    const { container } = render(
      <Pagination pagination={{ page: 1, pageCount: 1 }} />
    );
    expect(container.firstChild).toBeNull();
  });
});

describe('Post Detail → Author Card Rendering', () => {
  it('should render author card from post author_id field', () => {
    // Simulate extracting author_id from API post data
    const postData = { ...mockApiPost, author_id: 'poindexter-ai' };

    render(
      <AuthorCard authorId={postData.author_id} authorName="Poindexter AI" />
    );

    expect(screen.getByText('Poindexter AI')).toBeInTheDocument();
    expect(
      screen.getByText('AI Content Generation Engine')
    ).toBeInTheDocument();
  });

  it('should render fallback author when post has no author_id', () => {
    const postData = { ...mockApiPost, author_id: undefined };

    render(<AuthorCard authorId={postData.author_id} />);

    expect(screen.getByText('Glad Labs')).toBeInTheDocument();
  });
});

describe('Post Detail → Navigation Rendering', () => {
  it('should render previous and next post navigation from adjacent posts', () => {
    // Simulate the previous/next posts returned alongside a post detail
    const prevPost = {
      ...mockApiPost,
      id: '0',
      title: 'Earlier Article',
      slug: 'earlier-article',
    };
    const nextPost = {
      ...mockApiPost,
      id: '2',
      title: 'Later Article',
      slug: 'later-article',
    };

    render(<PostNavigation previousPost={prevPost} nextPost={nextPost} />);

    expect(screen.getByText('Earlier Article')).toBeInTheDocument();
    expect(screen.getByText('Later Article')).toBeInTheDocument();

    const prevLink = screen.getByText('Earlier Article').closest('a');
    expect(prevLink).toHaveAttribute('href', '/posts/earlier-article');

    const nextLink = screen.getByText('Later Article').closest('a');
    expect(nextLink).toHaveAttribute('href', '/posts/later-article');
  });
});

describe('Post Detail → Category Rendering', () => {
  it('should render category from post category_id field', () => {
    const postData = {
      ...mockApiPost,
      category_id: 'technology',
    };

    render(
      <PostCategories
        categoryId={postData.category_id}
        categoryName="Technology"
      />
    );

    expect(screen.getByText('Technology')).toBeInTheDocument();
    expect(screen.getByRole('link')).toHaveAttribute(
      'href',
      '/category/technology'
    );
  });
});

describe('Error States → Component Resilience', () => {
  it('should render PostCard gracefully with missing excerpt', () => {
    const postWithoutExcerpt = { ...mockApiPost, excerpt: undefined };
    const { container } = render(<PostCard post={postWithoutExcerpt} />);
    expect(
      screen.getByText('Building AI Agents with FastAPI')
    ).toBeInTheDocument();
  });

  it('should render PostCard gracefully with missing date', () => {
    const postWithNoDate = {
      ...mockApiPost,
      published_at: undefined,
    };
    const { container } = render(<PostCard post={postWithNoDate} />);
    expect(container).toBeInTheDocument();
    expect(screen.getByText(mockApiPost.title)).toBeInTheDocument();
  });

  it('should render PostNavigation with only one adjacent post', () => {
    render(<PostNavigation previousPost={mockApiPost} nextPost={null} />);
    expect(
      screen.getByText('Building AI Agents with FastAPI')
    ).toBeInTheDocument();
  });

  it('should render PostNavigation as null when no adjacent posts exist', () => {
    const { container } = render(
      <PostNavigation previousPost={null} nextPost={null} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('should render PostCategories as null when category is missing', () => {
    const { container } = render(<PostCategories />);
    expect(container.firstChild).toBeNull();
  });
});
