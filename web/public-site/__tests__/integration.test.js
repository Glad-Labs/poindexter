/**
 * Integration Tests - Blog Data → Component Rendering
 *
 * Tests end-to-end flows from API data shapes through React component rendering.
 * Each test mocks a realistic API response and renders the corresponding component,
 * then asserts that the data is correctly displayed in the DOM.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';

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

describe('Error States → Component Resilience', () => {
  it('should render PostCard gracefully with missing excerpt', () => {
    const postWithoutExcerpt = { ...mockApiPost, excerpt: undefined };
    render(<PostCard post={postWithoutExcerpt} />);
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
});
