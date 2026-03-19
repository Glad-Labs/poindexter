import { getAbsoluteURL, getAPIBaseURL } from '../url';

describe('URL Utilities (lib/url.js)', () => {
  // Store original env variables
  const originalEnv = process.env;

  beforeEach(() => {
    jest.resetModules();
    process.env = { ...originalEnv };
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  describe('getAPIBaseURL()', () => {
    test('returns FastAPI base URL', () => {
      const baseURL = getAPIBaseURL();

      expect(baseURL).toBeDefined();
      expect(typeof baseURL).toBe('string');
    });

    test('returns proper URL format', () => {
      const baseURL = getAPIBaseURL();

      expect(baseURL).toMatch(/^https?:\/\//);
    });

    test('includes port 8000 for local development', () => {
      const baseURL = getAPIBaseURL();

      // Should point to localhost:8000 or similar
      expect(baseURL).toMatch(/:8000|:8080/);
    });

    test('does not have trailing slash', () => {
      const baseURL = getAPIBaseURL();

      expect(baseURL).not.toMatch(/\/$/);
    });
  });

  describe('getAbsoluteURL()', () => {
    test('constructs absolute URL from path', () => {
      const path = '/api/posts';
      const absoluteURL = getAbsoluteURL(path);

      expect(absoluteURL).toBeDefined();
      expect(absoluteURL).toMatch(/^https?:\/\//);
    });

    test('includes the path in the result', () => {
      const path = '/api/posts';
      const absoluteURL = getAbsoluteURL(path);

      expect(absoluteURL).toContain(path);
    });

    test('works with root path', () => {
      const absoluteURL = getAbsoluteURL('/');

      expect(absoluteURL).toMatch(/^https?:\/\//);
    });

    test('handles paths with query parameters', () => {
      const path = '/api/posts?page=1&limit=10';
      const absoluteURL = getAbsoluteURL(path);

      expect(absoluteURL).toContain('page=1');
      expect(absoluteURL).toContain('limit=10');
    });

    test('handles paths with fragments', () => {
      const path = '/content#section1';
      const absoluteURL = getAbsoluteURL(path);

      expect(absoluteURL).toContain('section1');
    });

    test('preserves path as-is (no double-slash normalization)', () => {
      const path = '//api/posts';
      const absoluteURL = getAbsoluteURL(path);

      // getAbsoluteURL concatenates base + path without normalization
      expect(absoluteURL).toContain('//api/posts');
    });

    test('handles empty string path', () => {
      const absoluteURL = getAbsoluteURL('');

      expect(absoluteURL).toBeDefined();
      expect(absoluteURL).toMatch(/^https?:\/\//);
    });
  });

  describe('URL Construction Consistency', () => {
    test('getAbsoluteURL produces consistent results', () => {
      const path = '/api/posts/123';
      const url1 = getAbsoluteURL(path);
      const url2 = getAbsoluteURL(path);

      expect(url1).toBe(url2);
    });

    test('getAPIBaseURL produces consistent results', () => {
      const url1 = getAPIBaseURL();
      const url2 = getAPIBaseURL();

      expect(url1).toBe(url2);
    });

    test('getAbsoluteURL uses getAPIBaseURL internally', () => {
      const path = '/api/posts';
      const absoluteURL = getAbsoluteURL(path);
      const baseURL = getAPIBaseURL();

      expect(absoluteURL).toContain(baseURL.replace(/^https?:\/\//, ''));
    });
  });

  describe('URL Encoding', () => {
    test('handles special characters in path', () => {
      const path = '/api/search?q=hello world';
      const absoluteURL = getAbsoluteURL(path);

      expect(absoluteURL).toContain('/api/search');
    });

    test('preserves URL encoding in paths', () => {
      const path = '/api/posts/my-post-title';
      const absoluteURL = getAbsoluteURL(path);

      expect(absoluteURL).toContain('my-post-title');
    });
  });
});
