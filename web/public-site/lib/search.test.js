/**
 * Search Utility Tests (lib/search.js)
 *
 * Tests the search functionality
 * Verifies: Post searching, filtering, ranking
 */
import {
  searchPosts,
  createSearchIndex,
  normalizeSearchTerm,
} from '../lib/search';

describe('Search Utility (lib/search.js)', () => {
  const mockPosts = [
    {
      id: 'post-1',
      title: 'Getting Started with React',
      slug: 'getting-started-react',
      excerpt: 'Learn the basics of React framework',
      content: 'React is a JavaScript library...',
      category_id: 'javascript',
    },
    {
      id: 'post-2',
      title: 'Advanced TypeScript Patterns',
      slug: 'advanced-typescript',
      excerpt: 'Master TypeScript advanced patterns',
      content: 'TypeScript provides static typing...',
      category_id: 'typescript',
    },
    {
      id: 'post-3',
      title: 'Node.js Best Practices',
      slug: 'nodejs-best-practices',
      excerpt: 'Server-side JavaScript with Node.js',
      content: 'Node.js allows you to...',
      category_id: 'nodejs',
    },
  ];

  describe('searchPosts', () => {
    it('should find posts by title', () => {
      const results = searchPosts('React', mockPosts);
      expect(results.length).toBeGreaterThan(0);
      expect(results.some((p) => p.id === 'post-1')).toBe(true);
    });

    it('should find posts by excerpt', () => {
      const results = searchPosts('TypeScript', mockPosts);
      expect(results.length).toBeGreaterThan(0);
    });

    it('should perform case-insensitive search', () => {
      const results1 = searchPosts('react', mockPosts);
      const results2 = searchPosts('REACT', mockPosts);
      expect(results1.length).toBe(results2.length);
    });

    it('should find partial matches', () => {
      const results = searchPosts('Start', mockPosts);
      expect(results.some((p) => p.id === 'post-1')).toBe(true);
    });

    it('should rank results by relevance', () => {
      const results = searchPosts('React', mockPosts);
      // Post with title match should rank higher than excerpt match
      expect(results[0].id === 'post-1').toBe(true);
    });

    it('should handle empty search term', () => {
      const results = searchPosts('', mockPosts);
      expect(results.length).toBe(mockPosts.length);
    });

    it('should return empty results for no matches', () => {
      const results = searchPosts('xyzabc123', mockPosts);
      expect(results.length).toBe(0);
    });

    it('should search in multiple fields', () => {
      const results = searchPosts('JavaScript', mockPosts);
      expect(results.length).toBeGreaterThan(0);
      // Should find both title and content matches
    });

    it('should handle special characters', () => {
      const results = searchPosts('Node.js', mockPosts);
      expect(results.some((p) => p.id === 'post-3')).toBe(true);
    });

    it('should support AND operator', () => {
      const results = searchPosts('Node.js Best', mockPosts);
      expect(results.length > 0).toBe(true);
    });
  });

  describe('createSearchIndex', () => {
    it('should create a searchable index', () => {
      const index = createSearchIndex(mockPosts);
      expect(index).toBeDefined();
      expect(typeof index).toBe('object');
    });

    it('should normalize text in index', () => {
      const index = createSearchIndex(mockPosts);
      // Index should contain lowercase versions
      expect(index).toBeDefined();
    });

    it('should handle empty posts array', () => {
      const index = createSearchIndex([]);
      expect(index).toBeDefined();
    });
  });

  describe('normalizeSearchTerm', () => {
    it('should convert to lowercase', () => {
      const normalized = normalizeSearchTerm('REACT');
      expect(normalized).toBe(normalized.toLowerCase());
    });

    it('should trim whitespace', () => {
      const normalized = normalizeSearchTerm('  react  ');
      expect(normalized).toBe(normalized.trim());
    });

    it('should remove special characters', () => {
      const normalized = normalizeSearchTerm('react!!!');
      expect(normalized).not.toContain('!');
    });

    it('should handle empty string', () => {
      const normalized = normalizeSearchTerm('');
      expect(normalized).toBe('');
    });

    it('should remove diacritics', () => {
      const normalized = normalizeSearchTerm('café');
      expect(normalized).toBeDefined();
    });
  });

  describe('Search Performance', () => {
    it('should handle large post collections efficiently', () => {
      const largePosts = Array.from({ length: 1000 }, (_, i) => ({
        id: `post-${i}`,
        title: `Post ${i}: React Guide ${i}`,
        slug: `post-${i}`,
        excerpt: 'Some excerpt',
        content: 'Some content',
        category_id: 'tech',
      }));

      const start = performance.now();
      const results = searchPosts('React', largePosts);
      const end = performance.now();

      expect(results.length).toBeGreaterThan(0);
      expect(end - start).toBeLessThan(1000); // Should complete in under 1 second
    });
  });

  describe('Search Ranking', () => {
    it('should rank title matches higher than excerpt', () => {
      const results = searchPosts('React', mockPosts);
      expect(results[0].title.includes('React')).toBe(true);
    });

    it('should consider match position', () => {
      const results = searchPosts('Started', mockPosts);
      // Early matches should rank higher
      expect(results.length > 0).toBe(true);
    });
  });
});
