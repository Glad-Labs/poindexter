/**
 * Posts API Tests (lib/posts.ts)
 *
 * Tests the FastAPI integration for fetching posts
 * Verifies: API calls, caching, error handling, data transformation
 */
import {
  getPosts,
  getPost,
  getPostsByCategory,
  getPostsByTag,
  getPostsByAuthor,
} from '../lib/posts';

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
    it('should fetch all published posts', async () => {
      const mockResponse = [mockPost];

      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const result = await getPosts();

      expect(result).toEqual(mockResponse);
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/posts'),
        expect.any(Object)
      );
    });

    it('should handle pagination', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [mockPost],
      });

      await getPosts({ page: 2, limit: 10 });

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('page=2'),
        expect.any(Object)
      );
    });

    it('should filter by status', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [mockPost],
      });

      await getPosts({ status: 'published' });

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('published'),
        expect.any(Object)
      );
    });

    it('should handle API errors gracefully', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 500,
      });

      await expect(getPosts()).rejects.toThrow();
    });

    it('should handle network errors', async () => {
      (fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      await expect(getPosts()).rejects.toThrow('Network error');
    });

    it('should cache results by default', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [mockPost],
      });

      await getPosts();
      await getPosts();

      // Should only call fetch once if caching is enabled
      expect(fetch).toHaveBeenCalledTimes(1);
    });

    it('should support cache invalidation', async () => {
      (fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: async () => [mockPost],
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => [{ ...mockPost, title: 'Updated' }],
        });

      await getPosts();
      await getPosts({ cache: false });

      expect(fetch).toHaveBeenCalledTimes(2);
    });
  });

  describe('getPost', () => {
    it('should fetch a single post by slug', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const result = await getPost('test-post');

      expect(result).toEqual(mockPost);
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('test-post'),
        expect.any(Object)
      );
    });

    it('should handle post not found', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 404,
      });

      await expect(getPost('nonexistent')).rejects.toThrow();
    });

    it('should include post content', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const result = await getPost('test-post');

      expect(result.content).toBeDefined();
      expect(result.content.length > 0).toBe(true);
    });

    it('should include SEO metadata', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const result = await getPost('test-post');

      expect(result.seo_title).toBeDefined();
      expect(result.seo_description).toBeDefined();
      expect(result.seo_keywords).toBeDefined();
    });
  });

  describe('getPostsByCategory', () => {
    it('should fetch posts by category ID', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [mockPost],
      });

      const result = await getPostsByCategory('tech');

      expect(result).toEqual([mockPost]);
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('tech'),
        expect.any(Object)
      );
    });

    it('should handle empty category', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });

      const result = await getPostsByCategory('empty-category');

      expect(result).toEqual([]);
    });
  });

  describe('getPostsByTag', () => {
    it('should fetch posts by tag', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [mockPost],
      });

      const result = await getPostsByTag('javascript');

      expect(result).toEqual([mockPost]);
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('javascript'),
        expect.any(Object)
      );
    });
  });

  describe('getPostsByAuthor', () => {
    it('should fetch posts by author ID', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [mockPost],
      });

      const result = await getPostsByAuthor('author-1');

      expect(result).toEqual([mockPost]);
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('author-1'),
        expect.any(Object)
      );
    });
  });

  describe('Data Normalization', () => {
    it('should normalize dates from API response', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const result = await getPost('test-post');

      expect(result.created_at).toBeDefined();
      expect(result.updated_at).toBeDefined();
    });

    it('should handle missing optional fields', async () => {
      const minimalPost = {
        id: 'post-1',
        title: 'Minimal Post',
        slug: 'minimal-post',
        content: 'Content',
        status: 'published',
        created_at: '2024-01-15T10:00:00Z',
        updated_at: '2024-01-15T10:00:00Z',
        view_count: 0,
      };

      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => minimalPost,
      });

      const result = await getPost('minimal-post');

      expect(result.title).toBeDefined();
      expect(result.featured_image_url).toBeUndefined();
    });
  });

  describe('API Error Handling', () => {
    it('should log API errors', async () => {
      const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
      });

      try {
        await getPosts();
      } catch (_err) {
        // Expected to throw
      }

      expect(consoleSpy).toHaveBeenCalled();
      consoleSpy.mockRestore();
    });

    it('should handle malformed JSON responses', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => {
          throw new Error('Invalid JSON');
        },
      });

      await expect(getPosts()).rejects.toThrow();
    });
  });
});
