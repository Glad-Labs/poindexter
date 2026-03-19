/**
 * Posts API Tests (lib/posts.ts)
 *
 * Tests the FastAPI integration for fetching posts
 * Verifies: API calls, error handling, data transformation
 */
import { getPosts, getPostBySlug, getPostsByCategory } from '../lib/posts';

// Mock fetch
global.fetch = jest.fn();

describe('Posts API (lib/posts.ts)', () => {
  const mockPost = {
    id: 'post-1',
    title: 'Test Post',
    slug: 'test-post',
    content: '# Test Post\n\nContent here',
    status: 'published',
    created_at: '2024-01-15T10:00:00Z',
    updated_at: '2024-01-15T10:00:00Z',
    view_count: 42,
    excerpt: 'This is a test post',
    featured_image_url: 'https://example.com/image.jpg',
    author_id: 'author-1',
    category_id: 'tech',
    seo_title: 'Test Post SEO',
    seo_description: 'Description',
    seo_keywords: 'test,post',
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('getPosts', () => {
    it('should fetch published posts and return structured response', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [mockPost], total: 1 }),
      });

      const result = await getPosts();

      expect(result.posts).toEqual([mockPost]);
      expect(result.total).toBe(1);
      expect(result.page).toBe(1);
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/posts'),
        expect.any(Object)
      );
    });

    it('should handle pagination with page parameter', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [mockPost], total: 20 }),
      });

      const result = await getPosts(2);

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('offset='),
        expect.any(Object)
      );
      expect(result.page).toBe(2);
    });

    it('should filter by published status', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [mockPost], total: 1 }),
      });

      await getPosts();

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('status=published'),
        expect.any(Object)
      );
    });

    it('should return empty result on API error', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
      });

      const result = await getPosts();

      // getPosts catches errors and returns empty result
      expect(result.posts).toEqual([]);
      expect(result.total).toBe(0);
    });

    it('should return empty result on network error', async () => {
      (fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      const result = await getPosts();

      expect(result.posts).toEqual([]);
      expect(result.total).toBe(0);
    });

    it('should calculate totalPages correctly', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [mockPost], total: 25 }),
      });

      const result = await getPosts();

      expect(result.totalPages).toBeGreaterThan(0);
    });
  });

  describe('getPostBySlug', () => {
    it('should fetch a single post by slug', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const result = await getPostBySlug('test-post');

      expect(result).toBeDefined();
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('test-post'),
        expect.any(Object)
      );
    });

    it('should return null on post not found', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 404,
        statusText: 'Not Found',
      });

      const result = await getPostBySlug('nonexistent');

      expect(result).toBeNull();
    });

    it('should include post content', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const result = await getPostBySlug('test-post');

      expect(result?.content).toBeDefined();
    });
  });

  describe('getPostsByCategory', () => {
    it('should fetch posts by category slug', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [mockPost], total: 1 }),
      });

      const result = await getPostsByCategory('tech');

      expect(result).toBeDefined();
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('tech'),
        expect.any(Object)
      );
    });

    it('should return empty result for empty category', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: [], total: 0 }),
      });

      const result = await getPostsByCategory('empty-category');

      expect(result.posts).toEqual([]);
    });
  });
});
