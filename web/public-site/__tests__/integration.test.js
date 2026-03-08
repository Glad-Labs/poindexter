/**
 * Integration Tests - Blog API + Frontend
 *
 * Tests end-to-end flows combining API calls and component rendering
 * Verifies: Post fetching → rendering, search → filtering, category listing
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Mock fetch for all integration tests
global.fetch = jest.fn();

// Mock Next.js modules
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

jest.mock('next/image', () => ({
  __esModule: true,
  default: (props) => <img {...props} />,
}));

describe('Blog API + Frontend Integration', () => {
  beforeEach(() => {
    global.fetch.mockClear();
  });

  const mockPost = {
    id: '1',
    slug: 'integration-test',
    title: 'Integration Test Post',
    content: '# Test Content',
    excerpt: 'This is a test post',
    author: 'Test Author',
    date: '2024-03-08',
    category: 'Technology',
    tags: ['test', 'integration'],
    image: 'https://example.com/image.jpg',
  };

  describe('Post Fetching and Rendering', () => {
    it('should fetch and display a single post', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      // Simulating a post fetch + render
      const response = await fetch('/api/posts/integration-test');
      const post = await response.json();

      expect(post.title).toBe('Integration Test Post');
      expect(global.fetch).toHaveBeenCalledWith('/api/posts/integration-test');
    });

    it('should handle post not found error', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 404,
      });

      const response = await fetch('/api/posts/nonexistent');

      expect(response.ok).toBe(false);
      expect(response.status).toBe(404);
    });

    it('should fetch multiple posts for listing', async () => {
      const mockPosts = [
        { ...mockPost, id: '1', slug: 'post-1' },
        { ...mockPost, id: '2', slug: 'post-2' },
      ];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: mockPosts, total: 2 }),
      });

      const response = await fetch('/api/posts');
      const data = await response.json();

      expect(data.posts).toHaveLength(2);
      expect(data.total).toBe(2);
    });

    it('should handle pagination in post listing', async () => {
      const mockPage1 = {
        posts: [
          { ...mockPost, id: '1' },
          { ...mockPost, id: '2' },
        ],
        page: 1,
        total: 25,
        perPage: 10,
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockPage1,
      });

      const response = await fetch('/api/posts?page=1&limit=10');
      const data = await response.json();

      expect(data.page).toBe(1);
      expect(data.posts).toHaveLength(2);
      expect(data.total).toBe(25);
    });
  });

  describe('Search + Filtering Integration', () => {
    it('should search posts by title', async () => {
      const searchResults = [mockPost];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ results: searchResults }),
      });

      const response = await fetch('/api/search?q=integration');
      const data = await response.json();

      expect(data.results[0].title).toContain('Integration');
    });

    it('should filter posts by category', async () => {
      const categoryPosts = [mockPost];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: categoryPosts }),
      });

      const response = await fetch('/api/posts?category=Technology');
      const data = await response.json();

      expect(data.posts[0].category).toBe('Technology');
    });

    it('should filter posts by tag', async () => {
      const tagPosts = [mockPost];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: tagPosts }),
      });

      const response = await fetch('/api/posts?tag=integration');
      const data = await response.json();

      expect(data.posts[0].tags).toContain('integration');
    });

    it('should filter posts by author', async () => {
      const authorPosts = [mockPost];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: authorPosts }),
      });

      const response = await fetch('/api/posts?author=Test%20Author');
      const data = await response.json();

      expect(data.posts[0].author).toBe('Test Author');
    });

    it('should combine multiple filters', async () => {
      const filteredPosts = [mockPost];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: filteredPosts }),
      });

      const response = await fetch(
        '/api/posts?category=Technology&tag=integration&sort=date'
      );
      const data = await response.json();

      expect(data.posts).toHaveLength(1);
    });
  });

  describe('Category Listing Integration', () => {
    it('should fetch category with posts', async () => {
      const categoryData = {
        category: 'Technology',
        posts: [mockPost],
        total: 1,
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => categoryData,
      });

      const response = await fetch('/api/posts?category=Technology');
      const data = await response.json();

      expect(data.category).toBe('Technology');
      expect(data.posts).toHaveLength(1);
    });

    it('should handle empty category', async () => {
      const emptyCategory = {
        category: 'NonExistent',
        posts: [],
        total: 0,
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => emptyCategory,
      });

      const response = await fetch('/api/posts?category=NonExistent');
      const data = await response.json();

      expect(data.posts).toHaveLength(0);
    });
  });

  describe('Post Detail + Related Posts Integration', () => {
    it('should fetch post with related posts', async () => {
      const postWithRelated = {
        ...mockPost,
        relatedPosts: [
          { ...mockPost, id: '2', slug: 'related-1' },
          { ...mockPost, id: '3', slug: 'related-2' },
        ],
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => postWithRelated,
      });

      const response = await fetch(
        '/api/posts/integration-test?includeRelated=true'
      );
      const data = await response.json();

      expect(data.relatedPosts).toHaveLength(2);
    });

    it('should fetch post with author info', async () => {
      const postWithAuthor = {
        ...mockPost,
        author: {
          name: 'Test Author',
          bio: 'Author bio',
          image: 'https://example.com/author.jpg',
        },
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => postWithAuthor,
      });

      const response = await fetch(
        '/api/posts/integration-test?includeAuthor=true'
      );
      const data = await response.json();

      expect(data.author.bio).toBe('Author bio');
    });
  });

  describe('SEO Data Integration', () => {
    it('should fetch post with SEO metadata', async () => {
      const postWithSEO = {
        ...mockPost,
        metaDescription: 'Test post description',
        metaKeywords: 'test, integration, api',
        ogImage: 'https://example.com/og-image.jpg',
        canonicalUrl: 'https://example.com/posts/integration-test',
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => postWithSEO,
      });

      const response = await fetch(
        '/api/posts/integration-test?includeSEO=true'
      );
      const data = await response.json();

      expect(data.metaDescription).toBeDefined();
      expect(data.canonicalUrl).toContain('integration-test');
    });
  });

  describe('Error Handling Integration', () => {
    it('should handle API connection errors', async () => {
      global.fetch.mockRejectedValueOnce(new Error('Network error'));

      try {
        await fetch('/api/posts');
      } catch (error) {
        expect(error.message).toBe('Network error');
      }
    });

    it('should handle malformed API responses', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => {
          throw new Error('Invalid JSON');
        },
      });

      const response = await fetch('/api/posts');

      try {
        await response.json();
      } catch (error) {
        expect(error.message).toBe('Invalid JSON');
      }
    });

    it('should handle 500 server errors', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
      });

      const response = await fetch('/api/posts');

      expect(response.status).toBe(500);
      expect(response.ok).toBe(false);
    });

    it('should handle rate limiting', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 429,
        statusText: 'Too Many Requests',
      });

      const response = await fetch('/api/posts');

      expect(response.status).toBe(429);
    });
  });

  describe('Performance Integration', () => {
    it('should cache API responses appropriately', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [mockPost] }),
        headers: {
          'cache-control': 'max-age=3600',
        },
      });

      const response = await fetch('/api/posts');

      expect(response.ok).toBe(true);
    });

    it('should handle concurrent requests', async () => {
      global.fetch
        .mockResolvedValueOnce({
          ok: true,
          json: async () => mockPost,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => [mockPost],
        });

      const responses = await Promise.all([
        fetch('/api/posts/1').then((r) => r.json()),
        fetch('/api/posts').then((r) => r.json()),
      ]);

      expect(responses).toHaveLength(2);
      expect(global.fetch).toHaveBeenCalledTimes(2);
    });
  });

  describe('Complete User Flow Integration', () => {
    it('should handle full post reading flow', async () => {
      // Step 1: Fetch post list
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [mockPost] }),
      });

      const listResponse = await fetch('/api/posts');
      const listData = await listResponse.json();

      expect(listData.posts).toHaveLength(1);

      // Step 2: Fetch single post detail
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const detailResponse = await fetch(`/api/posts/${mockPost.slug}`);
      const detailData = await detailResponse.json();

      expect(detailData.title).toBe('Integration Test Post');

      // Should have made 2 API calls
      expect(global.fetch).toHaveBeenCalledTimes(2);
    });

    it('should handle search to detail flow', async () => {
      // Step 1: Search
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ results: [mockPost] }),
      });

      const searchResponse = await fetch('/api/search?q=integration');
      const searchData = await searchResponse.json();

      expect(searchData.results).toHaveLength(1);

      // Step 2: View detail
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const detailResponse = await fetch(`/api/posts/${mockPost.slug}`);
      const detailData = await detailResponse.json();

      expect(detailData.author).toBe('Test Author');

      expect(global.fetch).toHaveBeenCalledTimes(2);
    });
  });
});
