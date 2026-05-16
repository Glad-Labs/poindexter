/**
 * Posts API Tests (lib/posts.ts)
 *
 * Tests the static JSON integration for fetching posts from R2/CDN.
 * Verifies: fetch calls, error handling, pagination, data transformation
 */
import {
  getPosts,
  getPostBySlug,
  getPostsByCategory,
  postFeaturedImage,
} from '../lib/posts';

// Mock logger to avoid noise
jest.mock('./logger', () => ({
  __esModule: true,
  default: {
    error: jest.fn(),
    warn: jest.fn(),
    info: jest.fn(),
    log: jest.fn(),
  },
}));

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
        json: async () => ({
          posts: [mockPost],
          total: 1,
          exported_at: '2024-01-15T10:00:00Z',
        }),
      });

      const result = await getPosts();

      expect(result.posts).toEqual([mockPost]);
      expect(result.total).toBe(1);
      expect(result.page).toBe(1);
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/posts/index.json'),
        expect.any(Object)
      );
    });

    it('should handle pagination with page parameter', async () => {
      // Create 15 posts so page 2 returns the remainder
      const manyPosts = Array.from({ length: 15 }, (_, i) => ({
        ...mockPost,
        id: `post-${i}`,
        slug: `test-post-${i}`,
      }));

      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          posts: manyPosts,
          total: 15,
          exported_at: '2024-01-15T10:00:00Z',
        }),
      });

      const result = await getPosts(2);

      // Page 2 with 10 per page should return posts 10-14 (5 posts)
      expect(result.posts).toHaveLength(5);
      expect(result.page).toBe(2);
    });

    it('should fetch from static index.json (not API with status param)', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          posts: [mockPost],
          total: 1,
          exported_at: '2024-01-15T10:00:00Z',
        }),
      });

      await getPosts();

      const calledUrl = (fetch as jest.Mock).mock.calls[0][0] as string;
      expect(calledUrl).toContain('/posts/index.json');
    });

    it('should return empty result on API error', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
      });

      const result = await getPosts();

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
      const manyPosts = Array.from({ length: 25 }, (_, i) => ({
        ...mockPost,
        id: `post-${i}`,
        slug: `test-post-${i}`,
      }));

      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          posts: manyPosts,
          total: 25,
          exported_at: '2024-01-15T10:00:00Z',
        }),
      });

      const result = await getPosts();

      expect(result.totalPages).toBe(3); // 25 posts / 10 per page = 3 pages
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
        expect.stringContaining('/posts/test-post.json'),
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

  describe('postFeaturedImage', () => {
    // Pins the canonical fallback chain. Every list page and the detail
    // page route through this — drift here is the bug the user reported
    // 2026-05-16 (paginated thumbnail and detail-page hero differing).
    it('prefers featured_image_url when both are set', () => {
      expect(
        postFeaturedImage({
          featured_image_url: 'https://cdn/featured.jpg',
          cover_image_url: 'https://cdn/cover.jpg',
        }),
      ).toBe('https://cdn/featured.jpg');
    });

    it('falls back to cover_image_url when featured is missing', () => {
      expect(
        postFeaturedImage({
          featured_image_url: undefined,
          cover_image_url: 'https://cdn/cover.jpg',
        }),
      ).toBe('https://cdn/cover.jpg');
    });

    it('falls back to cover_image_url when featured is empty string', () => {
      expect(
        postFeaturedImage({
          featured_image_url: '',
          cover_image_url: 'https://cdn/cover.jpg',
        }),
      ).toBe('https://cdn/cover.jpg');
    });

    it('returns null when both columns are missing', () => {
      expect(
        postFeaturedImage({
          featured_image_url: undefined,
          cover_image_url: undefined,
        }),
      ).toBeNull();
    });
  });

  describe('getPostsByCategory', () => {
    it('should filter posts by category from index', async () => {
      const posts = [
        { ...mockPost, id: 'p1', category_id: 'tech' },
        { ...mockPost, id: 'p2', category_id: 'health' },
        { ...mockPost, id: 'p3', category_id: 'tech' },
      ];

      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          posts,
          total: 3,
          exported_at: '2024-01-15T10:00:00Z',
        }),
      });

      const result = await getPostsByCategory('tech');

      expect(result).toBeDefined();
      expect(result.posts).toHaveLength(2);
      expect(result.total).toBe(2);
    });

    it('should return empty result for empty category', async () => {
      (fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          posts: [mockPost],
          total: 1,
          exported_at: '2024-01-15T10:00:00Z',
        }),
      });

      const result = await getPostsByCategory('empty-category');

      expect(result.posts).toEqual([]);
    });
  });
});
