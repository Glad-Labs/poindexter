import { getPaginatedPosts, getPostBySlug } from '../api-fastapi';

// Mock fetch globally
global.fetch = jest.fn();

describe('FastAPI Client (lib/api-fastapi.js)', () => {
  beforeEach(() => {
    fetch.mockClear();
  });

  const mockPosts = [
    {
      id: '1',
      title: 'First Post',
      slug: 'first-post',
      excerpt: 'First post excerpt',
      published_at: '2024-01-15',
      cover_image_url: '/image1.jpg',
    },
    {
      id: '2',
      title: 'Second Post',
      slug: 'second-post',
      excerpt: 'Second post excerpt',
      published_at: '2024-01-14',
      cover_image_url: '/image2.jpg',
    },
  ];

  describe('getPaginatedPosts()', () => {
    test('calls API with correct endpoint', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: mockPosts, total: 2 }),
      });

      await getPaginatedPosts(1, 10);

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/posts'),
        expect.any(Object)
      );
    });

    test('returns paginated posts in { data, meta } shape', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: mockPosts, total: 2 }),
      });

      const result = await getPaginatedPosts(1, 10);

      expect(result.data).toBeDefined();
      expect(Array.isArray(result.data)).toBe(true);
      expect(result.meta.pagination).toBeDefined();
    });

    test('calculates offset from page parameter', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: mockPosts, total: 2 }),
      });

      await getPaginatedPosts(2, 10);

      // page 2 with pageSize 10 -> offset=10
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('offset=10'),
        expect.any(Object)
      );
    });

    test('passes limit parameter correctly', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: mockPosts, total: 2 }),
      });

      await getPaginatedPosts(1, 5);

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('limit=5'),
        expect.any(Object)
      );
    });

    test('throws on API error', async () => {
      fetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
      });

      await expect(getPaginatedPosts(1, 10)).rejects.toThrow('API Error');
    });

    test('throws on network error', async () => {
      fetch.mockRejectedValueOnce(new Error('Network error'));

      await expect(getPaginatedPosts(1, 10)).rejects.toThrow('Network error');
    });
  });

  describe('getPostBySlug()', () => {
    test('calls API with correct slug', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ data: mockPosts[0] }),
      });

      await getPostBySlug('first-post');

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('first-post'),
        expect.any(Object)
      );
    });

    test('returns single post data with normalized fields', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ data: mockPosts[0] }),
      });

      const result = await getPostBySlug('first-post');

      expect(result.title).toBe('First Post');
      expect(result.slug).toBe('first-post');
      // Normalized fields
      expect(result).toHaveProperty('category');
      expect(result).toHaveProperty('tags');
    });

    test('returns null on 404', async () => {
      fetch.mockResolvedValueOnce({
        ok: false,
        status: 404,
        statusText: 'Not Found',
      });

      const result = await getPostBySlug('non-existent');

      expect(result).toBeNull();
    });

    test('includes post content if available', async () => {
      const postWithContent = {
        ...mockPosts[0],
        content: 'Full post content here',
      };

      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ data: postWithContent }),
      });

      const result = await getPostBySlug('first-post');

      expect(result.content).toBeDefined();
    });
  });

  describe('API Request Format', () => {
    test('includes proper headers in requests', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: mockPosts, total: 2 }),
      });

      await getPaginatedPosts(1, 10);

      expect(fetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
        })
      );
    });

    test('includes credentials in requests', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ posts: mockPosts, total: 2 }),
      });

      await getPaginatedPosts(1, 10);

      expect(fetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          credentials: 'include',
        })
      );
    });
  });
});
