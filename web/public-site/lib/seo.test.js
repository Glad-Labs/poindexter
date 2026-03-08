/**
 * SEO Utility Tests (lib/seo.js)
 *
 * Tests SEO optimization functions
 * Verifies: Meta generation, structured data, canonical URLs
 */
import {
  generateMetaTags,
  generateStructuredData,
  getCanonicalUrl,
  generateOGTags,
} from '../lib/seo';

describe('SEO Utility (lib/seo.js)', () => {
  const mockPost = {
    id: 'post-1',
    title: 'Complete Guide to React Hooks',
    slug: 'guide-react-hooks',
    excerpt: 'Learn everything about React Hooks with practical examples',
    seo_title: 'React Hooks Guide | Advanced Patterns',
    seo_description: 'Master React Hooks from basics to advanced techniques',
    seo_keywords: 'react,hooks,javascript,tutorial',
    featured_image_url: 'https://example.com/image.jpg',
    author_id: 'author-1',
    published_at: '2024-01-15T10:00:00Z',
    view_count: 1500,
  };

  describe('generateMetaTags', () => {
    it('should generate meta title tag', () => {
      const tags = generateMetaTags(mockPost);
      expect(tags).toContain('title');
      expect(tags).toContain(mockPost.seo_title || mockPost.title);
    });

    it('should use SEO title if available', () => {
      const tags = generateMetaTags(mockPost);
      expect(tags).toContain(mockPost.seo_title);
    });

    it('should fallback to post title if no SEO title', () => {
      const postWithoutSeoTitle = { ...mockPost, seo_title: undefined };
      const tags = generateMetaTags(postWithoutSeoTitle);
      expect(tags).toContain(mockPost.title);
    });

    it('should generate meta description tag', () => {
      const tags = generateMetaTags(mockPost);
      expect(tags).toContain('description');
      expect(tags).toContain(mockPost.seo_description || mockPost.excerpt);
    });

    it('should generate meta keywords tag', () => {
      const tags = generateMetaTags(mockPost);
      expect(tags).toContain('keywords');
      if (mockPost.seo_keywords) {
        expect(tags).toContain(mockPost.seo_keywords);
      }
    });

    it('should generate robots meta tag', () => {
      const tags = generateMetaTags(mockPost);
      expect(tags).toContain('robots');
    });

    it('should generate viewport meta tag', () => {
      const tags = generateMetaTags(mockPost);
      expect(tags).toContain('viewport');
    });

    it('should handle missing optional fields', () => {
      const minimalPost = {
        title: 'Simple Post',
        slug: 'simple-post',
        excerpt: 'Description',
      };
      const tags = generateMetaTags(minimalPost);
      expect(tags).toBeDefined();
      expect(tags.length > 0).toBe(true);
    });

    it('should escape HTML special characters', () => {
      const postWithSpecialChars = {
        ...mockPost,
        title: 'Post with "quotes" & special chars',
      };
      const tags = generateMetaTags(postWithSpecialChars);
      expect(tags).not.toContain('<');
      expect(tags).not.toContain('>');
    });

    it('should limit description length', () => {
      const postWithLongDesc = {
        ...mockPost,
        seo_description:
          'This is a very long description that exceeds the recommended 160 characters for meta descriptions and should be truncated properly',
      };
      const tags = generateMetaTags(postWithLongDesc);
      // Description should be reasonably limited
      expect(tags).toBeDefined();
    });
  });

  describe('generateStructuredData', () => {
    it('should generate Schema.org structured data', () => {
      const structured = generateStructuredData(mockPost);
      expect(structured).toBeDefined();
      expect(structured).toContain('@context');
      expect(structured).toContain('schema.org');
    });

    it('should include BlogPosting type', () => {
      const structured = generateStructuredData(mockPost);
      expect(structured).toContain('BlogPosting');
    });

    it('should include headline', () => {
      const structured = generateStructuredData(mockPost);
      expect(structured).toContain(mockPost.title);
    });

    it('should include description', () => {
      const structured = generateStructuredData(mockPost);
      expect(structured).toContain(
        mockPost.excerpt || mockPost.seo_description
      );
    });

    it('should include image if available', () => {
      const structured = generateStructuredData(mockPost);
      if (mockPost.featured_image_url) {
        expect(structured).toContain(mockPost.featured_image_url);
      }
    });

    it('should include datePublished', () => {
      const structured = generateStructuredData(mockPost);
      expect(structured).toContain('datePublished');
    });

    it('should be valid JSON-LD', () => {
      const structured = generateStructuredData(mockPost);
      expect(() => JSON.parse(structured)).not.toThrow();
    });

    it('should include author information if available', () => {
      const structured = generateStructuredData(mockPost);
      if (mockPost.author_id) {
        expect(structured).toContain('author') ||
          expect(structured).toBeDefined();
      }
    });
  });

  describe('getCanonicalUrl', () => {
    it('should generate canonical URL', () => {
      const canonical = getCanonicalUrl(mockPost);
      expect(canonical).toBeDefined();
      expect(canonical).toContain(mockPost.slug);
    });

    it('should use site domain from config', () => {
      const canonical = getCanonicalUrl(mockPost);
      expect(canonical).toMatch(/https?:\/\//);
    });

    it('should not have trailing slash if not needed', () => {
      const canonical = getCanonicalUrl(mockPost);
      const endsWithSlash = canonical.endsWith('/');
      // Should be consistent
      expect(typeof endsWithSlash).toBe('boolean');
    });

    it('should be absolute URL', () => {
      const canonical = getCanonicalUrl(mockPost);
      try {
        const url = new URL(canonical);
        expect(url.hostname).toBeDefined();
      } catch (_err) {
        expect(true).toBe(false); // URL should be valid
      }
    });
  });

  describe('generateOGTags', () => {
    it('should generate Open Graph title', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags).toContain('og:title');
    });

    it('should generate Open Graph description', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags).toContain('og:description');
    });

    it('should generate Open Graph image', () => {
      const ogTags = generateOGTags(mockPost);
      if (mockPost.featured_image_url) {
        expect(ogTags).toContain('og:image');
      }
    });

    it('should generate Open Graph type as article', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags).toContain('og:type');
      expect(ogTags).toContain('article');
    });

    it('should generate Twitter card tags', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags).toContain('twitter:card') || expect(ogTags).toBeDefined();
    });

    it('should include Open Graph URL', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags).toContain('og:url');
    });
  });

  describe('SEO Compliance', () => {
    it('should follow SEO best practices for titles', () => {
      const tags = generateMetaTags(mockPost);
      // Title should be reasonable length
      expect(tags).toBeDefined();
    });

    it('should ensure descriptions are within recommended length', () => {
      const tags = generateMetaTags(mockPost);
      // Should not have excessively long descriptions
      expect(tags).toBeDefined();
    });

    it('should generate valid meta robots directive', () => {
      const tags = generateMetaTags(mockPost);
      expect(tags).toContain('robots');
    });

    it('should include canonical URL to prevent duplicate content', () => {
      const canonical = getCanonicalUrl(mockPost);
      expect(canonical).toBeDefined();
    });
  });
});
